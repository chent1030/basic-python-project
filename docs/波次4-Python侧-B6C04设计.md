# 波次 4 Python 侧 — B6 辅房视觉点检 Agent（契约 C-04 room-checks/judge）设计

> 基线：basic-project `962ac91`（main）。紧接波次 3（C-07 inspection-plans/draft）。
> 对应 PRD §23.2/§24（辅房点检项与视觉判定）、AC-07/08/09；架构文档契约表 C-04。

## 1. 端点契约（C-04）

`POST /api/v1/agent/room-checks/judge`（Java → Python，**同步**，现场等待）

- 鉴权：内网 trust → `Identity.require("cps_admin")`，与 C-01/C-03/C-07 同源
  （`FrameworkRoute` + `app/api/v1/endpoints/agent_runs.py` 的 principal 体系）。
- 幂等：键 = `room-judge-{submissionId}-{itemId}-{attempt}`（契约原文）。
  Java 侧显式传 `idempotency_key`；缺省时服务端按同格式派生。进程内
  LRU+TTL 缓存（与 C-07 同款：TTL 24h / 1024 条；多实例部署演进 Redis/DB），
  重放返回同结果 + `replayed=true`。**技术失败不缓存**（同键重试可恢复）。

### 入参（点检项定义 + 照片 object keys）

| 字段 | 必填 | 约束 | 说明 |
|---|---|---|---|
| `submission_id` / `item_id` | ✓ | str ≤64 | 幂等键组成部分 |
| `attempt` | ✓ | int ≥1 | 同 item 重拍/补拍递增 |
| `item_content` | ✓ | str ≤512 | 点检内容（如"地面无杂物、无积水"） |
| `item_type` | ✓ | str ≤64 | 点检类型=照片应含对象类别（如"地面"；≠辅房类型，PRD 23.2） |
| `photo_object_keys` | ✓ | list[str]，1..8（端点）/≤`max_images_per_side`(4，模型预算) | RustFS object keys |
| `deduction` / `config_version` | ✗ | number / str ≤64 | 扣分值与配置版本，透传留痕（PRD 23.2：执行记录引用检查时配置） |
| `room_name` | ✗ | str ≤256 | prompt 上下文 |
| `idempotency_key` | ✗ | str | 缺省派生 |

### 响应（200；业务状态 + 证据留存）

```json
{
  "idempotency_key": "room-judge-sub-1-item-9-1",
  "status": "JUDGED",            // TYPE_MISMATCH | JUDGED | UNJUDGEABLE | SKIPPED
  "verdict": "PASS",             // PASS|FAIL；仅 JUDGED 时非空
  "reason": "...", "evidence": "...",
  "photo_object_keys": ["room/101/ground-1.jpg"],
  "item_snapshot": {"content": "...", "type": "...", "deduction": 10, "config_version": "v3"},
  "stage_trace": {
    "type_match":    {"status": "PASS|TYPE_MISMATCH|SKIPPED", "prompt_version": "...", "photo_subject": "...", "reason": "..."},
    "content_judge": {"status": "PASS|FAIL|UNJUDGEABLE|NOT_REACHED", "verdict": "...", "reason": "...", "evidence": "...", "confidence": 0.92}
  },
  "model_version": "room-judge/qwen-vl@1",
  "prompt_versions": {"type_match": "room-type-match/qwen-vl@1", "content_judge": "room-content-judge/qwen-vl@1"},
  "judged_at": "…ISO8601…", "replayed": false
}
```

状态语义（严格对齐 PRD 24.1 / AC-08 / AC-09）：
- `TYPE_MISMATCH`：照片主体不符点检项类型 → **须重拍，不进入合格判断**；
- `JUDGED`：类型已匹配，内容判定 `PASS|FAIL` + 理由；
- `UNJUDGEABLE`：模糊/遮挡/光线不足无法判定 → **须补拍并阻止提交**（不得猜）；
- `SKIPPED`：模型检查禁用 / provider 未配置 / RustFS 配置缺失等前置条件不具备
  → **不伪造判定**（A7 原则，同 initial_review 的 SKIPPED 口径）。

错误映射：`403` 非 cps_admin；`422` 入参不合法（含照片数超模型预算）；
`502` 判定链技术失败（模型调用异常/超时/输出两次无效/取图网络故障，
`detail.error_code=ROOM_JUDGE_MODEL_FAILED`，不缓存 → Java 同键重试）。

## 2. 判定链（两阶段，照片-only）

