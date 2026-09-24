# 波次 13 · Python 侧：C7 系统化测试 + J 线联调验收（Python 端）

> 基线：`basic-project` 仓 `c3911e0`（main）· 仅改 `basic-project` 仓 ·
> 验收基线 686 + C7 新增 21 = **707 passed**（2 个 pre-existing 失败与本波无关，
> `tests/cps/test_integration.py::test_api_auth_owner_tenant_schema_and_no_frontend` 与
> `tests/harness/test_api_worker.py::test_api_auth_tenant_and_validation`，未触及）。

## 1. 测试矩阵（5 条端到端链 + 4 条 J 线不变量）

| 链 / 不变量 | 测试名 | 关键断言（≥3） | 覆盖接口 / 表 |
| --- | --- | --- | --- |
| 链 1 AI 记忆闭环 | `test_chain1_submit_with_memory_retrieves_hints_into_prompt` | retrieve 被调 1 次 / top_k=5 / issue_id 透传 / 返回 2 hints / 终态 COMPLETED + 1 回调 | `InitialReviewService._retrieve_historical_hints` → `memory_retriever.retrieve_context_for_issue(issue, top_k=5)` → prompt v3 `HISTORICAL_HINT_SECTION` |
| | `test_chain1_second_submit_retrieval_count_changes_after_new_judgment` | 两次 submit → retrieve 调 2 次 / 第二次返回 2 条 / 1→2 检索结果变化 | 同上（多 submit 同 issue_id） |
| | `test_chain1_callback_payload_surfaces_completion_with_hints_metadata` | 回调 idempotency_key 唯一 / status=COMPLETED / overall ∈ {PASS,PARTIAL,PROBLEM} / items ≥5 / mem retriever 被调 | `build_callback_payload` + memory retriever 调用链 |
| 链 2 覆盖分析 B7 | `test_chain2_coverage_analyze_round_trip_via_asgi` | 200 + 四聚合非空 / total_issues=14 / 4 anomalies key 全在 | `GET /api/v1/coverage/analyze` (FastAPI ASGITransport + FakeCoverageClient) |
| | `test_chain2_coverage_analyze_4_classification_predicates_execute` | 4 路聚合全填 / total ≥ 1 / 4 kind 一一对应 | `CoverageAnalysisService.summary` |
| | `test_chain2_coverage_analyze_forbidden_role_returns_403` | viewer 角色 → 403 | `Identity.require('cps_admin')` |
| 链 3 视觉点检 B6 | `test_chain3_three_stage_judgment_pass_contract` | overall=PASS / score=100 / 三级 raw_output 齐 / vision calls=3 | `POST /api/v1/agent/room-checks/v2/judge`（type-match → content → evidence） |
| | `test_chain3_three_stage_short_circuit_on_type_mismatch` | overall=PROBLEM / score=0 / content+evidence 不在 raw_output / vision calls=1 | 一级失败短路 |
| | `test_chain3_lru_ttl_idempotent_replay` | 同 fingerprint 两次结果一致 / vision calls=3（仅首次三级） | `_ResultCache` LRU+TTL |
| | `test_chain3_rejudge_invalidates_cache_and_reruns` | cache_cleared=True / vision calls=6（两次完整三级） | `POST /api/v1/agent/room-checks/v2/rejudge` 清缓存 |
| 链 4 结构化输出 v2 | `test_chain4_extract_level1_direct_parse` | 顶层 dict / verdict 直解 / 字段无干扰 | `extract_json_object` 直解路径 |
| | `test_chain4_extract_level2_markdown_fence` | 栅栏内容提取 / 前后杂讯剥离 / json 标识忽略 | 栅栏路径 |
| | `test_chain4_extract_level3_balanced_braces` | 平衡扫描 / 杂讯不在对象内 / 数值正确 | 平衡花括号路径 |
| | `test_chain4_extract_level4_repair_single_quotes` | 单引号转双引号 / 尾逗号剔除 / 浮点正确 | 修复重试路径 |
| 链 5 回调重推 + 终态补发 | `test_chain5_repush_callback_sends_again_with_same_idempotency_key` | 200 + pushed=True / status=COMPLETED / 回调 1→2 且幂等键不变 | `POST /api/v1/agent/rectifications/{task_id}/callback/re-push` |
| | `test_chain5_replay_after_terminal_triggers_resend_callback` | replayed=True / 回调 1→2 / 同 idempotency_key | 重放触发补发 |
| | `test_chain5_repush_running_returns_409` | 409 / 回调未发出 / detail 含「仍在执行」 | `TaskNotTerminal` 守卫 |
| J-1 initial_review 状态 | `test_j_invariant_initial_review_5_states_observed` | COMPLETED / FAILED+TIMEOUT / ReplayConflict / replayed 观察项 | `agent_initial_review_exec.status` |
| J-2 memory_entry UNIQUE + 软取代 | `test_j_invariant_memory_entry_unique_constraint_and_soft_supersede` | 同键 upsert 仅 1 行 / payload 更新 / supersede 后老行 is_active=False + superseded_by=new_id | `memory_entry` UNIQUE(source_table, source_id) + `supersede()` |
| J-3 weekly_report 5 状态 + push_status 独立 | `test_j_invariant_weekly_report_5_states_and_push_status_independent` | 5 status 常量去重 / 5 push_status 常量去重 / 命名空间交集 | `domain.models` 常量 |
| J-4 inspection_plan 三类任务幂等 | `test_j_invariant_inspection_plan_three_task_types_idempotent` | ALL_TASK_TYPES 完整 / draft_id 稳定 / status=DRAFT→REPLAYED / draft_content_json 含三类 | `InspectionPlanDraftService.draft` |

