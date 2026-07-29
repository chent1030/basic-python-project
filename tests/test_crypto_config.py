"""Tests for crypto + datasource segment config + auto-decrypt on load.

Covers:
- AES-GCM encrypt/decrypt round-trip
- enc: prefix detection (maybe_decrypt passes through non-encrypted values)
- genkey produces 32-byte keys
- DatasourceConfig builds DSN from host/port/user/password parts
- URL-encoding of special characters in password
- DSN field takes precedence over segments
- is_configured() detects empty vs filled datasources
- End-to-end: encrypt password in raw config dict → load → plaintext DSN
"""
from __future__ import annotations

import pytest

from app.core.config import DatasourceConfig, Settings, _decrypt_tree
from app.core.crypto import ENC_PREFIX, decrypt, encrypt, genkey, maybe_decrypt


# ----------------------------------------------------------- crypto core
def test_genkey_returns_32_bytes_base64():
    import base64

    key = genkey()
    raw = base64.b64decode(key)
    assert len(raw) == 32  # AES-256


def test_encrypt_decrypt_round_trip():
    key = genkey()
    plaintext = "my-db-password-2026!"
    ct = encrypt(plaintext, key)
    assert ct.startswith(ENC_PREFIX)
    assert decrypt(ct, key) == plaintext


def test_same_plaintext_different_ciphertext():
    """AES-GCM uses random nonce → same input yields different output."""
    key = genkey()
    a = encrypt("secret", key)
    b = encrypt("secret", key)
    assert a != b
    assert decrypt(a, key) == decrypt(b, key) == "secret"


def test_maybe_decrypt_passes_through_plaintext():
    key = genkey()
    assert maybe_decrypt("plain-value", key) == "plain-value"
    assert maybe_decrypt("", key) == ""
    assert maybe_decrypt(123, key) == 123
    assert maybe_decrypt(None, key) is None
    assert maybe_decrypt({"k": "v"}, key) == {"k": "v"}


def test_maybe_decrypt_returns_value_when_no_key():
    """No key configured → never attempt decryption, return as-is."""
    assert maybe_decrypt("enc:something", None) == "enc:something"


def test_decrypt_rejects_wrong_key():
    from cryptography.exceptions import InvalidTag

    key1 = genkey()
    key2 = genkey()
    ct = encrypt("secret", key1)
    with pytest.raises(InvalidTag):
        decrypt(ct, key2)


def test_invalid_key_length_raises():
    import base64

    bad_key = base64.b64encode(b"too-short").decode()
    with pytest.raises(ValueError, match="32 字节"):
        encrypt("x", bad_key)


# ----------------------------------------------------------- DSN building
def test_build_dsn_postgres_from_parts():
    cfg = DatasourceConfig(
        type="postgresql",
        host="127.0.0.1", port=5432,
        username="postgres", password="secret", database="app",
    )
    assert cfg.build_dsn() == "postgresql+asyncpg://postgres:secret@127.0.0.1:5432/app"


def test_build_dsn_mysql_from_parts():
    cfg = DatasourceConfig(
        type="mysql", host="db", port=3306,
        username="root", password="pass", database="biz",
    )
    assert cfg.build_dsn() == "mysql+aiomysql://root:pass@db:3306/biz?charset=utf8mb4"


def test_build_dsn_redis_from_parts():
    cfg = DatasourceConfig(type="redis", host="cache", port=6379, database="2")
    assert cfg.build_dsn() == "redis://cache:6379/2"


def test_build_dsn_url_encodes_special_chars_in_password():
    """密码里的 @ / : 等特殊字符必须 URL encode,否则 DSN 解析错乱。"""
    cfg = DatasourceConfig(
        type="postgresql", host="h", port=5432,
        username="u", password="p@ss/w0:rd", database="d",
    )
    dsn = cfg.build_dsn()
    # 特殊字符被编码,@ 不在密码段里出现裸的
    assert "p%40ss%2Fw0%3Ard" in dsn
    # 主机部分不受影响
    assert "h:5432/d" in dsn


