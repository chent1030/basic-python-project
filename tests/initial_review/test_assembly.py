"""装配回归（J0 教训）：TestClient 真实装配冒烟。

与 tests/smoke_test.py（mock 数据源、绕 lifespan）相反，本文件验证：
- 真实 create_app + lifespan 启动（不 mock DatasourceManager）；
- postgres_primary（local.yaml）真实可达时，C-01 → 执行 → 落库全链路；
- 模型检查经 env 开关关闭后 A7/A8 SKIPPED、A6 照常（确定性检查不依赖 LLM）；
- model_version 组合串进入响应/落库。

PG 不可达时整文件 skip（开发机必须起 ai-experience-postgres 容器）。
"""

from __future__ import annotations

import socket
import time
import uuid

import pytest

PG_HOST = "127.0.0.1"
PG_PORT = 5432


def _pg_reachable(timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((PG_HOST, PG_PORT), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def client(monkeysession=None):
    if not _pg_reachable():
        pytest.skip(f"postgres_primary ({PG_HOST}:{PG_PORT}) 不可达：本冒烟需真实 PG")
    import os

    # 免 LLM：A7/A8 SKIPPED（模型不可用语义），A6 确定性检查照常
    os.environ["CPS_INITIAL_REVIEW_MODEL_CHECK_ENABLED"] = "false"
    # 回调指快速拒连端口：断言不依赖 Java 在线，后台重试随事件循环结束消亡
    os.environ.setdefault("CPS_JAVA_CALLBACK_BASE_URL", "http://127.0.0.1:1")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:  # 真实 lifespan：DatasourceManager startup/shutdown
        yield c

    os.environ.pop("CPS_INITIAL_REVIEW_MODEL_CHECK_ENABLED", None)
    os.environ.pop("CPS_JAVA_CALLBACK_BASE_URL", None)


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    """真实签名 access token（local.yaml secret_key），非 mock。"""
    from app.core.security import create_access_token

    token = create_access_token(
        {"sub": "wave2-assembly", "tenant_id": "local-factory", "roles": ["cps_admin"]}
    )
    return {"Authorization": f"Bearer {token}"}


def _body() -> dict:
    uid = uuid.uuid4().hex[:8]
    return {
        "issue_id": f"ISS-ASM-{uid}",
        "submission_id": f"SUB-ASM-{uid}",
        "version_no": 1,
        "reason": "设备接地引下线锈蚀严重导致接触不良存在安全隐患",
        "short_term_measure": "已更换锈蚀接地扁铁并对接头做紧固和防腐涂刷处理",
        "long_term_measure": "建立季度专项巡检制度对接地电阻值定期检测并纳入班组考核",
    }


def test_assembly_submit_completes_with_a6(client, auth_headers):
    resp = client.post("/api/v1/agent/rectifications", json=_body(), headers=auth_headers)
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["status"] == "COMPLETED", data
    assert data["overall"] in {"PARTIAL", "PROBLEM", "PASS"}
    detail = client.get(
        f"/api/v1/agent/rectifications/{data['task_id']}", headers=auth_headers
    ).json()
    # A6 确定性检查在组合版本串里；A7/A8 被关闭不应出现
    assert "measure-similarity" in detail["model_version"]
    assert "text-validity" not in detail["model_version"]


def test_assembly_status_poll_roundtrip(client, auth_headers):
    body = _body()
    created = client.post(
        "/api/v1/agent/rectifications", json=body, headers=auth_headers
    ).json()
    task_id = created["task_id"]
    # 幂等重放：同 body 再提交返回同一 task_id
    again = client.post(
        "/api/v1/agent/rectifications", json=body, headers=auth_headers
    ).json()
    assert again["task_id"] == task_id
    # C-03 轮询
    status = client.get(f"/api/v1/agent/rectifications/{task_id}", headers=auth_headers)
    assert status.status_code == 200, status.text
    detail = status.json()
    assert detail["status"] == "COMPLETED"
    assert detail["model_checks"]["measure_similarity"]["implementation_status"] == "IMPLEMENTED"
    assert detail["model_checks"]["text_validity"]["implementation_status"] == "SKIPPED"


def test_assembly_history_requery_after_second_version(client, auth_headers):
    """同 issue 提交 v2：A6 历史比对应读到 v1（真实 PG 落库回读）。"""
    uid = uuid.uuid4().hex[:8]
    v1 = {
        "issue_id": f"ISS-HIS-{uid}",
        "submission_id": f"SUB-HIS1-{uid}",
        "version_no": 1,
        "reason": "第一版原因说明与第二版完全不同的表述内容",
        "short_term_measure": "第一版短期措施更换绝缘子并试验合格后投运",
        "long_term_measure": "第一版长期措施开展红外测温普查并更新台账",
    }
    r1 = client.post("/api/v1/agent/rectifications", json=v1, headers=auth_headers)
    assert r1.status_code == 202, r1.text
    time.sleep(0.1)
    v2 = dict(v1)
    v2["submission_id"] = f"SUB-HIS2-{uid}"
    v2["version_no"] = 2
    v2["reason"] = "第二版原因聚焦锈蚀处理的整改依据与安全评估"
    v2["short_term_measure"] = "第二版短期措施整体更换引下线并复测接地电阻"
    v2["long_term_measure"] = "第二版长期措施纳入防腐蚀专项治理年度计划"
    r2 = client.post("/api/v1/agent/rectifications", json=v2, headers=auth_headers)
    assert r2.status_code == 202, r2.text
    detail = client.get(
        f"/api/v1/agent/rectifications/{r2.json()['task_id']}", headers=auth_headers
    ).json()
    history = detail["model_checks"]["measure_similarity"]["history"]
    assert history["samples"] == 1  # 真实 PG 回读 v1
    assert history["verdict"] == "PASS"
