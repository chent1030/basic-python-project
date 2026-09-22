# 波次 3 Python/Agent 侧：C1 报告 Skill + C2 报告 Agent + C3 调度 + C4 归档 + C6 推送 + C-05/C-08 端点 + **C-07 计划草稿**

> 日期：2026-09-26 ｜ 范围：basic-project `app/skills/weekly_report/` + `app/projects/weekly_report/` + `app/projects/inspection_plans/`
> 基线：波次 2（4a11fe6 初审检查管线）+ 远端「架构重新修改」已合入
> 总原则：AI 输出只是意见；上传成功≠推送成功；未接入推送显示「推送未配置」。

## 1. C1 报告 Skill（`app/skills/weekly_report/`）

- `WeeklyReportSkill` Protocol：`render(spec, data) → RenderedReport(content_type, body, skill_version, template_id)`
- `DefaultWeeklyReportSkill` 模板拼装实现（确定性，**无 LLM 调用**，单测复盘由 D1 计划 Agent 后接入）
- `templates.py` 维护 `REPORT_TEMPLATES` 注册表（按 `report_type` → `ReportTemplate`），未命中 fallback `generic`
- 产物：`text/html; charset=utf-8`，含周编号、窗口、生成时间、数据摘要段落

## 2. C2 报告 Agent（`app/projects/weekly_report/application/service.py`）

`WeeklyReportService.run_once(report_type, window)` 编排（流程同既有 initial_review 降级三态）：

1. **advisory lock**（PG `pg_try_advisory_xact_lock(0x57524550='WREP')`）防多实例双触发；sqlite 测试跳过
2. **同窗口幂等检查**：UNIQUE(report_type, window_start, window_end) + 状态 COMPLETED → 返回 `{"skipped": True, "reason": "同窗口已 COMPLETED"}`
3. **状态机**：`PENDING → RUNNING → ARCHIVING → COMPLETED/FAILED`
4. **数据获取**：`WeeklyReportDataFetcher` Protocol + `StubDataFetcher`（B7 Java 到位后替换）
5. **Skill 渲染** → `RustFSWeeklyReportUploader.put_bytes`（沿用 A7 SigV4，bucket 可独立 `CPS_WEEKLY_REPORT_BUCKET`）
6. **Pusher 推送** → `record_push`（独立列，与 status 解耦；AC-29）

## 3. C3 调度（`app/tasks/weekly_report_task.py`）

- **APScheduler** cron `0 8 * * MON`，timezone Asia/Shanghai，misfire_grace_time=7200
- 环境开关 `CPS_WEEKLY_REPORT_SCHED_ENABLED`，**默认 false**（防开发机周一早误触发）；生产部署置 `true` 启用
- 配合 advisory lock + UNIQUE 索引形成双重防双触发
- 手动等价验证：`scripts/c3_weekly_report_smoke.py`

## 4. C4 归档运行记录

- alembic 迁移 `c3a1b2d3e4f5_create_weekly_report_run`（`down_revision = b41c7f2ae903`，已应用于 ai-experience-postgres 127.0.0.1:5432/alembic_version 已更新）
- `weekly_report_run` 表 24 列 / 6 索引，关键列：
  - `status` ∈ `PENDING/RUNNING/ARCHIVING/COMPLETED/FAILED`
  - `push_status` ∈ `PENDING/SUCCESS/FAILED/SKIPPED/UNCONFIGURED`（独立列，对齐 AC-29「上传成功≠推送成功」）
  - `archive_object_key` 路径约定：`weekly-reports/{report_type}/{yyyy-Ww}/{seq}.html`
  - `UNIQUE(report_type, window_start, window_end)` 兜底同窗口幂等

## 5. C6 推送接口预留（`app/projects/weekly_report/infrastructure/pusher.py`）

- `Pusher` 接口：`async send(run_id, payload) → PushResult`
- `NoOpPusher` 默认实现：`push_status = UNCONFIGURED`，`push_result = "推送未配置"`（AC-29）
- 未来可接入企业微信 / 钉钉 / 邮件；接入由用户在 Java/部署层做，Python 侧只暴露接口

## 6. C-05/C-08 端点（`app/api/v1/endpoints/weekly_reports.py`）

