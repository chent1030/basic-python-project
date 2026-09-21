# Agent 框架开发与部署指南

日期：2026-09-11。对应根项目 `basic-project` 中的 `app.harness.kernel`，不是独立的 CPS 原型目录。

## 1. 交付范围与边界

这是工程师通过 Python 编写业务组合的框架，不提供前端 Agent 配置器。运行 API 只接收业务输入、审批决定和运维操作。

- Single、Sequence、Parallel、Condition、Loop 都由程序推进，不创建主 Agent。只有显式 Supervisor 或原生子 Agent 父代理会进行模型委派。
- 审批可关闭、继承、条件启用、调用前启用或输出后启用；工具审批是另一层独立策略。
- 采用 SQLite WAL 事务存储运行、调用、审批、事件、邮箱、产物和业务记忆；LangGraph 使用独立持久 Checkpoint。
- 默认部署是**单主机、共享本地数据目录的多进程服务**。并发槽与运行租约在数据库中协调。不要把 SQLite 文件放在不可靠的网络文件系统上，更不要把这个实现描述为已通过多机高可用认证。
- MCP、远程 Agent、Docker 的适配代码已经提供，但需要实际服务、凭据或镜像。离线测试不等于这些外部服务的生产联调。
- 不承诺任意外部工具 exactly-once。副作用结果不确定时进入 `uncertain`，需要对账，不能自动假定失败并重复执行。

## 2. DDD 代码分层

| 层 | 目录 | 职责 |
| --- | --- | --- |
| 领域 | `app/harness/kernel/domain` | Agent 定义、组合、Scope、审批/重试/部署策略、通信与执行端口 |
| 应用 | `app/harness/kernel/application` | 运行用例、顺序/并行/动态推进、审批、恢复、Worker、观察、记忆审核 |
| 基础设施 | `app/harness/kernel/infrastructure` | SQLite、DeepAgents、模型提供商、Checkpoint/Store、MCP、远程图、解释器和沙箱 |
| 装配 | `app/harness/kernel/bootstrap.py` | 加载 YAML、导入工程师模块、注册业务组合 |
| 接口 | `app/api/v1/endpoints/agent_runs.py`、`app/harness/kernel/__main__.py` | 鉴权 HTTP/SSE、命令行 |
| 业务示例 | `app/projects/agent_examples.py`、`app/projects/agent_advanced.py` | 角色边界、业务契约和组合，不在领域内核写 CPS 业务 |

旧 `app.harness.BaseSingleAgent` 和文档审核依赖的上下文、工具、通讯及兼容后端仍然保留，通过惰性导出避免新内核加载旧实现。旧复合拓扑、聚合器、演示脚本和无调用的预处理/章节模块已移除，兼容范围与迁移说明见根目录 `HARNESS.md`。**旧 Harness 不自动获得新内核的持久审批、隔离和租约保证。** 新业务统一从 `app.harness.kernel` 导入，迁移旧业务时应显式重写注册模块。

## 3. uv 安装与启动

```bash
uv sync --locked --extra dev
uv run --locked agent-framework workflows
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
```

FastAPI lifespan 默认启动框架和嵌入式 Worker。已有数据源、LLM、HTTP 和调度器启动行为保留。框架注册不访问外部模型，实际运行才请求模型服务。

开发环境直接运行：

```bash
uv run --locked agent-framework --tenant local run document_analysis \
  --task document_001 --key first_run --input examples/agent-input.json
```

执行前先把 `config/agents.yaml` 的模型名称、地址调整为实际部署。示例的 `127.0.0.1:8001/v1` 不是框架自带模型服务；不可用时会真实失败，不返回演示报告。

分离 API 与 Worker：

```bash
AGENT_EMBEDDED_WORKER=false uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
uv run --locked agent-framework worker
```