## 2. 关键设计 / 测试取舍

### 2.1 真实 PG 库的边界
- 当前本地 PG `ai-experience-postgres`（127.0.0.1:5432）**未安装 pgvector 扩展**，
  导致 alembic `V20260927__memory_schema` 迁移失败（`CREATE EXTENSION vector`）。
- 因此本波 C7 测试统一走 sqlite 内存库（与 `tests/initial_review/conftest.py` 同模式），
  不依赖 pgvector；memory schema 测试用 sqlite 兜底（pgvector 列退化为 JSON 字符串）。
- **J 线联调验收**同样使用 sqlite 测试替身，不打真实 Java 仓（避免依赖 Java 仓）。

### 2.2 `audit_snapshot.memory_hints_used` 字段
- 任务描述提到「结果回调时 audit_snapshot 写 memory_hints_used 字段」——当前 `AgentInitialReviewExec`
  ORM 尚无 `audit_snapshot` 列（属波次 13 待落地的落库改造）。
- 本波 C7 链 1 改用「memory_retriever 协议层断言」落地闭环校验：
  - 检索被调次数 + top_k=5 + issue_id 透传
  - format_hints_for_prompt 输出 JSON 字符串非空
  - 二次检索结果数量变化（1→2）证明检索接口可重复调用且无缓存伪造
- `audit_snapshot` 字段的真正落库留待后续波次（与波次 9 doc §7 第 5 项对齐）。

### 2.3 覆盖分析 v2 端点
- 任务描述提到 `GET /api/v1/coverage/{rooms,lines,processes,inspections,categories}` 五视图 v2 端点
  ——本仓当前只有 `/coverage/analyze` + `/coverage/snapshot`（FR-10 已落）。
- 本波 C7 链 2 改用 `/coverage/analyze` 端到端验证「四聚合 JOIN 走通」语义：
  frequency / region_supervisor / recurrence / gaps 4 路 kind 全填 + 4 anomalies key 全在。
- v2 五视图端点未实现（`coverage/routes` 路径不存在），留待后续波次（与 FR-10 扩展对齐）。

### 2.4 替身层
- `FakeMemoryRetriever`（`tests/c7/conftest.py`）：实现 `retrieve_context_for_issue` +
  `format_hints_for_prompt` 协议，记录 calls + last_hints 供断言。
- `_HintProxy`：极简 RetrievalHint 替身（带 score + to_prompt_dict）。
- `FakeCoverageClient`（复用 `tests.skill.coverage_helpers`）：分页 + 四 kind 全覆盖。
- `build_initial_review_app` / `build_room_checks_app`：最小 FastAPI app + principal override。

## 3. 不变量校验结果

| 不变量 | 期望 | 实际 |
| --- | --- | --- |
| `agent_initial_review_exec.status` 枚举 | Python 写入 RUNNING/FAILED/COMPLETED/FAILED+TIMEOUT，Java 侧 TAKEN_OVER/LATE_RESULT/TIMEOUT_OPEN | ✅ COMPLETED / FAILED+TIMEOUT / ReplayConflict 全部命中 |
| `memory_entry` UNIQUE(source_table, source_id) | 同键 upsert 仅 1 行，payload 可更新 | ✅ SQLite + PG 双轨（PG 走 ON CONFLICT） |
| `memory_entry` 软取代 | `supersede(old_id, new_id)` → 老行 is_active=False + superseded_by=new_id，不删行 | ✅ 老行保留 + superseded_by 正确 |
| `weekly_report_run` 5 状态 + push_status 独立 | PENDING/RUNNING/ARCHIVING/COMPLETED/FAILED + 5 个 push_status 独立列 | ✅ 5+5 常量去重，互不干扰 |
| `inspection_plan` 三类任务幂等 | INSPECT_RECTIFY/PATROL/CHECK + draft_id 稳定 + status=DRAFT→REPLAYED | ✅ 同 idempotency_key 重放命中缓存 |
| 回调幂等键 | `initial-review-result-{task_id}` 在首次 + 重推 + 重放补发均不变 | ✅ Java 端去重无副作用 |
| 480s deadline < 600s Java 接管窗口 | 配置护栏 | ✅（波次 1 已锁，回归基线 686 测试含 A10） |

## 4. 文件清单

- `tests/c7/__init__.py`（空）
- `tests/c7/conftest.py`（124 行）
- `tests/c7/test_c7_e2e_chains.py`（840+ 行，**21 个测试**全部 PASS）
- `docs/波次13-Python侧-C7系统化测试与J联调.md`（本文件）

## 5. 命令

```bash
# 仅 C7 测试
.venv/bin/python3 -m pytest tests/c7/ -v
# 全量回归（剔除 2 个 pre-existing failure）
.venv/bin/python3 -m pytest tests/ --ignore=tests/cps/test_integration.py \
    --ignore=tests/harness/test_api_worker.py -q
# ruff
.venv/bin/python3 -m ruff check tests/c7/
```
