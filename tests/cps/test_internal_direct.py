"""内网直连身份（internal direct）单测。

口径（用户要求：只填地址，不再有 token/白名单）：
- Java 后端只需配置 Python 侧地址即可调用 ``/api/v1/agent*``；
- 不再有共享密钥（env ``CPS_INTERNAL_API_KEY`` / yaml ``internal_api_key``）、
  IP 白名单（``trusted_networks``）或 ``internal_trust`` 开关；
- 无 ``Authorization`` 头 → 签发内网直连 Principal（``actor=legacy-cps``，
  仅 CPS 业务角色，审计来源 ``internal-direct``）；
- 携带 Bearer 但按 JWT 解析失败 → 401，**不**回退成内网直连。

本文件替代原 ``test_internal_shared_key.py``（共享密钥通道已整体删除）。
"""

from __future__ import annotations

import time

import pytest
import yaml
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from app.api.v1.endpoints.agent_runs import INTERNAL_DIRECT_ROLES, principal
from app.projects.cps.infrastructure.config import CPSConfig


def _make_request(client_host: str = "203.0.113.9") -> Request:
    return Request(
        {
            "type": "http",
            "client": (client_host, 51234),
            "headers": [],
            "method": "GET",
            "path": "/",
        }
    )


def _write_config(tmp_path, **extra) -> str:
    config: dict[str, object] = {"enabled": True}
    config.update(extra)
    path = tmp_path / "cps.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return str(path)


def test_no_token_gets_internal_direct_principal(tmp_path, monkeypatch):
    """无 Authorization 头（公网来源 IP）→ 内网直连身份，仅 CPS 业务角色。"""
    monkeypatch.setenv("CPS_CONFIG", _write_config(tmp_path))
    result = principal(_make_request("203.0.113.9"), None)
    assert result.tenant == "local-factory"
    assert result.actor == "legacy-cps"
    assert result.source == "internal-direct"
    assert set(result.roles) == set(INTERNAL_DIRECT_ROLES)
    # 框架角色（run_reader/run_operator/approver）不因内网直连获得。
    assert "run_operator" not in result.roles
    assert result.expires > time.time()


def test_no_token_identity_is_network_independent(tmp_path, monkeypatch):
    """不再有 IP 白名单：任意来源 IP 的无凭证请求得到同一内网直连身份。"""
    monkeypatch.setenv("CPS_CONFIG", _write_config(tmp_path))
    loopback = principal(_make_request("127.0.0.1"), None)
    public = principal(_make_request("203.0.113.9"), None)
    assert (loopback.actor, loopback.roles, loopback.source) == (
        public.actor,
        public.roles,
        public.source,
    )


def test_garbage_bearer_rejected(tmp_path, monkeypatch):
    """携带无法解析的 Bearer → 401（不降级成内网直连）。"""
    monkeypatch.setenv("CPS_CONFIG", _write_config(tmp_path))
    with pytest.raises(HTTPException) as excinfo:
        principal(
            _make_request(),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt"),
        )
    assert excinfo.value.status_code == 401


def test_legacy_shared_key_env_is_not_honoured(tmp_path, monkeypatch):
    """旧共享密钥通道已删除：即使 env CPS_INTERNAL_API_KEY 仍存在，Bearer <key> 也 401。"""
    monkeypatch.setenv("CPS_CONFIG", _write_config(tmp_path))
    monkeypatch.setenv("CPS_INTERNAL_API_KEY", "s3cret-key")
    with pytest.raises(HTTPException) as excinfo:
        principal(
            _make_request(),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="s3cret-key"),
        )
    assert excinfo.value.status_code == 401


def test_config_no_longer_declares_auth_fields(tmp_path, monkeypatch):
    """CPSConfig 不再有 internal_trust / internal_api_key / trusted_networks。"""
    monkeypatch.setenv("CPS_CONFIG", _write_config(tmp_path))
    configuration = CPSConfig.load()
    for field in ("internal_trust", "internal_api_key", "trusted_networks"):
        assert field not in CPSConfig.model_fields
        assert not hasattr(configuration, field)


def test_config_rejects_legacy_auth_keys(tmp_path, monkeypatch):
    """旧 yaml 若仍写这三个键 → 严格模式直接拒绝（extra=forbid），避免静默失效。"""
    monkeypatch.delenv("CPS_CONFIG", raising=False)
    path = _write_config(tmp_path, internal_trust=True)
    with open(path, encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    with pytest.raises(ValidationError):
        CPSConfig(**payload)