二者必须使用同一份代码、模型配置、策略和 `AGENT_DATA_DIR`。不要混用不同限额的 Worker；调整限额应作为一致的部署变更。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AGENT_CONFIG` | `config/agents.yaml` | 提供商和每 Agent 模型配置 |
| `AGENT_DATA_DIR` | `.agent-data` | 私有持久数据根目录 |
| `AGENT_WORKFLOW_MODULES` | `app.projects.agent_examples,app.projects.cps.bootstrap` | 逗号分隔的工程师注册模块；显式设置会覆盖默认列表 |
| `AGENT_RUNTIME_ENABLED` | `true` | 是否装配新框架 |
| `AGENT_EMBEDDED_WORKER` | `true` | API 进程是否运行 Worker |
| `AGENT_WORKER_CONCURRENCY` | `4` | 同一 Worker 的并行运行数量 |

命令行属于受信任的本机工程师工具，能直接访问本机数据库，不是远程鉴权入口。不要向不可信用户开放服务器 Shell。

## 4. 模型与提供商配置

`providers` 配置 `kind`、`base_url`、`api_key`、可选的 `api_key_env`、`timeout` 和 `max_retries`。`api_key` 可以直接写入 YAML；如果同时配置 `api_key_env`，环境变量优先。内置 OpenAI-compatible、Anthropic 和 Ollama 三种实际模型适配。

`models` 配置模型名、参数、能力声明以及可选 `fallbacks`。`agents` 按 Agent 名覆盖 Python 定义的 `model_profile`。

```yaml
providers:
  inference:
    kind: openai
    base_url: http://127.0.0.1:8001/v1
    api_key: ""
    timeout: 120
    max_retries: 2
models:
  small:
    provider: inference
    model: deployed-small-model
    parameters:
      temperature: 0.1
      max_tokens: 4096
    capabilities: [tools, structured_output]
  large:
    provider: inference
    model: deployed-large-model
    parameters:
      temperature: 0.2
      max_tokens: 8192
    capabilities: [tools, structured_output]
    fallbacks: [small]
agents:
  extract_facts: small
  identify_risks: large
  coordinator: large
```

模型名称必须是真实服务已经部署的名字。参数量不是框架自动识别出的保证：工程师用 profiles 把复杂角色映射到大模型，把轻量角色映射到小模型。

能力声明会在选择和降级前校验，但声明不能代替提供商实测。降级仅发生在模型调用边界，不把整个业务步骤连同已执行工具重跑。

禁止在模型 URL、普通参数或默认 HTTP 头中夹带密钥。密钥通过环境变量提供，不进入模型快照。运行提交时冻结模型配置；暂停任务恢复时配置不一致会拒绝继续，而不是悄悄换模型。需要换模型重跑时创建新的 run。

旧业务使用的 `config/config.yaml` 保持兼容，不与新框架配置隐式互相覆盖。

## 5. 工程师代码定义与组合

注册模块必须导出 `register(runtime)`，支持同步或异步。生产代码应明确版本、角色边界、输入输出 Schema、工具和重试契约。完整示例见 `app/projects/agent_examples.py`。

```python
from app.harness.kernel import Input, Output, Parallel, Sequence, Step
from app.projects.agent_examples import register

def register_project(runtime, extract, risk, report):
    flow = Sequence(
        Parallel(
            Step("facts", extract, Input()),
            Step("risks", risk, Input()),
            max_concurrency=2,
            failure_policy="require_all",
        ),
        Step("report", report, {"facts": Output("facts"), "risks": Output("risks")}),
    )
    runtime.register("my_document_analysis", flow, version="1.0.0")
```

这个辅助函数的 Agent 参数由项目注册模块提供，不是框架内置对象。也可继承 `DeepAgent` 并声明类属性、`prompt_file`，再交给 Step 转换为定义。

- `Input("document")` 读取组合入口字段；`Output("facts", "findings")` 读取已完成步骤字段。
- Sequence 不隐式猜测前一步输出，也不调用协调模型。通过显式绑定传值。
- Parallel 只合并分支的完成输出；重复导出 ID 和前向引用在注册时拒绝。
- `allow_partial` 返回 `results` 和 `errors`，不会把失败分支伪装成正常结果。
- Condition 用工程师谓词选分支，Loop 用工程师停止条件和有限迭代预算执行；二者以自己的 `id` 向外导出结果。
- Supervisor 从明确的成员白名单中提出 Decision，可以多轮、并行委派，也可以嵌在静态 Sequence 中。
- 普通 Python handler 是受信任代码。异步 handler 适合 I/O；同步 handler 在线程中执行，线程无法安全强杀，因此有副作用的 handler 必须接受取消后对账的约束。

## 6. 可选审批与人工改派

| 策略 | 行为 |
| --- | --- |
| `Approval.none()` | 无框架级审核 |
| `Approval.before()` | 调用前冻结输入并等待 |
| `Approval.after()` | 输出已持久化，人工审核后继续，不重复模型执行 |
| `Approval.every_delegation()` | Supervisor 每份委派方案均审核 |
| `Approval(mode="inherit")` | 继承外层组合或部署默认策略 |
| `condition=callable` | 仅在工程师规则成立时审核 |

Agent 默认 none 是明确关闭，不等于 inherit。若需继承 `Sequence(approval=...)`，Agent 或 Step 必须选择 inherit。部署 `require_approval=True` 与显式 none 冲突会报错，不能绕过部署要求。

无主 Agent 的静态步骤也能改派：

```python
from app.harness.kernel import Approval, Step

