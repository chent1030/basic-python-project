# Harness 合规修复开发计划

> **依据**：2026-09 按 [Harness工程标准.md](./Harness工程标准.md)（复刻自 yeasy《智能体 Harness 工程指南》）对 `basic-project/` 的全量架构审计。
> **审计结论摘要**：内核 `app/harness/kernel` 在运行时/编排/审批 HITL/故障语义/确定性恢复上高度符合标准（多项超出）；主要缺口集中在 **输出治理、记忆、评测、可观测 trace/指标、安全工程化、降级权限收缩** 六大领域，外加 1 个实锤配置违背（internal_trust）与 1 个架构双轨问题（业务绕过内核）。
> **条目引用**：本文所有修复项均引用标准文档条目 ID（如 MODEL-04），供追溯。

---

## 一、审计发现总览

| 领域 | 评级 | 主要符合点 | 主要缺口 |
|---|---|---|---|
| 运行时与编排 | ✅ 强 | 终止条件/检查点恢复/审批续跑/确定性重放/三级并发信号量/幂等重试契约 | 熔断器、漂移检测、token 前向估计 (RT-11/13/16/17) |
| 工具层与 MCP | ⚠️ 中 | 严格白名单/stdio 默认禁/路径防逃逸/PTC 约束 | 参数 schema 校验、危险命令检测、进度事件 (TOOL-06/07/02) |
| 模型集成与输出治理 | ❌ | Provider 抽象/配置驱动 fallback/resume 漂移检测/视觉能力门控 | 熔断、降级权限收缩、幻觉检测、推理预算 (MODEL-03~26) |
| 记忆 | ❌ | propose→人工审核留痕、审批编辑学习闭环 | 向量检索、整合管道、剪枝 (MEM-01~23 大部) |
| 安全 | ⚠️ 中 | 审批梯度/子代理继承/路径校验/凭据隔离 | 威胁模型、注入消毒、命令黑名单、日志脱敏 (SEC-01/05/16) |
| 可观测与评测 | ❌ | JSON 日志+RequestId、framework_events+SSE | trace/span、运行时指标、评测全线 (REL-02~07, EVAL-01~16) |
| 架构一致性 | ⚠️ | kernel DDD 分层、薄接入层 | 双轨 LLM 栈、construction_plan_review 绕过内核 (ARCH-26) |

**实锤违背**（详见 P0-1）：`config/cps.yaml` 提交了 `internal_trust: true`（提交 fd6917c），导致 `tests/harness/test_api_worker.py::test_api_auth_tenant_and_validation` 红（期望 401 实得 403），且本机无 token 请求获得全角色 Principal——同时违反标准 SEC-11/PRIN-01 与项目自家文档"tenant 只能取自签名声明"。

---

## 二、P0 修复项（本周，安全与正确性，预估合计 2~3 人日）

### P0-1 修复 internal_trust 配置违背 【SEC-11 / PRIN-01 / PRIN-10】 ✅ 已完成（波次 15）

**问题**：
- `config/cps.yaml`（提交 fd6917c 引入）含 `internal_trust: true`，而代码默认 `False`（`app/projects/cps/infrastructure/config.py`）。
- `tests/harness/test_api_worker.py:1-28` 的 `environment` fixture 未隔离 `CPS_CONFIG` 环境变量 → 测试读到仓库业务配置 → httpx 客户端 host=127.0.0.1 ∈ `trusted_networks` → `app/api/v1/endpoints/agent_runs.py:51-90` principal() 构造全角色 Principal（含 cps_admin）→ GET /agent-runs 缺 run_reader → 403（期望 401），测试失败。

**改动点**：
1. `config/cps.yaml`：删除 `internal_trust: true`（或改回 false）；如确需内网信任，将该配置移入 gitignore 的 `config/local.yaml`。
2. `tests/harness/test_api_worker.py` fixture：`monkeypatch.setenv("CPS_CONFIG", str(tmp_path / "cps.yaml"))` 写入最小安全配置（internal_trust=false, trusted_networks=[]），使框架测试与仓库业务配置解耦。
3. 新增测试：无 token + 客户端 IP 不在信任网段 → 必须 401；无 token + 在信任网段但 internal_trust=false → 必须 401；internal_trust=true 时合成 Principal 的角色集必须与文档声明一致。

