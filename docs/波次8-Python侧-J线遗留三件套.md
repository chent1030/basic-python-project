# 波次 8 Python/Agent 侧设计：J 线遗留三件套 + I 线重估

基线 `2808312`（main）。范围：J 线联调报告（cps/docs/波次7-J线联调报告.md 第四节）转交的 #2/#3/#4 全部关闭，I 线缺口重估单独成文（`I线缺口重估-20260926.md`）。

## 1. 终态重放补发回调（转交 #3）

- **问题**：同 task_ref 重投递返回 202 `replayed:true` 后不补回调，Java 只能等 600s 超时兜底。
- **方案**：`service.submit()` 中，命中**提交时已终态**的行（`not created and existing_status != "RUNNING"`）→ `dispatch(_deliver_callback(task_id))` 补发一次后再返回。
- **门槛必须用提交前状态**：重放 RUNNING 行由执行链接手跑到终态时，终态迁移已自然回调一次；若用重载后的 `row.status` 判断会重复回调（本波修复的半成品 bug，`test_replay_running_row_executes_normally_single_callback` 钉死）。
- **幂等安全**：回调 Body 幂等键恒为 `initial-review-result-{task_id}`，Java 端按键去重，重复投递无副作用。
- 旧契约测试 `test_completed_replay_sends_no_second_callback` 按新语义改写为 `test_completed_replay_re_sends_callback_idempotent`（2 次回调同键）。

## 2. 手动回调重推（转交 #4）

- **端点**：`POST /api/v1/agent/rectifications/{task_id}/callback/re-push`（`Identity.require("cps_admin")`）。
- **语义**：行不存在 404；`RUNNING` → 409 `TaskNotTerminal`；同步等待发送结果返回 `{task_id,status,pushed,callback_status,callback_attempts,last_error}`。
- **配额**：`callback_attempts` 累计口径（正常投递+手动重推），达 `callback_manual_push_max_total_attempts`（默认 20，config 可配）→ 429 `CallbackPushBudgetExhausted`，防滥用。
- 测试：service ×5（发送累计/失败记录/未知行/运行中拒绝/配额拒绝）+ router ×5（200 round-trip/404/409/429/403）。

## 3. 结构化输出鲁棒性（转交 #2）

- **问题**：初审文本阶段偶发 `输出中未找到 JSON 对象`，重试 2 次后规则兜底 → 逐项意见缺失、耗时拉长。
- **双管齐下**：
  1. **Prompt v2**（`TEXT_VALIDITY_PROMPT_VERSION=text-validity/qwen-plus@2`、`IMAGE_COMPARE_PROMPT_VERSION=image-compare/qwen-vl@2`）：末尾强制附 `_JSON_ONLY_RULE`（只输出一个 JSON 对象、禁解释文字/前后缀/markdown 栅栏）。
  2. **解析器多级降级** `extract_json_object()`：整段直解 → markdown 栅栏内容 → 平衡花括号扫描截取首个完整对象 → 修复重试（单引号→双引号、尾逗号剔除）。全部失败仍 ValueError → 上层重试 1 次 → 两次无效走 DEGRADED 规则兜底（不伪造）。
- **口径不变**：契约响应 schema 冻结测试未动；失败的模型检查仍记 DEGRADED。

## 4. 验证

- pytest **528 passed / 2 failed（既有环境基线，无新增）/ 1 skipped**；ruff 全绿。
- 失败基线：`tests/cps/test_integration.py::test_api_auth_owner_tenant_schema_and_no_frontend`、`tests/harness/test_api_worker.py::test_api_auth_tenant_and_validation`（环境依赖，波次 1 起既有）。

## 5. 遗留

- 转交 #2 真实环境复发率需在下次全链联调用真实 DashScope 观察（冒烟已过，联调期的连续失败形态需生产 prompt 样本验证 v2 是否根治）。
- I 线缺口与建议见 `I线缺口重估-20260926.md`。