def reviewable_step(small_agent, large_agent):
    return Step("analyze", small_agent, approval=Approval.before(), alternatives=(large_agent,))
```

该步骤的审核载荷为 `{"agent": "目标名称", "inputs": {...}}`。人工只能选择工程师提供的 alternatives，修改后的输入重新按目标 Agent Schema 校验。Supervisor 审核载荷为 Decision，人工可以修改白名单成员、参数或并行调用列表。

改派决定与记忆候选在同一数据库事务中记录：原建议、最终建议、审核人和来源审批都保留。候选不会立即用于后续决策，需要 `memory_reviewer` 再审核。

工具级 HITL 使用 `interrupt_on`。SDK 的 `approve`、`edit`、`reject` 映射到同一持久审批入口；工具名称不允许在编辑参数时偷偷替换。多中断使用 `decisions_by_interrupt` 按 ID 精确对应。自定义图中断使用 `responses` 映射。

`accept=false` 是拒绝本次框架审批并终止该动作；希望工具返回“被拒绝”后让模型继续推理，应提交 `accept=true` 和 `edited.decisions=[{"type":"reject","message":"原因"}]`。

## 7. DeepAgents 能力装配

### 7.1 原生、fork 与动态子 Agent

使用框架 `SubAgent(definition, description, input_mapper=...)`，不是直接塞 SDK 子 Agent 字典。所有委派进入统一 Invocation、并发预算、审批和私有工作区。

默认不添加 general-purpose 子 Agent。显式 `general_purpose=True` 才创建经过相同运行入口管理的子定义。主 Agent 并非这项能力的全局前提。

`mode="fork", allow_context_fork=True` 才允许继承会话；框架采用受控 **CompiledSubAgent fork**，继承历史消息，子 Agent 保留自己的角色提示词和私有目录。它不是把父权限、父私有文件和父系统提示词全部复制给子 Agent。带子 skills 的 fork 会拒绝。

`CodeInterpreterMiddleware` 已安装实际 QuickJS 扩展。JavaScript `task({description, subagentType})` 的委派同样经过框架审批；已覆盖中断恢复测试。

SDK 的普通 PTC `tools.xxx()` 不支持逐工具 HITL。框架对此不做虚假封装：需要 HITL 的工具不能走 PTC，PTC 也不能与可中断子委派混在同一解释器任务中，以免恢复 eval 时重复之前的副作用。使用正常工具路径、受控子 Agent 或单独的解释器 Agent。PTC 支持显式 BaseTool 白名单、审计和有限调用预算，禁止关闭预算。

### 7.2 Skills、记忆文件与工作区

`Resources` 将工程师指定的 Skills 和参考记忆文件挂载为只读路径；所有写、改、删和上传入口均拒绝修改挂载内容。挂载源内容进入定义快照，内容变更后不能用旧配置直接恢复任务。

默认运行目录：

```text
.agent-data/
  state.sqlite
  checkpoints/<scope-hash>.sqlite
  workspaces/<tenant>/<task>/<run>/<invocation>/<attempt>/
