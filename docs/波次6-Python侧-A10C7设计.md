# 波次 6 Python/Agent 侧交付设计（A10 + C7）

- 基线：`5b67bfd`（main）
- 范围：A10 初审管线边界测试；C7 系统化测试补强（契约 schema 冻结 + 端到端装配 + 测试金字塔标注）
- 原则：**只测行为不改产品代码**；发现的产品问题记录于 §5 待确认节，不静默修

## 1. 交付概览

| 项 | 数值 |
|---|---|
| 新增测试 | 42（A10×20 + 契约冻结×14 + 周报E2E×4 + 计划草稿×4） |
| pytest 全量 | 491 passed / 2 failed（既有环境失败，未新增）/ 1 skipped（不变） |
| ruff app+tests | 0 违规（全库 75 = 基线 69 + 修复后归零于 app/tests，新增为 0） |
| 新增文件 | `tests/initial_review/test_a10_boundaries.py`、`tests/contracts/__init__.py`、`tests/contracts/test_schema_freeze.py`、`tests/weekly_report/test_e2e_assembly.py`、`tests/inspection_plans/test_approval_replay.py`、本文档 |

## 2. A10 初审管线边界测试（`tests/initial_review/test_a10_boundaries.py`）

### 2.1 D-22 冻结口径逐条边界（文本规则）
| 冻结条款 | 锁定测试 |
|---|---|
| 空格算标点 | 全半角空格文本 → 比例违规；全全角 U+3000 同 |
| ……＝1 标点、游程内不判连续 | `…`×10 → P=5 无「连续标点」违规；游程 3/4 个（既有测试互补） |
| 换行不计长度 | 纯换行（含 U+2028/U+2029）→ L=0 空文本违规，不抛异常 |
| 相邻两标点不过（含跨全半角） | 全角"，"+半角"."相邻判连续；全标点文本逐条否决 |
| 三段各自 L≥15 | 三字段独立判定（既有测试互补，此处锁极端值组合） |
| 10×P≤L 精确整数比较 | L=30/P=3 等值通过；L=20/P=2 恰 10% 通过；首尾空白+18 字恰 10% |
| 恰好 L=15 | P=0 通过 |
| \t 计 L 不计 P（Phase 0 §⑥） | 纯 `\t`×20 通过 |
| 参数化安全网 | 12 组极端文本不抛异常 |

### 2.2 服务边界（超时/幂等/迟到/乱序/降级）
- **480s<600s 护栏**：`load_initial_review_settings().deadline_seconds==480` 且 `600-480≥120`（Java 接管窗口余量，防配置漂移）
- **重放不重置计时**：RUNNING 行（deadline=t0+480）在 t0+10 重放 → 响应 deadline_at 仍 t0+480
- **扫描后重放**：过期 RUNNING 行被 status() 惰性扫描置 FAILED/TIMEOUT → 同键 submit 返回原终态 replayed=True、零回调、不重执行
- **COMPLETED 重放**：回调仅 1 次（重放不重发）
- **迟到完成不复活终态**：扫描后 _execute 早退；`_finish` 乐观锁（`WHERE status='RUNNING'`）败者返回 False 不回调
- **乱序回调**：v1/v2 并发，先完成 v2 再 v1 → 回调按完成序各带各 task_id 不串单
- **RustFS HTTP 5xx 降级不伪造**：MockTransport 503 → image_compare=DEGRADED（reason 含「附件获取失败」+「503」），整体 verdict=SKIPPED

## 3. C7 契约测试（`tests/contracts/test_schema_freeze.py`）

冻结常量（防字段漂移，改契约必须改测试=显式版本化）：

| 契约 | 端点 | 锁定内容 |
|---|---|---|
| C-01 | POST /agent/rectifications | 响应 6 键 {review_task_ref,task_id,status,overall,deadline_at,replayed}；首建/重放/409 异参三态。波次 7 澄清：`replayed=True`＝「该幂等键下行已存在」（含 RUNNING 在途重放与并发 UNIQUE 兜底复用），**行存在≠本请求首建**；严格首建判定看 `replayed=False` |
| C-03 | GET …/rectifications/{task_id} | 16 键含 text_checks/model_checks/callback_status；text_checks 内层**短键** {reason,short_term,long_term}；callback_status∈{PENDING,SUCCESS,FAILED}；404 |
| C-04 | POST /agent/room-checks/judge | 15 键；stage_trace 是 dict{type_match,content_judge}（非 list）；502 → detail{error_code,message} |
| C-05 | GET /agent/weekly-reports | 外层 5 键 + items[] 13 键（在 §4 E2E 内联冻结） |
| C-06 | POST /agent/speech-to-text | 16 键含 request_id 别名（=idempotency_key 回显）；重放同构 |
| C-07 | POST /agent/inspection-plans/draft | 8 键；status 枚举 **DRAFT/REPLAYED**；draft_content_json 内嵌 {schema_version:"plan-draft.v1",tasks[]:{task_type,priority,rationale},context:{factory,area,risk_basis}}；重放同构 |
| C-08 | GET …/weekly-reports/{run_id}/download | 响应头 X-Cps-Run-Id/X-Cps-Period/X-Cps-Report-Type/Content-Disposition(attachment) + 字节一致（在 §4 E2E 内联冻结） |
| 错误体 | — | {detail} 单键 |