- `GET /api/v1/agent/weekly-reports`：分页列表（按 `report_type/period/status/push_status` 过滤）
- `GET /api/v1/agent/weekly-reports/{run_id}/download`：受控流式返回归档字节（admin → java proxy）
- 鉴权：复用 `agent_runs.Identity.require('cps_admin')`（与 C-01/C-03 同源）

## 7. **C-07 巡检计划草稿（新增 — 本轮补完 Java 客户端对接）**

> 上一波次 Java `CpsAgentFrameworkClient.requestInspectionPlanDraft` 已就位，本波次完成 Python/Sel-server 端。

- **服务**（`app/projects/inspection_plans/application/service.py`）：
  - `InspectionPlanDraftService.draft(req) → DraftResult`
  - 幂等：进程内 LRU+TTL 缓存（key=`idempotency_key`，TTL=24h）；重放同 key 返回同 `draft_id` + `status=REPLAYED`
  - 任务蓝图：`plan_type` 决定三类任务优先级权重
    - `WEEKLY_RECTIFY` → [RECTIFY(high), PATROL(normal), CHECK(low)]
    - `WEEKLY_PATROL`  → [PATROL(high),  CHECK(normal), RECTIFY(low)]
    - `WEEKLY_CHECK`   → [CHECK(high),   PATROL(normal), RECTIFY(low)]
    - 未知 plan_type   → 三类均 normal（兜底）
  - `draft_content_json` schema_version=`plan-draft.v1`，含 `tasks[]/{task_type, priority, rationale}` + `context{factory, area, risk_basis}`
  - `model_version = "plan-draft/skill@1"`（确定性实现）
  - `DraftGenerator` Protocol 暴露扩展点，后续可替换 LLM 实现

- **端点**（`app/api/v1/endpoints/inspection_plans.py`）：
  - `POST /api/v1/agent/inspection-plans/draft`（Java base-url=`http://127.0.0.1:8000/api/v1` + 相对路径）
  - 载荷：`{idempotency_key?, source_run_id, plan_type, title, factory?, area?, risk_basis?}`
  - 鉴权：`Identity.require('cps_admin')`（内网 → Java 调用）
  - 422 校验：缺必填字段 / 空字符串 / 长度超限

- **路由**（`app/api/v1/router.py`）：注入 `inspection_plans.router`

## 8. 集成约束

- **B7 周报数据接口（Java V20260927）→ Python 端替换**：`StubDataFetcher` 已就位，B7 到位后实现 `WeeklyReportDataFetcher` Protocol 即可
- **C-07 → Java D3 建单**：Java `CpsInspectionPlanService.applyDraft` 调 `CpsAgentFrameworkClient.requestInspectionPlanDraft` → Python C-07 端点 → 落 `cps_inspection_plan`（D3 事务内 INSERT 三类任务）
- **C3 调度默认关闭**：生产部署必须 `CPS_WEEKLY_REPORT_SCHED_ENABLED=true`

## 9. 验证

- `pytest tests/`：**395 passed / 2 failed（既有环境问题，与基线一致）/ 1 skipped**；本轮新增 15（C-07 service 8 + endpoint 7）
- `ruff check app tests`：全绿
- 真实 DB（ai-experience-postgres:5432）：
  - alembic 升级 → `weekly_report_run` 24 列 + 6 索引 + `alembic_version = c3a1b2d3e4f5`
  - `c3_weekly_report_smoke.py` 双分类全 PASS：`status=COMPLETED, push_status=UNCONFIGURED, archive_object_key=weekly-reports/{type}/2026-W37/1.html`
- 真 RustFS 上传路径可达（容器 cps-rustfs 9000/9001 healthy）

## 10. 【待确认】与风险

- 【待确认 D-10】周报重跑同窗口产生新 `run_no` 还是覆盖：当前实现跳过（UNIQUE 兜底）；后续版本保留路径版本化语义
- 【待确认 D1 计划 Agent】当前确定性模板，`DraftGenerator` Protocol 已留扩展点；待 D1 Agent LLM 实现 + 业务样本校准蓝图权重
- 【风险】B7 周报数据接口（Java V20260927）尚未与 Python 联调；`StubDataFetcher` 临时替代
- 【风险】C-07 进程内缓存：开发/单实例够用；多实例部署需替换 Redis/DB 缓存
- 【风险】C-07 任务枚举名 `INSPECT_*` 与 Java D3 一致；业务命名（中文/调整）待产品确认
- 【风险】RustFS bucket `cps-attachments` 需预先存在；首次部署初始化阶段创建