```

Checkpoint 位于模型可见工作区之外。临时文件、摘要和大输出卸载由当前 Backend 保存，不会自动进入长期记忆。

`Resources(persisted_files=True)` 额外挂载 `/persisted/`，使用租户和 invocation 限定的 StoreBackend。该文件 Store 是执行持久状态，不是经过审核的业务事实库。

### 7.3 自定义图、状态、中间件与缓存

- `CompiledAgent(graph_factory)` 接收提供的 checkpointer，必须使用它编译图；支持自定义中断及恢复。
- `state_schema`、`context_schema`、`context_factory`、`initial_state_factory` 支持开发工程师自定义图状态和服务端上下文。
- `output_schema` 传给 DeepAgents，并在应用层再次校验；缺少结构化结果视为失败。
- `middleware` 支持 SDK 中间件，已接入模型/工具计数、用量审计，工程师可加入 PII 处理、摘要策略和其他官方中间件。
- Harness Profile 通过 `DeepAgentsEngine(profile=...)` 提供，框架仍强制接管隐藏子 Agent 的启用方式。
- `ScopedCache` 按 tenant/task/run/invocation/version 隔离。缓存是可选图能力，不缓存审批授权，也不默认缓存有副作用的工具；自定义图可显式配置 LangGraph CachePolicy。

### 7.4 MCP、远程 Agent 和沙箱

- `MCPTools` 用实际 MCP SDK 建立并关闭会话，只暴露 `server__tool` 白名单；环境变量头和凭据由工程师配置。stdio 默认禁止，因为它会启动宿主机进程。
- `RemoteAgent` 用 LangGraph Agent Protocol 创建持久远程 thread/run、轮询、收集、中断恢复和请求取消。提交应答丢失会进入不确定状态，不盲目重复创建。远程服务自身的鉴权和工具策略必须由其部署保证，本地框架不能替远程服务强加沙箱。
- `DockerBackend` 要求镜像 digest 固定，默认关闭网络、只读容器根目录、丢弃 capabilities、限制内存/CPU/PID，并只挂载本次调用工作区。它不是 LocalShellBackend，也不会默认开放宿主机 Shell。
- 部署策略默认禁止 `execute`。启用 Docker 执行时，工程师还必须显式调整 `DeploymentPolicy.forbidden_tools`，并决定 execute 是否逐次审批。

加载高级示例：

```bash
AGENT_WORKFLOW_MODULES=app.projects.agent_examples,app.projects.agent_advanced \
  uv run --locked agent-framework workflows