def test_dsn_field_takes_precedence_over_parts():
    cfg = DatasourceConfig(
        type="postgresql",
        dsn="postgresql+asyncpg://existing:5432/db",
        host="ignored", port=9999,
    )
    # dsn 已填 → 不用分段字段
    assert cfg.build_dsn() == "postgresql+asyncpg://existing:5432/db"


def test_is_configured_detects_empty():
    assert DatasourceConfig(type="postgresql").is_configured() is False
    assert DatasourceConfig(type="postgresql", dsn="x").is_configured() is True
    assert DatasourceConfig(type="postgresql", host="h").is_configured() is True


def test_model_validator_fills_dsn_from_parts():
    """构造后 dsn 字段应被自动填充(如果分段已填、dsn 为空)。"""
    cfg = DatasourceConfig(
        type="postgresql", host="h", port=5432,
        username="u", password="p", database="d",
    )
    assert cfg.dsn == "postgresql+asyncpg://u:p@h:5432/d"


# ----------------------------------------------------------- decrypt tree
def test_decrypt_tree_recursive():
    """_decrypt_tree 应递归解密 dict/list 里的所有 enc: 字段。"""
    key = genkey()
    enc_pwd = encrypt("db-secret", key)
    enc_key = encrypt("sk-xxx", key)

    tree = {
        "crypto": {"key": key},
        "datasources": {
            "pg": {"password": enc_pwd, "host": "127.0.0.1"},
        },
        "llm": {"api_key": enc_key, "model": "gpt-4"},
        "plain_list": [enc_pwd, "normal", 123],
    }
    result = _decrypt_tree(tree, key)
    assert result["datasources"]["pg"]["password"] == "db-secret"
    assert result["datasources"]["pg"]["host"] == "127.0.0.1"  # 不动
    assert result["llm"]["api_key"] == "sk-xxx"
    assert result["llm"]["model"] == "gpt-4"
    assert result["plain_list"][0] == "db-secret"
    assert result["plain_list"][1] == "normal"


# ----------------------------------------------------------- end-to-end load
def test_settings_loads_with_encrypted_password():
    """端到端:模拟一个含 enc: 密码的原始配置 dict → Settings → 明文 DSN。"""
    key = genkey()
    enc_pwd = encrypt("super-secret", key)

    raw = {
        "crypto": {"key": key},
        "datasources": {
            "pg_primary": {
                "type": "postgresql",
                "host": "10.0.0.1",
                "port": 5432,
                "username": "admin",
                "password": enc_pwd,  # 加密的
                "database": "prod_db",
            },
        },
    }
    # 复刻 load_settings 的解密 + 校验流程
    decrypted = _decrypt_tree(raw, raw["crypto"]["key"])
    settings = Settings.model_validate(decrypted)

    cfg = settings.datasources["pg_primary"]
    # password 已被解密成明文
    assert cfg.password == "super-secret"
    # DSN 用明文拼出来
    assert cfg.dsn == "postgresql+asyncpg://admin:super-secret@10.0.0.1:5432/prod_db"


def test_settings_loads_with_plaintext_password():
    """没有 crypto.key 时,密码保持明文也能用(开发环境)。"""
    raw = {
        "datasources": {
            "pg": {
                "type": "postgresql",
                "host": "localhost", "port": 5432,
                "username": "u", "password": "plain-pwd", "database": "d",
            },
        },
    }
    # 没有 crypto.key,_decrypt_tree 不执行(在 load_settings 里判断)
    settings = Settings.model_validate(raw)
    cfg = settings.datasources["pg"]
    assert cfg.password == "plain-pwd"
    assert "plain-pwd" in cfg.dsn


def test_settings_with_no_datasources():
    """完全不配数据源也能正常构造 Settings(空 dict)。"""
    settings = Settings()
    assert settings.datasources == {}
    assert settings.crypto.key == ""
