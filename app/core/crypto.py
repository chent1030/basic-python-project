"""密码加解密 —— AES-GCM。

用法:
- 生产环境把敏感字段(数据库密码等)加密后填入 config.yaml,密文以 `enc:` 前缀标识
- 加载配置时调用 maybe_decrypt(value, key) 自动识别:
    - 以 `enc:` 开头 -> 解密返回明文
    - 否则 -> 原样返回(开发环境可直接填明文)
- 密钥写在 config.yaml 的 crypto.key 字段(32 字节,base64 编码)

加密封装:每个密文内嵌随机 nonce + GCM tag,因此同一明文每次加密结果不同。
解密时无需额外参数,密文自包含。

CLI 工具:
    python -m app.core.crypto encrypt "my-password"
    python -m app.core.crypto genkey
"""
from __future__ import annotations

import base64
import os
import sys
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# 密文前缀,加载时据此判断是否需要解密
ENC_PREFIX = "enc:"

# nonce 长度(GCM 推荐 12 字节)
_NONCE_LEN = 12


# --------------------------------------------------------------------------
# 核心加解密
# --------------------------------------------------------------------------
def encrypt(plaintext: str, key: str) -> str:
    """加密明文,返回 `enc:<base64(nonce||ciphertext||tag)>`。

    Args:
        plaintext: 要加密的明文
        key:       base64 编码的 32 字节密钥(来自 config.crypto.key)

    Returns:
        带 enc: 前缀的密文字符串,可直接写入 config.yaml
    """
    aes = _make_aesgcm(key)
    nonce = os.urandom(_NONCE_LEN)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    # nonce + ciphertext + tag 拼一起 base64,解密时再切开
    payload = base64.b64encode(nonce + ct).decode("ascii")
    return f"{ENC_PREFIX}{payload}"


def decrypt(ciphertext: str, key: str) -> str:
    """解密 `enc:<base64>` 格式的密文,返回明文。"""
    if not ciphertext.startswith(ENC_PREFIX):
        # 不是密文,原样返回(允许同一字段混用明文/密文)
        return ciphertext
    payload = ciphertext[len(ENC_PREFIX):]
    raw = base64.b64decode(payload)
    nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    aes = _make_aesgcm(key)
    return aes.decrypt(nonce, ct, None).decode("utf-8")


def maybe_decrypt(value: Any, key: str | None) -> Any:
    """递归解密:字符串且以 enc: 开头才解密,其他类型原样返回。

    用于配置加载阶段:对整个 settings 树扫一遍,自动解密所有 enc: 字段。
    """
    if key is None:
        return value
    if isinstance(value, str) and value.startswith(ENC_PREFIX):
        return decrypt(value, key)
    return value


def _make_aesgcm(key: str) -> AESGCM:
    """从 base64 编码的 key 构造 AESGCM。32 字节 = AES-256。"""
    raw = base64.b64decode(key)
    if len(raw) != 32:
        raise ValueError(
            f"crypto.key 解码后必须是 32 字节(AES-256),当前 {len(raw)} 字节。"
            f"用 `python -m app.core.crypto genkey` 生成。"
        )
    return AESGCM(raw)


def genkey() -> str:
    """生成一个新的 32 字节随机密钥,base64 编码返回。"""
    return base64.b64encode(os.urandom(32)).decode("ascii")


# --------------------------------------------------------------------------
# CLI:python -m app.core.crypto <command>
# --------------------------------------------------------------------------
def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1

    cmd = argv[1]
    if cmd == "genkey":
        print(genkey())
        return 0

    if cmd == "encrypt":
        if len(argv) < 3:
            print("用法: python -m app.core.crypto encrypt <plaintext> [key]")
            return 1
        plaintext = argv[2]
        # key 从 config.yaml 读,或命令行第二个参数
        if len(argv) >= 4:
            key = argv[3]
        else:
            from app.core.config import settings

            key = settings.crypto.key
            if not key:
                print("config.yaml 里没配 crypto.key,无法加密。先运行 genkey 生成并填入。")
                return 1
        print(encrypt(plaintext, key))
        return 0

    print(f"未知命令: {cmd}")
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))


__all__ = ["encrypt", "decrypt", "maybe_decrypt", "genkey", "ENC_PREFIX"]