```

高级模块只有在提供对应环境变量后才注册远程/MCP/沙箱示例。服务名称、远程图 ID、MCP 的 `documents__search` 工具和 Docker 镜像都需要与真实部署匹配。

## 8. Agent 通信与工作区共享

- 请求／响应：`await context.agents.request("name", payload, key="stable_request_key")`。调用者必须在 `delegates` 中声明目标。循环请求和深度超限会拒绝；等待子调用时释放父执行槽，避免单槽死锁。
- 邮箱：`context.messages.send(recipient_invocation_id, payload, key="stable_key")`。持久去重、过期时间、显式消费状态；运行中的 Agent 在下一模型安全点接收资料，成功消费后确认。
- 产物：`context.artifacts.publish(name, content, expected_version=0, readers=("*",))`。默认不共享，`*` 只代表当前 run 的读取者；跨 run 必须显式 grant。
- 工作区：`context.workspace.read/write` 仅在当前 Scope，禁止路径逃逸与符号链接。并行分支入口输入也会深拷贝，不能通过修改共享字典污染其他分支。
- 事件：只用于监控、审计和观察，不把事件总线当作 Agent 请求响应。

自定义 Python 工具、注册模块、后端和中间件都是**受信任的服务器代码**，不是恶意 Python 的隔离容器。不要声称 Python 对象级权限能够阻止恶意插件调用 `os`。不可信执行应放在沙箱或独立服务中。

## 9. 观察与长期记忆

`Observer(repository, name, handler)` 可旁路消费整条链路事件，消费者游标持久化，失败不会改变业务执行结果。处理器必须用事件 ID 实现幂等，因为交付是 at-least-once。

`attach_memory_observer(runtime)` 注册实际的观察 Agent：终态运行事件触发独立观察 run，输出经 Schema 校验后转为候选记忆。观察 run 标记为 observer，不再次触发观察循环。可给观察 Agent 配置独立的小模型。

业务记忆具有 namespace、来源 run、证据游标、版本、审核人和可选过期时间。只检索 accepted 且未过期的条目；Supervisor 获取当前业务 workflow namespace 内的已审核记忆作为参考，不把记忆当成授权。

对观察 Agent 应使用低权限工具集；现成观察适配器禁止业务工具和再委派。观察结果不能自动修改流程代码、审批策略或模型配置。

## 10. API、监控和鉴权

前缀：`/api/v1/agent-runs`。接口要求签名有效的 access JWT，且具有 `sub`、`tenant_id`、`roles`、`exp`。tenant 只能取自签名声明，不能用请求体或 Header 自报。refresh token 不能访问。

| 接口 | 用途 |
| --- | --- |
| `GET /workflows` | 查询工程师注册的场景 |
| `POST /`、`GET /`、`GET /{run}` | 幂等提交、列表、状态和定义/模型快照 |
| `GET /{run}/invocations` | 调用树、尝试、输入输出与中断状态 |
| `GET /{run}/events?after=N` | 持久事件补齐 |
| `GET /{run}/events?stream=true` | SSE，支持 Last-Event-ID，令牌到期断流 |
| `GET /{run}/approvals`、`POST /{run}/approvals/{id}` | 审核、编辑及版本冲突检测 |
| `POST /{run}/cancel` | 请求取消，不伪称撤销已经发生的外部动作 |
| `POST /{run}/invocations/{id}/reconcile` | 对账后提供已确认输出或显式授权重试 |
| `GET/POST /{run}/messages` | 运行内邮箱与补充资料 |
| `GET /{run}/artifacts`、`GET /{run}/artifacts/{id}` | 产物元数据与授权下载 |
| `POST /{run}/artifacts/{id}/grants` | 跨 run 显式复用授权 |
| `GET /memories?namespace=...`、`POST /memories/{id}/review` | 长期记忆审核 |

角色分别为 `run_reader`、`run_operator`、`approver`、`memory_reviewer`。审批还要满足该审批自身的 roles。运维角色具有所属租户的运行管理权限；该实现不是按个人 owner 限制的私有聊天产品。

监控默认记录状态、工具名称、用量、节点及父子关联，**不把原始 token 文本或工具参数自动广播到事件流**。有业务权限的用户可查询持久输入输出；不要将监控接口开放给匿名用户。

## 11. 故障恢复与运维

1. 相同 tenant/task/idempotency key 与相同请求返回同一 run；改动请求复用 key 会冲突。
2. 完成节点只读取持久输出，审批后恢复不重新执行完成步骤。
3. 原生/自定义图中断保留 attempt 和 Checkpoint；审核结果对应特定中断 ID。
4. 进程崩溃时，租约到期后才能由另一个 Worker 接管。尚在执行的调用标记为 uncertain，不能猜测外部副作用是否已成功。
5. 对账时先检查真实下游系统，再提供确定输出，或者明确允许重试。业务重试必须声明 `Retry(idempotent=True, attempts=...)`，并由工具使用下游幂等键。
6. `cancelled` 表示框架停止推进；同时检查 `has_uncertain_effects` 及远程取消状态。取消不代表网络服务已回滚。
7. 定义、模型配置、挂载文件或部署策略变化会阻止旧任务静默恢复；保留对应发布版本，或新建 run。

备份时停止 Worker，备份整个私有数据目录，包括 state.sqlite、Checkpoint、工作区和配置版本。在线单独复制 SQLite 主文件可能遗漏 WAL，不能作为完整备份流程。

数据包含业务材料，应放在加密磁盘并限制 OS 权限。远程存储、数据保留周期、备份恢复演练、服务 SLA 和模型质量阈值需要按实际部署确定；本框架不虚构这些生产验收结果。

## 12. 验证命令

```bash
uv run --locked pytest tests/harness -q
uv run --locked pytest tests -q
uv run --locked ruff check app/harness/kernel app/api/v1/endpoints/agent_runs.py \
  app/projects/agent_examples.py app/projects/agent_advanced.py tests/harness
uv build
```

现有 `RUN_DOC_REVIEW_LIVE=1` 测试会访问真实文件、OCR 和 LLM，默认跳过。新增框架测试明确使用离线模型和 HTTP/远程协议替身，不在生产实现中提供伪造结果。逐项能力对应关系见 `docs/Agent框架能力验收清单.md`。