**验收标准**：
- `.venv/bin/python -m pytest tests/harness -q` 全绿（含原失败用例）。
- `git grep -n "internal_trust: true" config/` 无结果。
- 新增 3 个鉴权用例通过。

**预估**：0.5 人日。**风险**：若 J 线联调依赖 localhost 免鉴权，需在部署侧 local.yaml 开启并在文档标注仅限隔离环境（SEC-10）。

### P0-2 参数级护栏中间件 【MODEL-11/12/14、SEC-04/16、PRIN-04】 ✅ 已完成（波次 15，WARN 模式灰度）

**问题**：`app/harness/kernel/infrastructure/middleware.py` 的 ExecutionAudit 只做工具名禁用检查（`policy.check_tool`）与事件审计，无参数校验、无注入检测、无相似工具建议；标准要求的六层门控缺③④⑥三层。

**改动点**（集中在一个新中间件 `ToolGuardrailMiddleware`，或扩展 ExecutionAudit）：
1. **参数 schema 校验**（MODEL-09/11 层③）：工具注册时声明 `args_schema`（pydantic/jsonschema），执行前校验；未知字段/类型不符/越界 → 拒绝执行并返回带字段定位的错误信息给模型自修正。
2. **危险模式扫描**（MODEL-14、SEC-16 部分）：对参数字符串扫描 `rm -rf`、`DROP TABLE`、`<script>`、`${jndi:`、`curl|sh` 等正则清单（清单外置 `config/guardrails.yaml`，SEC-21），命中 → FAIL + `tool.rejected` 审计事件。
3. **相似工具建议**（MODEL-12）：未知工具名用编辑距离（cutoff 0.6）给出最相近的已注册工具，拼进错误反馈。
4. 数值参数范围校验（SEC-19）：timeout/memory/file_size 类参数白名单范围。

**验收标准**：
- 新增 `tests/harness/test_guardrails.py`：注入参数命中→拒绝+事件；未知工具→返回相似名建议；schema 违规→带字段错误。
- 现有 `tests/harness`、`tests/cps` 回归全绿。
- 护栏规则在 `config/guardrails.yaml` 可配置且未知规则名启动时报错。

**预估**：1 人日。**风险**：误杀业务参数——先用 WARN 模式灰度一周（只告警不拦截），确认无误杀再切 FAIL。

### P0-3 降级权限收缩（authority shrink） 【MODEL-04/05/06】 ✅ 已完成（波次 15）

**问题**：`app/harness/kernel/infrastructure/deepagents.py:126-134` 的 `ModelFallbackMiddleware` 是纯可用性回退——fallback 模型执行时不标记 degraded，高价值 CPS 判定在弱模型（如 qwen-turbo 替代 qwen-plus）上照常自动出结果，上层无法感知降级事实。

**改动点**：
1. `infrastructure/models.py` 的 `ModelProfile` 增加 `authority` 档位字段（如 `may_auto_decide` / `may_execute_external` / `may_recommend` 布尔集，默认全 True）。
2. fallback 中间件：切换到 fallback 时比对主/备 authority，收缩生效并在 invocation 记录写入 `degraded=true` + 使用的 profile 名（MODEL-06 运行记录）。
3. `app/projects/cps/infrastructure/agents.py`：判定类 agent（整改判定/复核类）配置 `degrade_policy=block`——回退期间 invocation 进入 Waiting 转 `Approval.before()` 人工通道，显式提示"自动决策当前不可用"；提取/摘要/草稿类 `degrade_policy=allow`。
4. run 事件流新增 `model.degraded` 事件。

**验收标准**：
- 单测：主模型故障→fallback 触发→判定类 agent 转人工审批；提取类 agent 继续执行且 invocation 标 degraded。
- `.venv/bin/python -m pytest tests/harness tests/cps -q` 全绿。

**预估**：1 人日。**依赖**：P0-2 无依赖，可并行。

---

## 三、P1 修复项（近期 2~4 周，可靠性与质量）

### P1-1 Provider 级熔断器 【MODEL-03 / RT-11 / PRIN-12】

- **改动**：`infrastructure/models.py` 每 provider 独立三态熔断器（failure_threshold=5、recovery_timeout=60s、half-open 试探 1 次成功才 closed）；`Models.snapshot()`/选择逻辑消费熔断状态，全部 open 时显式抛错而非静默全试。
- **验收**：单测模拟连续失败→open→冷却→half-open 恢复；全 open 时 submit 返回明确错误。
- **预估**：1 人日。