```
judge(req)
 ├─ 幂等缓存命中 → replayed=true 返回
 ├─ 前置检查：model_check.enabled=false 或 rustfs 配置缺失 → SKIPPED（缓存可重放）
 ├─ ①取图：object_key → RustFS S3 SigV4 GET → data URL
 │    （复用 initial_review RustFSObjectFetcher；env CPS_STORAGE_* 优先，对齐 Java）
 ├─ ②类型匹配（视觉模型，PRD 24.1 第一步）：
 │    不符 → TYPE_MISMATCH 短路，content_judge=NOT_REACHED（AC-08：不扣分不绕过）
 ├─ ③内容判定（视觉模型，按 item_content 逐字进入 prompt）：
 │    PASS|FAIL → JUDGED；UNJUDGEABLE → 须补拍（AC-09）
 └─ ④证据留存：stage_trace + 照片引用 + item_snapshot + prompt/model 版本锚点
      随响应返回，由 Java 落点检执行记录
```

- 多照片口径：**全部照片主体均须类型匹配**，内容判定联合作出
  （主流场景单照片；AC-08 桌面/地面错拍即拒）。
- 超时预算对齐 initial_review 惯例：每阶段单次 `field_budget_seconds`
  （默认 90s，`asyncio.timeout`，含模型输出自纠重试 1 次）；
  同步链最坏 ~2×90s+取图，**Java 侧同步 HTTP 客户端超时需 ≥240s**（待确认①）。
- Prompt 版本化常量：`room-type-match/qwen-vl@1`、`room-content-judge/qwen-vl@1`
  （变更须递增主版本并同步本文档）。

## 3. 配置与复用（零新增依赖）

- **沿用** `cps_agent.initial_review.model_check`（qwen vision：
  `Qwen2.5-VL-7B-Instruct`，temperature 0.1）与 `.rustfs` 节；env
  `CPS_INITIAL_REVIEW_MODEL_CHECK_ENABLED` / `CPS_STORAGE_*` 同套生效
  （initial_review 与 room_checks 同开同关，部署心智一致——待确认④是否需独立开关）。
- 模型客户端/取图器/结构化输出容错解析（剥围栏→校验→错误回喂重试 1 次→
  两次无效判技术失败）全部复用 `app/projects/initial_review/infrastructure/
  model_client.py`，本波次不复制实现；测试注入 `FakeModelCheckClient` + Fake 取图器。

## 4. 代码结构（对齐现有项目分层）

| 文件 | 职责 |
|---|---|
| `app/projects/room_checks/domain/models.py` | C-04 状态枚举、JudgeRequest/JudgeResult、视觉输出 pydantic schema（TypeMatchSchema/ContentJudgeSchema） |
| `app/projects/room_checks/application/service.py` | RoomCheckJudgeService：幂等缓存 + 判定链编排 + SKIPPED/技术失败分流（JudgeRequestError→422、JudgeTechnicalError→502） |
| `app/projects/room_checks/infrastructure/prompts.py` | 两阶段 prompt 常量（版本化） |
| `app/api/v1/endpoints/room_checks.py` | 端点：入参规整 + 鉴权 + 状态码映射（C-07 同款惰性 app.state 装配） |
| `app/api/v1/router.py` | 挂载 room_checks.router（+2 行） |
| `tests/room_checks/test_service.py`、`test_endpoint.py` | 19 个测试（见 §5） |

## 5. 测试与验收

- `pytest tests/room_checks/ -q`：**19 passed**；全量 `414 passed, 2 failed`——
  2 failed 为既有环境失败（tests/cps/test_integration.py、tests/harness/
  test_api_worker.py），与基线 395p/2f 相比**零新增失败、+19 通过**。
- ruff：新增文件 0 违规（仓库既有 69 条 legacy 违规基线不变）。
- 覆盖点：类型不匹配短路且不触内容判定、JUDGED PASS/FAIL 全链证据、
  UNJUDGEABLE 须补拍、三种 SKIPPED（禁用/RustFS 缺失/provider 未配置）不伪造、
  ModelCallError/取图故障/超时→技术失败且**不缓存**（同键重试可恢复）、
  幂等重放、照片超预算 422、端点 403/422/502 与契约键派生。

## 6. 待确认项（联调前需对齐）

1. **Java 同步客户端超时**：建议 ≥240s（2 阶段×90s 预算+取图），或改为
   room_checks 独立预算配置节。
2. **多照片口径**：现为"全部须类型匹配、内容联合判定"；若业务要求逐张独立
   判定/逐张出证据需调整（现 stage_trace 已含模型给出的整体理由）。
3. **技术失败映射**：现为 502+error_code（Java 同键重试）；若 Java 希望统一
   200+业务状态体（契约仅定义 TYPE_MISMATCH/JUDGED/UNJUDGEABLE 三态）需补充契约。
4. **模型开关**：与 initial_review 共用 `CPS_INITIAL_REVIEW_MODEL_CHECK_ENABLED`
   （同开同关）；若需独立开关再加 room_checks 专属 env。
5. **真实模型冒烟**：单测全部 Fake 不触网；qwen-vl 真链路冒烟留联调环境执行。
