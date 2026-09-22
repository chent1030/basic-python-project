# 波次 2 Python/Agent 侧：A6 雷同性 + A7 视觉比对 + A8 文本有效性 + A9 意见汇总

> 日期：2026-09-26 ｜ 范围：basic-project `app/projects/initial_review/`
> 基线：波次 1（410c0c8 文本规则引擎 + C-01/C-02/C-03 骨架）+ J0 联调（369313f）
> 总原则（PRD §28.4 / ADR）：AI 输出只是意见；技术失败/不可用不得冒充内容判定、不得伪造通过。

## 1. 检查管线（CheckPipeline）

`application/check_pipeline.py`：`CheckPipeline.run(request, snapshot)` 编排四项检查，输出
`(text_checks, model_checks, overall)` 三元组由 service 落库并回调 Java（C-02）。

| 检查 | 类型 | 不可用时语义 |
|---|---|---|
| A5 文本规则（波次 1） | 确定性（domain/text_rules） | 永远可执行 |
| A6 雷同性 | 确定性（bigram Jaccard） | 无历史/无对侧文本 → SKIPPED+原因 |
| A7 视觉比对 | 模型（vision） | 模型禁用/不可用/附件缺失 → SKIPPED/DEGRADED |
| A8 文本有效性 | 模型（text） | 字段空 → SKIPPED；模型失败 → DEGRADED |

降级语义三态（`domain/verdicts.py`）：
- `IMPLEMENTED`：真实执行并出具结论；
- `DEGRADED`：尝试执行但技术失败（超时/异常/模型输出两次无效），verdict=SKIPPED + reason 含「执行失败/预算超时」；
- `SKIPPED`：前置缺失（模型禁用、附件一侧为空、RustFS 未配置、无历史）未尝试调用。
基础设施故障**不**把 overall 推成 PROBLEM（任务级解读仍由 A9 聚合规则决定）。

## 2. A6 雷同性（`domain/measure_similarity.py`）

- 算法：文本归一化（去空白/全半角）→ 字符 bigram 集合 → Jaccard 相似度（确定性、无模型依赖）。
- 两路判定（`check_measure_similarity`）：
  1. 同单 short↔long 互抄：≥ `same_fail`(0.90) FAIL，≥ `same_warn`(0.75) WARN；
  2. vs 历史提交（同 issue 早期版本）：≥ `history_fail`(0.85) FAIL，≥ `history_warn`(0.70) WARN。
- 历史来源合并：C-01 载荷 `issue_snapshot.history_submissions`（Java 侧提供）+ 本库 COMPLETED
  早期版本（`history_loader` 注入），`_dedupe_history` 去重；历史装载失败仅 warning 不阻断。
- 阈值入 `config/config.yaml: cps_agent.initial_review.similarity`，**D-05 暂定值**（W3 前用业务样本校准）。

## 3. A7 视觉比对（`domain/image_compare.py` + `infrastructure/prompts.py`）

- 结构化输出 `ImageCompareResult{verdict, reason, confidence, readings_observed, readings_match}`。
- 送检：前后照片各取 ≤4 张（`max_images_per_side`），拼 data URL 一次视觉调用完成「整改前后比对 + 读数核对」
  （读数核对指令并入同一 prompt，不另发请求）。
- 升级规则：模型判 PASS 但提交读数与照片观测不一致 → 强制不低于 WARN（照片与登记值矛盾）。
- 附件双模式：
  - `object_key` 模式：经 `RustFSObjectFetcher`（S3 SigV4 签名，endpoint/key 从
    `cps_agent.initial_review.rustfs`，env `CPS_STORAGE_*` 优先，对齐 Java）拉取字节；可从快照重放；
  - `content_base64` 模式：仅首次执行（request 在内存）可用；重放/崩溃恢复路径如实记 DEGRADED「原文不可得」。
- RustFS 配置不完整 → SKIPPED「配置不完整」，不伪造。

## 4. A8 文本有效性（`domain/text_validity.py`）

- 逐字段（reason/short_term_measure/long_term_measure）调 text 模型输出
  `FieldValidity{verdict, reason, confidence, problem_fragment}`，prompt 带版本号
  （`text-validity/qwen-plus@1`，入 `model_version` 组合串）。
- 空字段（纯空白）→ SKIPPED 不发模型调用；单字段预算 `field_budget_seconds`(90s 可配)，
  超时/输出两次无效 → 该字段 DEGRADED，**不放大**到其余字段；整任务 480s 上限不变。
- 模型客户端 `DashScopeModelClient`（`infrastructure/model_client.py`）：经 llm.providers.qwen
  配置，`extract_json_object` 容错解析；测试用 `FakeModelCheckClient` 注入，单测不依赖真实模型。
- 开关：env `CPS_INITIAL_REVIEW_MODEL_CHECK_ENABLED=false` → A7/A8 全部 SKIPPED（A5/A6 确定性照常）。

## 5. A9 意见汇总（`domain/verdicts.py: aggregate_overall`）

- 任一 FAIL → overall=**PROBLEM**；无 FAIL 但有 WARN 或 SKIPPED → **PARTIAL**（附明细说明：
  「存在 WARN 项：…」「未出具结论的项：…」）；全 PASS → **PASS**；全 SKIPPED → PARTIAL+说明。
- `composite_model_version` 组合串：文本规则版本 + 各 prompt 版本，随 C-02 回调与落库。
- wire 层字段名保持既有约定（short/long 不带 `_measure` 后缀，见 `WIRE_FIELD_NAMES`）。

## 6. service 接线（`application/service.py`）

- `InitialReviewService` 持有 `CheckPipeline`（model_client/rustfs_fetcher/history_loader 可注入，
  默认真实装配）；`CheckRunner` 签名扩展为 `(exec_row, request|None)`——重放路径 request=None。
- C-02 回调载荷新增 `overall_note`（A9 aggregate_note）；`is_late` 由 Java 端按任务状态判定。

## 7. 装配回归（J0 教训，`tests/initial_review/test_assembly.py`）

- TestClient 真实 `create_app` + lifespan（不 mock DatasourceManager），连 local.yaml 的 PG；
  PG 不可达整文件 skip。
- 覆盖：C-01 → 执行 → 落库全链路；模型开关关闭时 A7/A8 SKIPPED、A6 照常；model_version 组合串入响应/落库。
- 真实模型冒烟：`scripts/wave2_real_model_smoke.py`（手动跑，需 qwen key 与 RustFS 在线）。

## 8. 验证

- `pytest tests/`：**350 passed / 2 failed（既有环境问题，与基线一致）/ 1 skipped**，无新增失败；
  本波次新增 62 个测试（pipeline 18 + similarity 11 + verdicts 5 + assembly 3 + service/conftest 扩展等）。
- `ruff check app tests scripts`：全绿（含顺手修复 agent_runs.py、j0 脚本存量 lint）。

## 9. 【待确认】与风险

- 【待确认 D-05】A6 四个相似度阈值为暂定值，W3 前需业务样本校准（config 可配）。
- 【待确认】历史相似度判定为任务书扩展（PRD §26.2 只明确同单互抄），history_fail/warn 已可配。
- 【风险】vision_model 默认 `Qwen2.5-VL-7B-Instruct` 依赖本地/网关可达；qwen-plus 走 DashScope key（local.yaml）。
- 【风险】base64 模式附件崩溃后不可重放（如实 DEGRADED）；生产建议统一走 object_key。