### P1-2 记忆子系统升级 【MEM-01/03/04/09/19/20/21】

- **改动**：
  1. `infrastructure/store.py` ScopedStore 增加可选 embedding 后端接口（起步 in-process hnswlib；规模>50 万条切 pgvector，与 DEPLOY.md 已有规划对齐），维度可配置（MEM-03）。
  2. `application/services.py` Memory.propose 时同步写向量索引（异步、失败不阻塞 propose，MEM-11）；search 升级为混合检索：关键词+向量，双路命中优先（MEM-09）。
  3. Observer 增加整合触发门（>24h 或 >5 个新 run，MEM-19），触发四阶段管道 Orient→Gather→Consolidate→Prune（MEM-20）；**产出仍走 Memory.propose 人工审核**，复用现有信任模型，不自动写入。
  4. Prune 策略：expiry 过期删除、相似度>0.95 去重、低置信清理（MEM-21）。
- **验收**：单测覆盖混合检索排序、整合触发门、prune 三策略；现有 memory 相关测试回归绿。
- **预估**：3 人日。**风险**：embedding 服务选型需与 Java 侧 embedding-service（E 线）对齐，避免重复建设。

### P1-3 评测基建 【EVAL-01~09】

- **改动**：新建 `evals/` 目录：
  1. `evals/datasets/`：金标巡检样例集（首批 30~50 条，覆盖合规/不合规/边界）。
  2. `evals/run_eval.py`：跑样例集并输出轨迹指标 JSON（任务成功率/步数/工具调用数/token/耗时）。
  3. `evals/baselines/*.json`：指标基线文件；`evals/check_regression.py`：±5% 方向性阈值报警（EVAL-08），进 CI（GitHub Actions 或现有流水线）。
  4. 保留双轨：Mock 轨（现有 tests/harness 77 个用例）+ 真实模型轨（复用 scripts/ smoke 脚本改造，EVAL-05）。
- **验收**：CI 中 `python evals/check_regression.py` 以基线比对通过；文档写明如何新增样例与更新基线。
- **预估**：3 人日。**依赖**：无。

### P1-4 可观测补齐：trace_id + 运行时指标 【REL-02/03/04、ARCH-23、EVAL-16】

- **改动**：
  1. run 提交时生成 trace_id，贯穿 invocation→model call→tool call 事件（framework_events 增加 trace_id 列，SSE 透出）。
  2. 新增 `/agent-metrics`（或复用 cps.py /metrics 分节）：从 framework_events 聚合导出 Prometheus 文本格式——`agent_runs_total{tenant,status}`、`agent_tool_calls_total{tool,success}`、`agent_tokens_total{model}`、`agent_run_duration_seconds` 直方图（REL-03）。
  3. `app/core/logging_config.py` JSON 日志增加 credential 正则脱敏过滤器（SEC-06）。
- **验收**：`curl /agent-metrics` 返回合法 Prometheus 文本；日志中注入假 key 后被脱敏；单测覆盖聚合正确性。
- **预估**：2 人日。

### P1-5 旧 llm.py 输出治理兜底 【MODEL-07/09/10、ARCH-05】

- **改动**：`app/services/llm.py`（在 P2-1 收编完成前的过渡加固）：
  1. `invoke` 增加可选 `schema` 参数 → `with_structured_output` 强制结构化（MODEL-09）。
  2. 解析失败→带错误信息重试 1 次（ARCH-05 四步防御第 2 步）。
  3. 检测 `finish_reason/max_tokens` 截断→抛显式错误而非返回半截文本（MODEL-10）。
- **验收**：`tests/` 新增 llm 服务用例：schema 违例重试、截断检测；现有 projects 测试回归绿。
- **预估**：1.5 人日。

---

## 四、P2 修复项（中期 1~2 月，架构收敛）

### P2-1 收编双轨架构 【ARCH-26 / DEF-02，最高架构优先级】