## 4. C7 端到端装配

### 4.1 周报全链（`tests/weekly_report/test_e2e_assembly.py`）
- **双类型全链**：调度等价遍历（`for rt in settings.report_types`，生产入口 `app/tasks/weekly_report_task.py` 依赖 postgres 装配故模拟）→ 两类型各自 COMPLETED → 归档键按 `weekly-reports/{rt}/` 前缀隔离 → run_no 各自 1 → push_status 恒 UNCONFIGURED、列表 push_unavailable_reason=="推送未配置"（AC-29 未配置可见不伪装）→ C-05 外层+元素 schema 冻结、total==2
- **同窗口重跑幂等**：两类型重跑均 skipped，total 仍 2
- **C-08 头冻结 + 字节一致**：Content-Disposition attachment、X-Cps-* 三头、下载字节==归档字节
- **未完成不可下载**：uploader.available=False → run FAILED → 下载 404

### 4.2 计划草稿→批准回调幂等（`tests/inspection_plans/test_approval_replay.py`）
- 批准链路（PRD §22）：Java applyDraft → C-07 出草稿 → 人工批准 → Java 落库建单；批准后 Java 重取草稿（同 key 重放）必须拿到**同一 draft_id + 逐字段相同内容**，否则批准与建单两份草稿
- 同 key 三次请求：draft_id/draft_content_json/generated_at/model_version("plan-draft/skill@1") 全同，仅 status DRAFT→REPLAYED
- 不同 key → 新 draft_id 不串单；缺省 key 按 source_run_id 派生命中缓存
- 蓝图三类任务恒含 {INSPECT_RECTIFY,INSPECT_PATROL,INSPECT_CHECK}，plan_type 同名主任务 high（`_PLAN_TYPE_BLUEPRINTS` 口径）

## 5. 发现的产品问题（待确认，未修）

### BUG-1（中危）：CheckRunnerError 是死代码，TCP 级故障逃逸为 HTTP 500
- `app/projects/initial_review/application/service.py:58` 定义、`:190` except（→FAILED+CHECK_ERROR），但**全库无任何 `raise CheckRunnerError`**
- 逃逸路径：`check_pipeline.py:341 await self._rustfs.fetch_data_url(key)` → `model_client.py:295-303 fetch_bytes` 仅把 `resp.status_code!=200` 包成 ModelCallError（→DEGRADED，本波次已测）；**TCP 级 `httpx.ConnectError`（RustFS 不可达）裸抛**，穿过 stage 级 except(ModelCallError,TimeoutError) 与 _execute 级 except(CheckRunnerError) → submit 调用方收到未处理异常（HTTP 500），行卡 RUNNING 直到 status() 惰性扫描误标 TIMEOUT「进程可能在执行中崩溃」
- 设计意图（FAILED+CHECK_ERROR+回调）不可达。建议：`_execute` 增 `except Exception` 兜底转 CheckRunnerError 语义，或 fetch_bytes 包 ConnectError。**待产品确认后另开波次修复**
- 观察项（非 bug）：`WeeklyReportService.run_once` 返回 dict 不含 `run_no`（列表接口有）；若 Java 侧需要首跑判定，走 C-05 列表
- 观察项（语义确认）：C-01 `replayed=True` 语义是「行已存在」（含 RUNNING 重放），非「本请求未创建」；Java 侧勿用它区分首建/接管，应以 status==RUNNING+自身重试上下文判断

## 6. 测试金字塔标注

| 层 | 文件 | 说明 |
|---|---|---|
| 单元 | tests/test_text_rules.py（29，既有） | 纯函数规则 |
| 集成 | tests/initial_review/*、tests/contracts/*、tests/weekly_report/*、tests/inspection_plans/*、tests/room_checks/*、tests/speech/* | 内存 sqlite + fake 依赖 + 真实服务编排 |
| 冒烟（真实环境，不进 pytest） | scripts/j0_initial_review_e2e.py、scripts/c3_weekly_report_smoke.py、scripts/wave2_real_model_smoke.py | 真模型/RustFS/推送 |

## 7. 验证记录
- `pytest -q`：491 passed / 2 failed（`tests/cps/test_integration.py::test_api_auth_owner_tenant_schema_and_no_frontend`、`tests/harness/test_api_worker.py::test_api_auth_tenant_and_validation`，基线既有环境失败）/ 1 skipped —— 与基线 449p/2f/1s 相比 +42 passed，失败与 skipped 不变
- `ruff check app tests`：All checks passed
