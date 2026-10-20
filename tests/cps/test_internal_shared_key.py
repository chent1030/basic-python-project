"""跨网络共享密钥（internal shared key）认证单测。

场景（m01345 生产跨网部署）：
- Java 以 ``Authorization: Bearer <key>`` 直连 Python agent API；
- 密钥来源 env ``CPS_INTERNAL_API_KEY``（优先）或 cps.yaml ``internal_api_key``；
- 恒定时间比较；错密钥/无密钥且 IP 不可信 → 401（不回退 IP 兜底）。
"""

from __future__ import annotations

import yaml
import pytest
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from app.api.v1.endpoints.agent_runs import principal
from app.projects.cps.infrastructure.config import CPSConfig


def _make_request(client_host: str) -> Request:
    return Request(
        {"type": "http", "client": (client_host, 51234), "headers": [], "method": "GET", "path": "/"}
    )


def _write_config(tmp_path, **overrides) -> str:
    config = {
        "enabled": True,
        "internal_trust": False,  # 关闭 IP 信任：只允许密钥路径通过
        "internal_api_key": "",
        "trusted_networks": ["127.0.0.1/32", "::1/128"],
    }
    config.update(overrides)
    path = tmp_path / "cps.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return str(path)


def test_env_shared_key_bearer_gets_legacy_principal(tmp_path, monkeypatch):
    monkeypatch.setenv("CPS_CONFIG", _write_config(tmp_path))
    monkeypatch.setenv("CPS_INTERNAL_API_KEY", "s3cret-key")
    result = principal(
        _make_request("203.0.113.9"),  # 公网 IP，不在 trusted_networks
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="s3cret-key"),
    )
    assert result.actor == "legacy-cps"
    assert "cps_admin" in result.roles


def test_yaml_shared_key_bearer_gets_legacy_principal(tmp_path, monkeypatch):
    monkeypatch.setenv("CPS_CONFIG", _write_config(tmp_path, internal_api_key="yaml-key"))
    monkeypatch.delenv("CPS_INTERNAL_API_KEY", raising=False)
    result = principal(
        _make_request("203.0.113.9"),
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="yaml-key"),
    )
    assert result.actor == "legacy-cps"


def test_wrong_shared_key_rejected_without_ip_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("CPS_CONFIG", _write_config(tmp_path, internal_api_key="yaml-key"))
    monkeypatch.delenv("CPS_INTERNAL_API_KEY", raising=False)
    with pytest.raises(HTTPException) as excinfo:
        principal(
            _make_request("203.0.113.9"),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong-key"),
        )
    assert excinfo.value.status_code == 401


def test_untrusted_ip_without_token_still_rejected(tmp_path, monkeypatch):
    # 回归保护：internal_trust=false 且 IP 不可信 → 无 token 仍 401（密钥不放宽此路径）
    monkeypatch.setenv("CPS_CONFIG", _write_config(tmp_path, internal_api_key="yaml-key"))
    monkeypatch.delenv("CPS_INTERNAL_API_KEY", raising=False)
    with pytest.raises(HTTPException) as excinfo:
        principal(_make_request("203.0.113.9"), None)
    assert excinfo.value.status_code == 401


def test_load_reads_internal_api_key_from_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("CPS_CONFIG", _write_config(tmp_path, internal_api_key="abc"))
    assert CPSConfig.load().internal_api_key == "abc"