- **问题**：`app/projects/construction_plan_review/orchestrator.py:312` 直接 `from deepagents import create_deep_agent` 绕过内核（无审批/预算/租约/审计）；`room_checks` 自有 router+vision_client 直调 LLM；`initial_review` 仅用 digest 工具。
- **改动**：
  1. construction_plan_review 迁移为内核注册的 workflow/Step（享受审批与预算治理）；过渡期至少套 `ExecutionAudit`+`ModelCallLimitMiddleware`。
  2. 宣布 `app/services/llm.py` 为 legacy 冻结：新业务一律走 kernel；在 `docs/Agent框架开发与部署指南.md` 标注弃用路线。
  3. `app/harness/kernel/bootstrap.py:41-44` 硬编码的默认模块列表（`app.projects.agent_examples,app.projects.cps.bootstrap`）移到 `config/agents.yaml`。
- **验收**：`grep -rn "from deepagents import" app/projects/` 无结果（仅 kernel infrastructure 内出现）；回归测试全绿。
- **预估**：3~5 人日（含业务回归）。

### P2-2 提示词模块化与缓存感知 【PROD-01~05】

- **改动**：system_prompt 外置 `config/prompts/*.md`；四层组装（identity/capabilities/knowledge/context）+ 每层 cacheable/priority 标记；复用已有 `prompt_hash` 快照补缓存边界元数据（cache_key=sha256(静态部分)）。变量替换只发生在动态层，保证前缀缓存命中（PROD-02）。
- **验收**：同会话两次调用的静态前缀字节一致（单测断言）；提示词变更触发 `tests/` 中的提示词回归用例。
- **预估**：2 人日。

### P2-3 最小特性开关 【PROD-19/20 / REL-07 / PRIN-15】

- **改动**：`config/agents.yaml` 增加 `features{}` 节（如 `observer_enabled`、`mcp_enabled`、`guardrails_mode: warn|fail`）；启动加载校验未知 flag 报错；支持免部署重启生效（先做重启级，远程热更后续）。
- **验收**：单测覆盖 flag 开关 observer/mcp 的启用禁用路径。
- **预估**：1 人日。

### P2-4 威胁模型文档 【SEC-01】

- **改动**：新建 `docs/threat-model.md`，按标准 11 类威胁（恶意工具调用/路径穿越/权限提升/沙箱逃逸/提示注入劫持/凭据外泄/资源耗尽/供应链/间接注入/智能体间信任滥用/记忆投毒）逐项标注：现有缓解（引用 file:line）、风险评分（影响×可能性÷检测难度）、责任人与跟进项。
- **验收**：评审通过并入库；每类威胁均有"已缓解/部分/缺失"三态标注。
- **预估**：1 人日。

---

## 五、已知遗留与裁决记录（不在本计划内新开工作）

| 事项 | 状态 | 说明 |
|---|---|---|
| `config/agents.yaml` 明文 api_key 已入 git 历史 | 用户已裁决（波次 1）暂不轮换 | 风险仍在累积，建议在密钥轮换窗口一并处理；届时需 `git filter-repo` 或接受历史泄露事实并仅撤销旧 key |
| J 线联调依赖 localhost 免鉴权 | 待确认 | 若确认依赖，按 SEC-10 仅在隔离环境 local.yaml 开启 internal_trust |
| DEPLOY.md 提及的 pgvector/agents.enabled | 规划中 | 由 P1-2/P2-3 落地，不另开项 |

## 六、里程碑建议（沿用波次制）

- **波次 15（P0）**：P0-1 → P0-2（WARN 模式）→ P0-3；出口标准：tests/harness 全绿、护栏 WARN 上线、降级转人工生效。**✅ 已达成（2026-09-27）：tests/harness+tests/cps 115 passed；ruff 触达面（kernel/cps/tests/config）0 错误；新增 tests/harness/test_guardrails.py(10) 与 test_degrade.py(5)。**
- **波次 16（P1 上）**：P1-1 + P1-4 + P1-5；出口标准：熔断单测绿、/agent-metrics 可抓取、legacy 输出治理生效。
- **波次 17（P1 下）**：P1-2 + P1-3；出口标准：混合检索可用、evals 进 CI。
- **波次 18+（P2）**：P2-1 → P2-2 → P2-3 → P2-4；出口标准：业务全量走内核、双轨退役。

每波次出口前跑：`.venv/bin/python -m pytest tests/harness tests/cps -q` + `ruff check .`（沿用波次 13 的验收基线）。
