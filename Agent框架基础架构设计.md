# basic-project Agent 框架基础架构设计

| 项目 | 内容 |
| --- | --- |
| 文档版本 | 0.2.0 |
| 日期 | 2026-09-11 |
| 状态 | 架构设计稿，待评审 |
| 面向读者 | 后端开发工程师、架构师、测试及运维工程师 |
| 目标项目 | basic-project |
| 技术方向 | Python、uv、DDD、DeepAgents、LangGraph Runtime、LangChain |

> 本文整理已讨论的框架目标与设计约束，不是已实现能力清单。代码片段和配置示例表达拟议开发接口；不承诺是当前仓库或第三方 SDK 的现成 API。具体 SDK 适配应在锁定依赖版本后通过契约测试验证。

> 2026-09-11 实施补充：根项目已新增 `app.harness.kernel`。实际接口、部署方式和安全限制以 `docs/Agent框架开发与部署指南.md` 为准；逐项实现与测试边界见 `docs/Agent框架能力验收清单.md`。本设计稿中的拟议语法不自动等同于最终接口，外部服务的生产联调也不因代码交付而视为完成。

## 1. 架构结论

在 basic-project 内建设一个面向工程师的、代码优先的 Agent 开发框架：工程师通过 Python 定义 Agent、工具和执行组合，通过 YAML 选择提供商、模型与部署参数。框架提供统一执行、通信、状态管理、工作区隔离和监控，并允许按场景选择审批、重试、观察及记忆策略。

核心划分为：

```text
Agent 定义 + 执行组合 + 通信机制 + 运行策略 + 隔离与持久化
```

框架不要求业务人员在前端配置 Agent，不建设低代码拖拽设计器。前端或其他交互渠道可以承接业务输入、人工确认、产物查看和运行监控，但不负责定义框架。

DeepAgents 是执行引擎，不是业务领域模型。LangGraph Runtime 用于承接所选引擎的运行状态、暂停与恢复；主 Agent 动态选择业务动作，或者由工程师显式定义步骤关系。不得把所有业务强制改造成固定 Agent 顺序。

**主 Agent 不是框架的必选组件。** 只有工程师显式使用 Supervisor 时才装配主 Agent；Single、Sequence、Parallel 和代码条件分支由普通程序执行，不创建隐藏的协调 Agent，也不额外调用模型来决定已确定的步骤。

## 2. 目标与非目标

### 2.1 目标

1. 工程师能够定义单 Agent，并自由组合顺序、并行、动态委派及嵌套能力。
2. 每个 Agent 能独立选择模型地址、提供商、模型名称、调用参数、工具、Skills 和提示词。
3. 审批、观察和长期记忆均为可选策略；没有审批的业务能自动运行。
4. 顺序结果传递、跨 Agent 请求、消息通知、共享产物使用统一通信契约。
5. 工作区按租户、业务任务、执行批次、调用及尝试隔离，避免串任务和并发覆盖。
6. 运行状态、调用树、工具事件、审批等待、分支失败和产物均可查询和追踪。
7. 可靠运行模式支持持久化、进程重启恢复、幂等操作和事件重放。
8. CPS、文档审核等业务通过自己的领域对象和工具接入，不污染通用框架。
9. 完整纳入 DeepAgents 的能力体系，区分原生能力、LangChain/LangGraph 运行时能力、可选扩展和框架补充能力；工程师按需组合，不能将“完整支持”误解为“每次运行全部开启”。

### 2.2 非目标

- 不以 YAML 代替所有 Python 业务代码。
- 不把“所有 Agent 必须人工确认”写成框架全局规则。
- 不让主 Agent 绕过工具权限、工作区隔离和资源限制。
- 不默认开放 Shell、任意文件、网络或所有子 Agent 能力。
- 不把共享工作区直接当作跨任务长期记忆。
- 不把演示结果、硬编码统计或接口可启动视为完整业务验收。
- 不在首版同时建设多个功能等价的执行引擎；保留端口，优先实现 DeepAgents 适配。

## 3. 术语与身份

| 标识或概念 | 定义 |
| --- | --- |
| AgentDefinition | Agent 的不可变版本定义，包括角色、模型引用、工具和输入输出契约 |
| Composition | 工程师定义的执行结构，如 Sequence、Parallel、Supervisor |
| tenant_id | 数据与权限归属组织；单组织部署也保留逻辑作用域 |
| task_id | 长期业务任务，如一次巡检、一份待审核文档 |
| run_id | 一次执行批次；主动重新执行创建新 run，暂停恢复通常保留原 run |
| invocation_id | 一次具体 Agent 委派；同一个 Agent 可以在一个 run 中被多次调用 |
| attempt_id | 一次执行尝试；失败后的重试创建新的 attempt |
| dispatch_id | 一份待执行调度建议，可包含一个或多个候选调用 |
| approval_id | 对特定调用、输入及策略版本作出的审批记录 |
| artifact_id | 可引用、可版本追踪的文件或结构化产物 |
| input_snapshot_id | 本次调用使用的不可变输入版本 |
| checkpoint | 引擎恢复所需执行快照，不等于业务事实或长期记忆 |

业务任务、运行批次、调用和重试不可共用一个含义模糊的 session_id。外部 session_id 可以作为关联信息，但不替代上述主键。

## 4. 总体架构

```text
开发工程师
  ├─ Python：Agent / Tool / Composition / Policy
  └─ YAML：Provider / Model / Agent 参数 / 部署配置
                         │
业务 API / CLI / 定时任务 / 消息触发
                         │
                  框架应用服务
       启动 / 委派 / 审批 / 恢复 / 取消 / 查询
                         │
                    运行协调器
      解析组合 → 校验输入 → 应用策略 → 派发执行
                         │
        ┌────────────────┼────────────────┐
      Single           Sequence        Supervisor
                         │             动态提出调用
                      Parallel              │
                         └─────────────┬─────┘
                                统一 Invocation 入口
                                       │
                           DeepAgents 执行适配器
                                       │
                  模型 / 工具 / Skills / 工作区 / Checkpoint

横切能力：身份与权限、作用域隔离、事件、超时、限流、追踪
可选能力：人工审批、重试、观察 Agent、长期记忆
持久化：运行数据库、Outbox、事件流、对象存储、Checkpoint 存储
```

### 4.1 控制权划分

- 运行协调器是确定性的程序服务，不是主 Agent，不需要自己的模型或提示词。
- 工程师定义允许的组合结构、能力范围和策略。
- 主 Agent 在 Supervisor 模式中提出下一步业务动作，不负责执行授权。
- 应用服务校验输入、权限、预算及审批，并驱动执行。
- 执行适配器调用 DeepAgents，转译输出、中断和事件，不承担 CPS 业务规则。
- 业务应用决定问题确认、整改验收、报告发布和正式入库。

### 4.2 最小调用链

```text
业务请求 → 输入快照 → 有效配置快照 → Run
    → 组合解释（仅 Supervisor 内请求主 Agent 建议）→ Invocation 校验
    → 可选审批 → attempt 执行 → 输出校验 → 产物发布
    → 下游步骤或下一轮决策 → 业务收尾
```

### 4.3 无主 Agent 与有主 Agent 的模式

| 执行结构 | 是否需要主 Agent | 谁决定下一步 |
| --- | --- | --- |
| Single | 否 | 工程师指定调用对象，执行完成后返回 |
| Sequence | 否 | 程序按步骤与输入依赖推进 |
| Parallel | 否 | 程序按并发和汇合策略执行 |
| 代码条件分支 | 否 | 工程师提供的判断函数 |
| Supervisor | 是 | 显式配置的主 Agent 提出调用建议 |
| 混合嵌套 | 仅 Supervisor 子组合需要 | 非动态部分仍由程序控制 |

例如 `Sequence(ExtractAgent(), Supervisor(...), ReportAgent())` 只在中间动态子组合内使用主 Agent；不能把整条链再包进一个隐式主 Agent。非 Supervisor 组合不要求配置 main 模型。

人工确认、通信、隔离、监控和记忆均独立于是否存在主 Agent：顺序步骤可以要求人工审批；并行 Agent 可以共享授权产物；没有主 Agent 也可以记录事件并由旁路观察者分析。此类审批记录由程序定义的原调用与人工选择，不伪造“主 Agent 建议”。

## 5. DDD 分层与限界上下文

### 5.1 框架上下文

| 上下文 | 主要职责 | 核心模型 |
| --- | --- | --- |
| 定义与装配 | 解析代码定义、版本、模型和组合契约 | AgentDefinition、CompositionDefinition |
| 执行 | Run、调用、并行组、重试与取消 | Run、Invocation、Attempt、Join |
| 审批 | 请求、确认、改派及失效 | ApprovalRequest、ApprovalDecision |
| 通信与产物 | 请求关联、邮箱、产物授权及版本 | MessageEnvelope、Artifact、WorkspaceScope |
| 记忆 | 决策事实、候选经验、生效知识 | DecisionRecord、MemoryCandidate、ApprovedMemory |
| 可观测性 | 查询运行视图、事件和调用树 | RunEvent、RunProjection |

不将整个 run 的所有消息、文件与子调用塞进单一大聚合。Run 管理整体生命周期和预算，Invocation 管理本次调用及尝试；跨聚合协作使用应用服务、事务边界和领域事件。

### 5.2 分层职责

- Domain：实体、值对象、领域状态转换、不变量、领域事件及必要的领域仓储接口。
- Application：启动、委派、审批、恢复、取消等用例；依赖端口，不直接依赖模型 SDK。
- Infrastructure：DeepAgents、数据库、消息传输、对象存储、模型工厂和 Checkpoint 适配。
- Interfaces：HTTP、CLI、任务入口和事件订阅协议，负责输入验证和身份传递。

### 5.3 CPS 业务边界

CPS 自己维护 InspectionCase、Issue、Rectification、InspectionReport 和 WorkPlan。它们不能被通用 Invocation 状态替代。

```text
Invocation.succeeded ≠ 问题已确认
Invocation.succeeded ≠ 整改已验收
Invocation.succeeded ≠ 报告已发布
```

平台产出的是执行结果，业务应用按自己的规则将结果确认为业务事实。

## 6. 工程师开发接口

本节为拟议接口。首版应先固定语义，再实现对应类型，不直接照搬示例作为第三方 API。

### 6.1 Agent 定义

```python
class HistoryAnalysisAgent(DeepAgent):
    name = "history_analysis"
    version = "1.0.0"
    description = "解释历史巡检统计，分析问题分布和复发情况。"

    model_profile = "analysis_large"
    prompt_file = "prompts/history_analysis.md"
    input_schema = HistoryInput
    output_schema = HistoryOutput

    tools = [QueryInspectionStatistics, SearchConfirmedCases]
    skills = [InspectionStatisticsGuide]

    policy = AgentPolicy(
        timeout_seconds=120,
        max_iterations=12,
        read_only=True,
    )
```

定义对象与运行实例分离。可以缓存不可变定义和安全的模型客户端，不能在全局 Agent 实例上保存当前任务的可变消息、审批状态和工作目录。

### 6.2 顺序执行

```python
workflow = Sequence(
    Step(
        id="extract",
        agent=ExtractionAgent(),
        inputs={"document": Input("document")},
    ),
    Step(
        id="analyze",
        agent=AnalysisAgent(),
        inputs={
            "items": Output("extract", "items"),
            "rules": Input("rules"),
        },
    ),
    Step(
        id="report",
        agent=ReportAgent(),
        inputs={
            "analysis": Output("analyze"),
            "references": Output("extract", "references"),
        },
    ),
)
```

工程师声明输入映射，不默认把上一步全部聊天历史传给下游。支持引用原始输入和已经完成的任意前序步骤产物。

装配时检查步骤 ID 唯一性、引用关系、静态可判断的字段及类型兼容性；运行时验证真实输入输出。动态字段不能静态验证时必须声明映射与运行期校验规则。

### 6.3 并行与汇合

```python
workflow = Sequence(
    Step(id="extract", agent=ExtractionAgent()),
    Parallel(
        Step(
            id="history",
            agent=HistoryAnalysisAgent(),
            inputs={"items": Output("extract", "items")},
        ),
        Step(
            id="coverage",
            agent=CoverageAgent(),
            inputs={"items": Output("extract", "items")},
        ),
        max_concurrency=2,
        failure_policy="require_all",
    ),
    Step(
        id="report",
        agent=ReportAgent(),
        inputs={
            "history": Output("history"),
            "coverage": Output("coverage"),
        },
    ),
)
```

没有显式映射的 Step 接收父组合的输入，并仍须通过该 Agent 的 input_schema 校验。不得猜测字段。

并行组默认 require_all。可选 allow_partial 时，下游输入必须显式包含每个分支的成功、失败或取消状态，而非用空文本掩盖错误。fail_fast 应发出取消请求并等待各分支收敛，不能立即假定外部请求已停止。

### 6.4 主 Agent 动态委派

```python
workflow = Supervisor(
    coordinator=InspectionSupervisorAgent(),
    members=[
        IssueIdentificationAgent(),
        RectificationAgent(),
        HistoryAnalysisAgent(),
        CoverageAgent(),
        ReportAgent(),
    ],
    approval=Approval.none(),
    limits=RunLimits(max_delegations=20, max_concurrency=3),
)
```

Supervisor 只限定能力池，不固定调用顺序。主 Agent 可提出单调用或并行组，也可请求补充输入、人工处理或建议结束。建议先经过领域和应用校验，再变成实际调用。

### 6.5 嵌套组合

Sequence、Parallel、Supervisor 可封装成 CompositeAgent，声明输入输出后作为其他组合的成员。嵌套运行仍属于同一 run，保留父子 invocation 关系，并继承或收紧有效权限和资源预算。

## 7. 运行策略与人工确认

### 7.1 可选能力

```python
Approval.none()
Approval.before_execution()
Approval.after_execution()
Approval.before_each_delegation()
Approval.when(requires_review)
```

Approval.none 是明确关闭审批，不等于关闭权限校验、数据隔离或运行状态。inherit 表示继承上级默认策略，与 none 不同。

### 7.2 策略解析

区分两类配置：

1. 可覆盖的业务默认策略：调用点显式配置优先于最近父组合默认，再使用 Agent 默认和框架默认。
2. 不可放宽的部署限制：权限取交集，资源上限取更严格值；强制审批不允许被业务 none 绕过，出现冲突时装配或启动失败。

安全隔离不是可拆卸插件。工程师可以修改受信任的部署策略，但模型、用户输入和运行中的工具不能自行放宽策略。

### 7.3 审批绑定

审批至少绑定 run_id、dispatch_id、invocation_id、Agent 版本、输入快照哈希、调用参数哈希、有效策略版本和有效期。

人工改派或修改输入后应校验新候选，并生成对新版本明确有效的批准记录。旧批准不能沿用。跳过不等于执行成功；后续步骤需要重新判断缺失输入。

并行组可以一次展示多项，但审批记录应具体到各项调用及其输入。是否要求重试重新审批由策略明确声明；CPS 场景可要求每次重试都重新确认。

### 7.4 结果审核与副作用

after_execution 只能审核已生成的输出，不能撤销已经发送的邮件或数据库写入。需要审核的副作用必须拆为“草稿生成 → 审核 → 发布动作”，不能放在草稿 Agent 内提前执行。

### 7.5 提示词与策略一致性

```text
框架公共约束 + 角色职责 + 场景约束 + 有效运行策略 + 输出契约
```

公共约束不写死每次审批。启用或取消人工确认时，相应运行说明同步变化。若场景声明必须审批但代码设置 none，校验应报错。真正的执行阻断由代码和持久化状态保证，不靠模型自觉遵守 Prompt。

## 8. Agent 通信设计

### 8.1 通信种类

| 类型 | 语义 | 是否期待业务响应 |
| --- | --- | --- |
| 输入输出传递 | 上游产物绑定到下游输入 | 下游独立产生输出 |
| 请求与响应 | 向目标 Agent 发起一项任务 | 是，返回结果或明确失败 |
| 邮箱消息 | 对已有调用补充资料或协作通知 | 按消息契约决定 |
| 共享产物 | 发布文件或结构化结果供获准调用读取 | 不直接表示执行任务 |
| 运行事件 | 某状态变化已经发生 | 否，用于监控、审计和观察 |

### 8.2 请求入口

```python
result = await context.agents.request(
    target="history_analysis",
    payload=HistoryInput(line_id=input.line_id, period=input.period),
)
```

Python 调用与模型委派工具必须进入同一 Invocation 入口，统一做成员白名单、权限、审批和资源校验。不允许另一个默认委派工具成为绕过路径；嵌套 DeepAgents 子调用同样受控。

### 8.3 邮箱与安全消费点

```python
await context.messages.send(
    target_invocation_id=report_invocation_id,
    topic="additional_evidence",
    payload={"artifact_id": artifact.id},
)
```

发送成功只表示消息被持久接收，不表示模型已消费。默认在下一次模型调用前等安全点读取；正在进行的模型请求不能被静默改写。需要立即停止时使用中断或取消协议。

### 8.4 消息信封

```text
message_id, schema_version, tenant_id, task_id, run_id
sender_invocation_id, receiver_invocation_id
correlation_id, causation_id, message_type, topic
payload / artifact_ref, created_at, deadline
```

接收者确认、业务完成和消息投递确认分别记录。默认持久化传输允许至少一次投递，接收方按 message_id 或业务幂等键去重；不承诺任意外部副作用的 exactly-once。

### 8.5 死锁和背压

- 请求等待设置超时和最大嵌套深度。
- 检查 A 等 B、B 又同步等待 A 的循环。
- 等待子任务或审批的父调用不长期占用稀缺执行槽，避免线程池式死锁。
- 邮箱有容量、大小和频率限制；长文档通过 artifact_ref 传递。
- 消息读取、产物访问和跨 Agent 请求均继承本次作用域和权限。

## 9. 工作区隔离与产物管理

### 9.1 隔离层级

```text
tenant_id/
  tasks/task_id/
    evidence/                         任务证据
    confirmed-artifacts/              跨运行复用的已确认产物
    runs/run_id/
      input-snapshots/                 本轮只读输入
      shared/                         本轮显式共享产物
      invocations/invocation_id/
        attempts/attempt_id/
          private/                    尝试级私有文件
```

这里是逻辑作用域，不要求真实对象存储一定采用此目录形式。模型可只看到虚拟路径 /workspace/private、/workspace/shared 和 /task/evidence，底层适配器注入真实作用域。

### 9.2 权限默认值

- 私有区只有当前调用的授权执行身份可读写，父 Agent 不默认获得全部私有文件。
- run 共享区仅对显式授权的本轮调用可读；默认不可任意覆盖。
- task 资料区跨 run 复用须有明确版本与权限，不能自动把上一轮所有草稿带入。
- 跨 task 访问走独立授权工具，不直接浏览其他任务根目录。
- 长期记忆独立存储并单独校验租户、业务范围、审核状态和时效。

### 9.3 私有生成和显式发布

```python
draft = await context.workspace.private.write(
    name="history-analysis.json",
    content=analysis,
)

artifact = await context.artifacts.publish(
    source=draft,
    scope="run",
    readers=[report_invocation_id],
)
```

产物发布后内容不可变，修改创建新版本；可变的“最新版本”指针采用版本校验。存储以下元信息：artifact_id、作用域、生产者、内容哈希、MIME、大小、输入快照、父版本、授权策略和创建时间。

### 9.4 并发与输入版本

并行分支读取同一个不可变输入快照，写各自输出。用户中途上传新证据时生成新输入版本，不偷偷替换正在执行分支的数据。业务策略决定继续旧快照、标记产物过期，还是取消并重新分析。

共享可变状态只能通过受控端口修改，并提供期望版本和明确合并规则。不得使用无版本控制的“最后写入覆盖”。

### 9.5 隔离实施

目录命名不是访问控制。框架必须检查数据库归属、对象授权、虚拟路径、路径穿越和符号链接逃逸；缓存、向量检索、工具连接、事件订阅同样带作用域。

任意代码执行需要独立进程、容器或对应沙箱；不能仅靠 Python 对象或文件路径约定隔离恶意代码。

临时私有文件、运行产物、业务档案和审计事件设置不同保留策略。回收前检查引用和保留锁；删除不能破坏仍需恢复的 Checkpoint 或正在被引用的产物。

## 10. 模型与部署配置

业务组合用 Python；YAML 管理提供商、模型档位与部署参数。以下模型 ID 为占位示例，应替换成实际部署名称。

```yaml
providers:
  internal:
    type: openai_compatible
    base_url: http://model-gateway.internal/v1
    api_key_env: INTERNAL_MODEL_API_KEY

models:
  reasoning_large:
    provider: internal
    model: deployed-large-model
    temperature: 0.1
    max_tokens: 8192
  vision_medium:
    provider: internal
    model: deployed-vision-model
    temperature: 0.0
    max_tokens: 4096

agents:
  inspection_supervisor:
    model_profile: reasoning_large
  issue_identification:
    model_profile: vision_medium
  report:
    model_profile: reasoning_large
    temperature: 0.2
```

工程师可以为 Agent 改绑独立模型档位及提供商，实现独立地址和密钥来源。若支持 Agent 级地址覆盖，应同时校验提供商类型和凭据绑定，不应无意把某提供商密钥发送到另一个地址。

配置优先级为 Agent 显式覆盖、模型档位、提供商默认。参数量通过选择实际部署的模型实现，不是额外传入一个参数量字段。

运行开始保存模型、提示词、工具、Skills、组合与策略版本快照，不保存明文密钥。热更新默认仅影响新 run；恢复旧 run 时使用其快照，凭据通过当前受控密钥引用解析。

## 11. DeepAgents 完整能力与执行适配

### 11.1 版本依据与能力分类

2026-09-11 检查 basic-project 根目录的本地虚拟环境：deepagents 为 0.6.12，langchain 为 1.3.14，langgraph 为 1.2.10。该结果是本地安装状态，不代表最新发布版本，也不能拿独立 cps-inspection-agent 子项目的环境代替根项目环境。

本节同时核对官方在线文档和本地 SDK 的入口及实现。在线文档可能领先本地版本，例如官方文档中的 fork 子 Agent 标为 beta 且要求 deepagents>=0.7.13；当前根项目版本不能直接启用。核验来源见第 19 节。

将经常被统称为“模式”的概念拆开：

| 类别 | 内容 | 是否需要主 Agent |
| --- | --- | --- |
| 执行组合 | Single、Sequence、Parallel、条件路由、Supervisor、嵌套组合 | 仅需要动态委派时使用 |
| 引擎能力 | 规划、子 Agent、文件、Skills、上下文整理、结构化输出 | 除委派角色关系外均不强制 |
| 干预与治理 | Human-in-the-loop、权限、中间件、调用限制 | 不需要 |
| 运行与状态 | 多轮会话、流式事件、Checkpoint、Store、缓存 | 不需要 |
| 扩展能力 | 远程后台子 Agent、解释器、fork、外部工具协议 | 按扩展语义决定 |

框架应完整提供这些能力的配置、适配和测试入口，而不是让每个 Agent 默认变成一个全权限主 Agent。SDK 不支持的选项应启动失败并报告版本要求，禁止静默忽略。

### 11.2 能力矩阵

下表“纳入”表示目标架构要求，不表示已完成实现。审批、任务隔离和监控规则始终由框架统一执行。

| ID | 能力 | 依据或归属 | 工程师入口与平台要求 |
| --- | --- | --- | --- |
| DA01 | 单 DeepAgent 与工具循环 | 本地 create_deep_agent | 独立模型、工具和输出契约；可以无子 Agent 运行 |
| DA02 | Todo 规划与计划更新 | 本地 TodoListMiddleware | 可配置计划能力，展示计划但不把其状态等同业务审批或完成 |
| DA03 | 声明式同步子 Agent | 本地 SubAgent | 独立描述、模型、提示词、工具、Skills、输出与策略 |
| DA04 | 预编译子 Agent | 本地 CompiledSubAgent | 接入编译好的 runnable，验证输入状态、输出与治理端口 |
| DA05 | 多子 Agent 并行委派 | 官方子 Agent 文档及运行时集成 | 原生并行仍进入统一并发预算、Invocation 和审批记录 |
| DA06 | 异步远程子 Agent | 本地 AsyncSubAgent | 后台启动、状态跟踪、结果收集、取消确认和远程身份映射 |
| DA07 | fork 子 Agent | 在线文档中的版本条件及 beta 扩展 | 纳入升级适配项；上下文复制必须审查，不能在 0.6.12 中假装支持 |
| DA08 | 解释器驱动的动态子 Agent | 在线文档中的 beta 扩展 | 额外解释器依赖和沙箱策略；代码中的委派同样可审计、可审批 |
| DA09 | 工具调用 Human-in-the-loop | 本地 interrupt_on / HumanInTheLoopMiddleware | 工具级 approve / edit / reject，持久化后恢复执行 |
| DA10 | 子调用审批及人工接管 | 原生中断加框架审批用例 | 委派前审批、子 Agent 内工具审批、业务输出审核分别建模 |
| DA11 | 文件操作与 Backend | 本地 BackendProtocol 及各 Backend | 作用域映射、读写权限、文件引用及产物版本 |
| DA12 | Shell 与沙箱执行 | 本地执行 Backend 与可选沙箱集成 | 默认拒绝未授权执行，限制网络、密钥、资源和挂载 |
| DA13 | Skills 与按需加载 | 本地 skills / SkillsMiddleware | 独立选择技能、依赖验证、只读源和版本快照 |
| DA14 | Memory 文件注入与持久 Store | 本地 memory、store、MemoryMiddleware、StoreBackend | 区分记忆来源加载、存储命名空间和业务审核 |
| DA15 | 摘要、上下文压缩与大输出卸载 | 本地 Summarization 与文件中间件 | 策略可配置，保留来源，卸载数据仍按 run 隔离 |
| DA16 | 流式消息、状态和子调用事件 | 官方 streaming / event-streaming 与图运行时 | 统一事件映射、父子命名空间、脱敏和断线重放 |
| DA17 | Checkpoint、多轮状态与恢复 | 本地 checkpointer 和 LangGraph Runtime | 稳定 thread 绑定、恢复对账、历史版本与业务幂等 |
| DA18 | 自定义状态、上下文和结构化结果 | 本地 state_schema、context_schema、response_format | 区分可变执行状态、服务端上下文和可信业务事实 |
| DA19 | 自定义中间件与 Harness Profile | 本地 middleware 和 profile 解析；在线自定义文档 | 显式装配顺序、覆盖规则、实际能力清单与安全检查 |
| DA20 | 重试、降级、调用限制与 PII 处理 | LangChain 中间件生态加框架策略 | 明确模型重试与业务重试边界，避免重复副作用 |
| DA21 | 缓存与供应商特定优化 | 本地 cache 与模型相关中间件 | 能力检测、租户隔离、版本失效，不缓存审批授权 |
| DA22 | 外部工具与 MCP 接入 | 外部工具适配生态，非默认授权 | 连接生命周期、凭据管理、工具 Schema 与权限过滤 |
| DA23 | Sequence、Parallel、条件分支、反思循环 | 框架组合层，不混称为 DeepAgents 工厂参数 | 工程师代码组合，复用统一运行机制，不强制主 Agent |
| DA24 | 消息邮箱、观察 Agent、记忆审核、工作区隔离 | 框架补充，不能仅靠 SDK 自动完成 | 持久化投递、作用域、审核版本与旁路失败隔离 |

### 11.3 原生子 Agent 的三种接入形态

#### 声明式 SubAgent

框架将 AgentDefinition 映射为子 Agent 定义；name 唯一，description 用于委派选择，system_prompt 用于角色执行。模型、工具和输出契约按子 Agent 独立解析。

本地版本的声明式子 Agent 支持 model、tools、middleware、interrupt_on、skills、permissions、response_format 等覆盖项。不能因此假定所有父级设置都自动继承：框架必须计算并显式下发有效配置，逐项验证结果。[S1][S2][L1]

#### CompiledSubAgent

用于接入预先编译好的 Agent 或图，例如工程师封装好的“提取 → 并行分析 → 汇总”组合。当前 SDK 要求可用的消息状态通信约定；不能把任意函数直接冒充兼容 runnable。

预编译对象不保证自动继承父级状态 Schema、中间件或审批策略。适配器必须验证结果契约和治理边界；无法验证的编译对象只能作为受限制扩展，不能出现在需要强治理的场景中。[S1][S2][L1]

#### AsyncSubAgent

本地定义包含 name、description、graph_id 以及可选 url、headers，用于对接 Agent Protocol 服务。它不是简单把一个同步 Python 方法加上 async，也不是本地 Parallel 的同义词。[L1]

框架需要保存本地调用与远程 thread/run 的映射，处理远端中断、失联、超时、取消结果和输出访问。父级审批不自动成为远端工具执行的审批；远端同样需要可信策略，或在发送前限制为只读能力。

### 11.4 无子 Agent 模式及默认委派防护

本地 create_deep_agent 的实现会在满足默认配置时自动增加 general-purpose 子 Agent。因此 `subagents=[]` 不能直接被视为“已完全关闭子 Agent”。[S1][L1]

统一工厂需要显式解析目标能力：

```text
subagents=disabled
  → 禁用默认 general-purpose
  → 不注册任何显式同步或异步子 Agent
  → 禁用解释器中的子 Agent 调度入口
  → 检查最终模型可见工具中不存在委派能力
```

关闭方式使用所锁定版本支持的 Harness Profile 或装配接口，不依赖仅在 Prompt 中写“不要使用子 Agent”。若当前适配器无法可靠关闭，启动失败而不是悄悄开放。

Single、Sequence、Parallel 的普通叶子节点默认不具备委派能力。只有工程师显式声明子能力时，该节点才承担局部委派者角色；这不要求在整个业务流程外再增加一个总主 Agent。

### 11.5 Human-in-the-loop 的完整接入

必须分别支持三种语义，而不是只做一个弹窗：

| 介入位置 | 场景 | 实现边界 |
| --- | --- | --- |
| 委派或步骤执行前 | 是否调用下一步 Agent，是否改派 | 框架调度审批，必要时拦截原生 task / 远程启动入口 |
| Agent 内部工具执行前 | 写文件、执行命令、调用业务工具 | 原生 interrupt_on 加持久化中断适配 |
| Agent 输出之后 | 修改问题、验收整改、审核报告 | 业务应用审核草稿，不能冒充原生工具前置审批 |

原生工具审批的 approve、edit、reject 应映射到统一 ApprovalDecision。编辑后的工具名和参数必须重新校验；不能因“已编辑”就允许切换成更高权限工具。一次暂停若包含多个待审核工具调用，需要保存各项关联关系，不把一个决定套用到所有调用。[S3]

声明式子 Agent 的 interrupt_on 可继承或覆盖父配置；预编译和远程子 Agent 不能假定自动继承。平台计算最终策略后，分别验证父工具、委派入口、子工具和远程服务，不允许遗漏一层。

`Approval.none()` 应确保没有业务审批中间件或权限规则残留产生意外暂停；若部署权限声明强制 interrupt，则判定与 none 冲突，而不是静默覆盖。正常请求用户补充缺失资料属于 waiting_input，不等于强制审批。

恢复时关联同一持久化 thread 和对应 interrupt，验证审批版本和一次性消费。消息历史修复中间件只能修复工具消息结构，不能撤销或证明工具副作用，仍需业务幂等。

### 11.6 规划与上下文工程

- Todo 计划：允许工程师开启任务分解和计划展示；计划完成只是 Agent 的工作声明，不是业务验收。
- 摘要：在接近上下文预算时压缩历史，保留可回查原记录。摘要不是长期记忆，不可取代人工审批事实。
- 大输出卸载：长工具输出、上下文资料写入运行隔离 Backend，消息中只保留引用和摘要。内部卸载文件不应与正式报告混放。
- 消息修复：保留所选引擎需要的工具调用配对修复能力，防止中断恢复出现悬空调用；错误修复不得重新执行已完成副作用。
- 预算：分别限制模型调用、工具调用、委派层数、文件大小和 token 使用；不能只靠 recursion_limit 代表全部资源预算。

本地版本与在线文档的默认中间件栈存在演进，不在框架中复制一个永久固定的 SDK 栈清单。每次装配输出有效中间件和工具清单，并运行快照及安全测试。[S2][S6][L1]

### 11.7 文件 Backend、权限与沙箱

| Backend 或机制 | 用途 | 框架补充限制 |
| --- | --- | --- |
| StateBackend | 当前 thread 中的文件状态，可随 Checkpoint 保留 | 不自动成为跨 run 共享空间 |
| FilesystemBackend | 本地或挂载文件读写 | 根目录、虚拟路径与实际授权一致；不能当成 OS 沙箱 |
| StoreBackend | 通过 Store 跨 thread 保存文件 | namespace 必须带租户与业务作用域，禁止默认全局共享 |
| CompositeBackend | 按路径路由到多个存储后端 | 区分任务资料、私有临时数据、运行产物和记忆 |
| LocalShellBackend | 在本机执行命令 | 无 OS 级隔离，不用于不可信生产 Agent 的默认环境 |
| 沙箱后端 | 隔离环境中的代码与工具执行 | 供应商可替换；限制挂载、网络、凭据、CPU、内存与执行时间 |
| ContextHub / 托管后端 | 可选远程文件或上下文来源 | 无外部服务配置时不得静默启用，验证访问控制与资料出境边界 |

上述名称和基本用途已通过本地后端导出及官方后端文档核对；外部托管实现不等于平台默认依赖。[S4][L1]

文件权限需支持允许、拒绝、执行前审批等动作。内置文件中间件的路径规则仅覆盖其治理的工具，不证明 Shell、自定义工具或网络请求同样受限。沙箱执行必须独立限制这些旁路。

如果图像文件通过工具读取返回多模态内容，还必须验证供应商和模型确实接收到图像，而非只有文件名或文字路径；未验证前不能宣称视觉能力已接通。

### 11.8 Skills 与 Memory

Skills 是可选的流程指导和参考资料，通过后端访问和按需加载。它不是另一个 Agent，也不自动授予其文档中提到的工具或代码执行权限。每个 Agent 的 Skills 清单需独立解析，不能默认任意子 Agent 继承父级所有技能。[S1][S5]

Memory 需要区分：

1. memory 路径：告诉中间件加载哪些记忆资料。
2. Store / Backend：定义资料实际保存在哪里、是否跨 thread 保留。
3. 命名空间：定义谁可读取和修改。
4. 业务记忆策略：定义事实记录、候选提炼、审核、生效与失效。

传入 memory 或 store 不意味着自动拥有旁路观察、去重、审核和安全的自我优化。通用框架可选关闭记忆；CPS 可将共享长期记忆设为只读，观察者只提交候选，由业务审核后发布。[S7]

### 11.9 流式输出、多轮状态与恢复

同时纳入消息流、状态更新、工具事件、子 Agent 事件和业务自定义进度。官方文档提供 typed event streaming 和图 stream 两类消费路径；适配器应统一投影到 RunEvent，不让业务依赖原始 chunk 结构。[S8]

流式文本不是持久化完成信号。只有输出契约校验和状态事务完成后，才能发布 invocation.succeeded。多模态块、工具参数和模型元数据需保留类型并脱敏，不应无条件拼成纯文本。

多轮会话和审批恢复应沿用授权运行的稳定 thread；新业务 run 不得复用旧 thread 来混合上下文。并行子图的 namespace 与 invocation 映射要可追踪。历史重放或从旧 checkpoint 分叉产生新运行谱系，不能覆盖正式结果，也不能无审批重演外部副作用。

### 11.10 结构化输出、自定义状态与上下文

`response_format` 对接应用层契约，可根据提供商能力选择合适的结构化方式。声明式子 Agent 和预编译对象返回结构化结果时，适配器需保留字段，不只抽取最后一条自然语言文本。[S1][L1]

`state_schema` 承载执行期间状态；`context_schema` 承载由服务端注入的授权上下文。数据库连接、工作区实例和身份不应由模型输出重建，也不应直接序列化进对话或 Checkpoint。

框架额外校验 Agent 白名单、来源引用、业务输入映射和产物权限。JSON 合法不等于结论正确，也不等于可以发布。

### 11.11 中间件、Profile、重试与缓存

工程师可以配置原生或自定义中间件，但框架保留不可移除的权限和隔离边界。装配后应能查询：模型、工具、Skills、子 Agent、中间件顺序、审批规则、Backend 路由、资源上限与版本来源。

Harness Profile 是引擎装配配置，不等同于 YAML 的模型档位。中间件默认实例覆盖、排除项和 profile extras 均由版本适配器解析；不能以相同名字重复注册审批或日志，造成重复暂停与重复事件。[S2]

模型调用重试、工具重试和整个 Agent 调用重试必须分别治理。只对明确允许且可幂等的操作自动重试。模型降级应由工程师配置，保持所需视觉、工具和结构化能力，记录实际模型版本与错误，不能静默变成固定答案。

缓存包含供应商 Prompt 缓存和框架/图结果缓存等不同层次；启用前检查租户、版本、输入和权限边界。不得缓存可复用的审批通过决定或把一个用户的敏感结果返回另一个用户。

PII 处理、模型/工具次数限制、工具筛选等作为 LangChain 生态中间件接入，不宣称全部由 DeepAgents 单独实现。工具筛选不能扩展授权范围。

### 11.12 远程工具与 MCP

外部工具统一经 ToolRegistry 和连接适配器进入 Agent。工具描述和输出作为不可信资料，连接凭据由应用持有。按场景限制可见工具、输入 Schema、读写权限、超时及调用预算。[S2]

MCP 是外部工具/资源接入机制，不替代框架的 Agent 邮箱、业务审批或远程子 Agent 运行协议。远程工具若无法证明其副作用边界，应要求更严格策略或不准入。

### 11.13 fork 与解释器动态委派

这两类能力纳入完整架构，但单独标记为需要版本与安全验证的扩展，不在本次文档更新中升级依赖。

- fork 子 Agent：官方文档注明需要 deepagents>=0.7.13 且为 beta。它涉及父会话和提示词继承，与默认隔离任务输入不同。只有父历史的全部内容都在子调用授权范围内时才允许原生 fork；否则拒绝或改成显式筛选输入的隔离子 Agent，不声称两者语义相同。对话 fork 也不等于文件工作区复制或共享写权限。[S1]
- 动态子 Agent：官方描述的是解释器中用代码循环、分支和并行批次派发已提供的子 Agent；不是允许模型任意创建高权限 Agent。需要额外解释器组件，当前文档标为 beta。生成代码中的每次委派同样进入 Invocation、预算与审批边界；不能因为在解释器内部就隐去子调用。[S9]

扩展隔离权限不得高于父级；解释器无法覆盖审批、持久化和追踪时，对需要这些保障的场景拒绝启用。版本升级需要单独变更记录和可回退的依赖锁文件。

### 11.14 工程师能力组合接口

以下为框架拟议接口，不是 DeepAgents 参数的原样转写：

```python
class EvidenceAgent(DeepAgent):
    model_profile = "vision_medium"
    prompt_file = "prompts/evidence.md"
    input_schema = EvidenceInput
    output_schema = EvidenceOutput

    features = AgentFeatures(
        planning=Planning.enabled(),
        subagents=Subagents.disabled(),
        skills=[EvidenceReadingSkill],
        workspace=Workspace.scoped_run(),
        execution=Execution.disabled(),
        memory=Memory.disabled(),
        context=ContextPolicy.summarize_and_offload(),
    )
    policy = AgentPolicy(approval=Approval.none())
```

```python
inspection_team = Supervisor(
    coordinator=InspectionSupervisorAgent(),
    members=[EvidenceAgent(), HistoryAnalysisAgent(), ReportAgent()],
    approval=Approval.before_each_delegation(),
    limits=RunLimits(max_delegations=20, max_concurrency=3),
)

workflow = Sequence(
    LoadEvidenceStep(),
    inspection_team,
    ReviewReportStep(),
)
```

工程师还可以注册 CompiledSubAgent、远程子 Agent 和受信任自定义中间件的适配定义。高级扩展必须经过能力验证，不能通过无校验的任意 kwargs 覆盖框架权限。每个开发接口都要能输出可审阅的有效配置，避免“看似关闭，实际还存在默认工具”。

### 11.15 工厂与适配器职责

```text
AgentDefinition / Composition / Policy
       ↓
能力解析、版本检查、权限与继承计算
       ↓
EffectiveAgentSpec 不可变快照
       ↓
DeepAgentFactory
       ├─ 模型 / Schema / 中间件 / Profile
       ├─ Backend / 工作区 / Store / Checkpoint
       └─ 受控工具与子 Agent
       ↓
RuntimeAdapter：invoke / stream / resume / cancel
       ↓
统一 RunEvent 与 Artifact
```

定义层负责声明，应用层负责策略和调度，基础设施层负责 SDK 映射。SDK 原生行为不能反过来覆盖领域不变量。取消是框架协作协议，不承诺任意引擎调用或远程请求立即停止。

### 11.16 完整支持的验收定义

每项能力都需要同时有：开发接口、有效配置验证、执行适配、状态或事件映射、隔离与权限测试、失败处理、文档和示例。仅在依赖中安装 deepagents 或转发参数不算完成。

必选覆盖为 DA01–DA24 的能力类别；涉及 beta 或外部服务的能力作为显式扩展交付，不默认为本地已可用。不能只留空接口就声称扩展完成：未实现、版本不满足、外部依赖未配置必须分别报告状态。

## 12. 生命周期、持久化与恢复

### 12.1 Run 状态

```text
queued → running ↔ waiting_approval
               ↔ waiting_input
               ↔ waiting_children
               → succeeded / failed
               → cancelling → cancelled
```

Invocation 同样有独立状态；Attempt 记录具体开始、结束、超时、失败和取消结果。所有分支和业务必需后置动作结束后，组合才可成功。

### 12.2 顺序恢复

- 上游失败时不执行依赖步骤。
- 审批等待持久化，不通过长期挂起一个 HTTP 请求等待用户。
- 重启从有效检查点和调用记录恢复，不无条件重跑全部步骤。
- 修改上游产物后标记受影响下游过期，按业务策略重新执行。
- 取消不会撤销已发生副作用，需补偿动作时由业务明确实现。

### 12.3 可靠执行

持久化模式采用运行记录与 Outbox 同事务写入；发布器将事件送到消息流，消费者通过事件 ID 去重。若引擎 Checkpoint 与业务数据库不在同一事务中，通过幂等操作和恢复对账协调，不能假设二者天然原子一致。

Worker 使用租约和心跳判断占有权；领取与状态转换采用条件更新。外部调用前后记录操作状态和幂等键。崩溃导致结果不确定时进入对账或人工处理，不盲目重发支付、通知、发布等副作用。

可以提供进程内开发模式，但需明确其不承诺重启恢复、多实例调度或持久化事件重放；可靠运行模式作为业务部署默认方向。

## 13. 监控与旁路观察

### 13.1 统一事件

```text
run.started / run.succeeded / run.failed / run.cancelled
dispatch.proposed
approval.requested / approval.decided / approval.expired
invocation.queued / invocation.started / invocation.succeeded / invocation.failed
tool.started / tool.succeeded / tool.failed
message.accepted / message.consumed
artifact.published
memory.candidate_created / memory.reviewed
```

事件包含 event_id、schema_version、tenant_id、task_id、run_id、invocation_id、父调用 ID、sequence、时间及脱敏 payload。sequence 用于流内补齐，不假设不同并行生产者的本地时间是严格全局顺序。

### 13.2 运行视图

提供状态、调用树、排队耗时、执行耗时、等待审批、工具过程、并行分支、产物版本和失败信息。Token 用量取可获得的真实数据；未知用量和未配置价格时显示未知，不伪造成本。

HTTP 提供查询与控制，SSE 提供事件订阅及基于游标的断线重放。浏览器关闭不取消任务。取消、恢复等动作使用独立控制接口。事件鉴权按租户和任务，不向无权限用户广播全局日志。

不展示模型隐藏思维链；展示计划、工具动作、简短理由和可追溯证据即可。

### 13.3 观察 Agent

观察 Agent 异步消费持久化事件，与主执行资源隔离。消费失败可重试并记录水位，不导致主任务失败。观察自身事件需单独标识或过滤，避免递归观察形成无限循环。

Run 监控、审计和长期记忆为不同能力。关闭长期记忆或观察 Agent 不影响基础运行状态查询。

## 14. 记忆与决策记录

始终区分原始事实与可复用经验：

```text
人工决策记录 → 观察分析 → 候选记忆 → 场景要求的审核 → 生效记忆
```

CPS 可要求完整记录人工原建议、改派、理由、输入版本和后续结果，并审核通用记忆。其他场景可选择不同写入策略或完全禁用记忆。任何生效记忆都不能扩大工具权限或改变强制隔离策略。

一次改派无说明只证明选择发生，不能推导永久偏好；调用成功不证明整改长期有效。记忆检索必须过滤归属、范围、审核状态、有效期与版本，并记录命中的记忆 ID。

## 15. 项目目录建议

```text
app/
  harness/
    definitions/                  工程师声明接口与组合定义
    domain/                       通用实体、值对象和领域事件
    application/                  启动、调度、审批、恢复、取消
    composition/                  Single、Sequence、Parallel、Supervisor
    ports/                        应用执行、消息、存储等端口
    infrastructure/
      deepagents/                 工厂、能力/Profile 解析、子 Agent/HITL/流式适配
      persistence/                仓储、Outbox、Checkpoint 连接
      communication/              邮箱、请求关联、事件传输
      workspace/                  文件、对象存储与授权
      sandbox/                    本地受控执行与远程沙箱适配
      integrations/               MCP、远程 Agent Protocol、可选解释器适配
    observability/                调用树与查询投影
  projects/
    cps/
      domain/
      application/
      agents/
      tools/
      prompts/
      composition.py
    doc_review/
  api/v1/endpoints/
    agent_runs.py
    cps.py
```

初期可按上述层级组织单体模块；某个上下文复杂后再拆内部 domain/application/infrastructure，不为了目录完整性创建大量空模块。组合和领域规则应保持与 SDK 解耦。

## 16. 与现有代码集成

已有实现可作为迁移起点，但不能直接视为生产完整能力：

| 现有位置 | 迁移方向 |
| --- | --- |
| app/harness/base.py | 保留兼容入口，拆出不可变定义、每次调用上下文与运行用例 |
| app/harness/backends/deepagents_backend.py | 统一模型/工具装配，补足事件与恢复适配 |
| app/harness/agents/sequential.py | 对接显式输入输出映射和持久化步骤状态 |
| app/harness/agents/parallel.py | 增加并发预算、分支隔离、汇合与失败策略 |
| app/harness/agents/subagent.py | 统一受控委派入口，避免另一套审批与事件逻辑 |
| app/harness/communication/ | 明确共享状态、邮箱和事件的不同语义 |
| app/harness/middleware/tracing.py | 从单次耗时扩展为结构化运行事件 |
| app/projects/doc_review/ | 作为兼容性回归场景 |

现有 EventBus 的同步等待处理器不能直接承载耗时观察任务。现有运行上下文共享方式要审核并发可变状态和跨 run 复用风险。

独立 cps-inspection-agent 目录作为先前探索实现，不直接整体复制进通用框架。业务 Prompt 和业务契约可以评审复用，但 CPS 强制审批要求应放到 CPS 场景策略，不迁移成框架全局限制。

## 17. 建设顺序与验收

### 17.1 建设顺序

1. 固定身份、输入输出、策略继承、状态与事件契约。
2. 实现 Agent 工厂、Single 和 Sequence，完成配置快照及输入映射。
3. 实现隔离工作区、不可变产物、持久化运行及 Outbox。
4. 实现 Parallel、分支汇合、资源预算和失败恢复。
5. 实现 Supervisor、统一委派和可选人工审批。
6. 实现消息邮箱、运行查询、SSE 重放及观察消费者。
7. 接入 CPS 和文档审核业务回归，补全记忆和产物审批策略。

上述步骤均需对照 DA01–DA24 追踪交付，不以完成某一阶段代替完整能力验收。fork、解释器和远程服务等扩展在对应依赖与环境满足后单独验证，状态与基础能力分别展示。

### 17.2 验收矩阵

| 编号 | 场景 | 通过条件 |
| --- | --- | --- |
| A01 | 单 Agent 无人工确认 | 无 main 配置也可执行；不创建协调 Agent，结果通过契约校验 |
| A02 | 顺序输入映射 | 程序推进步骤，无额外协调模型调用；字段和版本映射正确 |
| A03 | 两个独立分支并行 | 无主 Agent 也可并行及汇合，实际重叠执行且不超过并发数 |
| A04 | 部分分支失败 | require_all 不继续；allow_partial 明确携带失败信息 |
| A05 | 动态调度免审批 | 主 Agent 按目标选择白名单能力，无固定业务顺序 |
| A06 | 动态调度需审批 | 未批准零执行；改派绑定新输入和 Agent 版本 |
| A07 | 重复提交与重启 | 不重复产生同一业务副作用，恢复状态可追溯 |
| A08 | 两任务使用同名文件 | 内容隔离，不串缓存、不串向量检索范围 |
| A09 | 同一任务重跑 | 新 run 不覆盖旧 run 产物，只显式复用任务资料 |
| A10 | 并行共享产物 | 分支私有写，授权发布后可读，冲突版本被拒绝 |
| A11 | 邮箱补充消息 | 消费状态可见，模型下一安全点获得资料 |
| A12 | 消息重投与循环请求 | 去重有效，超时与循环检测防止无限等待 |
| A13 | 监控断线重连 | 根据游标补齐，无权用户不能订阅其他任务 |
| A14 | 观察消费者故障 | 主链路不受影响，消费可恢复且无递归循环 |
| A15 | 策略一致性 | none 和 inherit 不混淆；强制限制冲突时拒绝启动 |
| A16 | 模型地址和 Agent 参数切换 | 实际请求使用指定配置，快照可追溯且不泄露密钥 |
| A17 | 缺少凭据或现场证据 | 不返回伪造正常分析结果 |
| A18 | 局部 Supervisor 嵌套 | 仅动态子组合产生主 Agent 调用，外层不创建隐式主 Agent |
| A19 | 无主 Agent 的步骤审批 | 程序生成审批请求，审批、改派和审计独立生效 |
| A20 | 关闭所有子 Agent | 不生成 general-purpose，模型工具和解释器内均无隐藏委派入口 |
| A21 | 原生工具 HITL | approve/edit/reject 均可恢复，编辑后重新校验权限和参数，多项审核准确关联 |
| A22 | 三类子 Agent 治理 | 声明式、预编译及远程形态均可追踪；未验证的审批继承不允许上线 |
| A23 | 独立子 Agent 配置 | 模型、工具、Skills、权限和输出契约符合显式配置，不意外继承高权限 |
| A24 | Backend 路由与权限 | 临时数据、共享产物和记忆正确分流；文件规则不能被 Shell 或自定义工具绕过 |
| A25 | Skills 与上下文整理 | 技能按配置加载；摘要与卸载不丢失来源，不污染长期记忆和跨任务目录 |
| A26 | 多层级流式事件 | 子调用、工具与结构化结果完整关联；无 token 时仍能看到真实运行状态 |
| A27 | 远程后台调用 | 启动和结果可持久关联；断连不被当成取消完成，重复收集不重复入库 |
| A28 | 原生和框架审批冲突 | none、工具 interrupt 和部署强制策略冲突时明确失败，而不是静默残留审批 |
| A29 | 中间件与模型降级 | 顺序可审计；降级不丢失所需能力，不重复工具副作用，不返回假数据 |
| A30 | fork 扩展 | 版本不满足时拒绝；获准启用后不突破父子授权边界，不误共享私有工作区 |
| A31 | 解释器动态子 Agent | 生成代码中的委派仍受预算、审批与追踪控制，不能无限并行或越权访问 |
| A32 | 外部工具和缓存 | 工具权限按作用域限制；缓存不泄露跨任务资料，不缓存可复用授权 |
| A33 | 有效能力清单 | 每个 run 能查询实际工具、中间件、子 Agent、Backend 和策略版本 |

测试应包含领域单元测试、SDK 适配契约测试、存储与消息集成测试、并发竞争测试、故障注入和真实业务样本评测。模拟模型测试不能代替真实模型效果验证。

## 18. 关键决策与待评审项

### 18.1 已确定的方向

- 代码优先，不建设 Agent 前端配置器。
- 支持单体、顺序、并行、动态委派和嵌套组合。
- 主 Agent 仅为 Supervisor 的显式组件；运行协调器是程序，不是隐藏的主 Agent。
- 完整纳入 DeepAgents 能力，但按需启用；默认子 Agent、原生权限与中间件必须显式治理。
- 人工确认是可选场景策略，不是框架全局强制条件。
- 所有委派统一入口，所有工作区具备明确作用域。
- 私有默认、显式共享、跨运行显式复用、跨任务明确授权。
- 业务成功与 Agent 执行成功分离。
- 监控事件、审计事实与长期记忆分离。

### 18.2 实施前需明确

| 项目 | 建议起点 | 需确认内容 |
| --- | --- | --- |
| 持久化 | 复用现有数据库基础设施，以事务与条件更新为基准 | 首个支持数据库及迁移方案 |
| 事件传输 | Outbox 加一种持久化消息实现 | Redis Streams 或已有消息平台及保留策略 |
| 文件存储 | 本地隔离适配与对象存储端口 | 生产对象存储、容量、保留期 |
| 身份权限 | 复用现有认证入口，服务端生成 Scope | 单组织默认值和多组织授权规则 |
| 审批接入 | 通用 ApprovalPort | CPS 的具体审核角色及有效期 |
| DeepAgents | 统一工厂并锁定版本 | 原生委派能否满足拦截、事件及工作区要求 |
| 运行规模 | 首版限制全局、租户和 run 并发 | SLA、Token 预算、队列容量和超时 |
| 记忆 | 默认不将工作区自动转为记忆 | 场景范围、审核方式和失效周期 |

架构评审通过后应先交付一个真实纵向闭环：工程师代码定义组合、两分支隔离并行、可选审批、持久化恢复和实时监控，再迁移全部业务能力。不能仅凭目录创建、依赖安装或接口可启动宣称框架完成。

## 19. 核验来源与版本记录

### 19.1 官方资料

本次通过官方站点的 Markdown 文档核对能力描述。链接以来源地址记录；SDK 能力随版本变化，实施时应按锁文件重跑适配测试，不照搬在线示例中的模型名和默认配置。

| 编号 | 官方资料 | 来源地址 |
| --- | --- | --- |
| S1 | Subagents：默认、声明式、预编译、fork、动态委派 | `https://docs.langchain.com/oss/python/deepagents/subagents.md` |
| S2 | Customize Deep Agents：工厂配置与中间件栈 | `https://docs.langchain.com/oss/python/deepagents/customization.md` |
| S3 | Human-in-the-loop：工具审批、编辑、拒绝与恢复 | `https://docs.langchain.com/oss/python/deepagents/human-in-the-loop.md` |
| S4 | Backends：State、Filesystem、Store、Composite 和执行后端 | `https://docs.langchain.com/oss/python/deepagents/backends.md` |
| S5 | Skills：技能组织、后端加载和可执行资源 | `https://docs.langchain.com/oss/python/deepagents/skills.md` |
| S6 | Prebuilt middleware：重试、摘要、调用限制等生态组件 | `https://docs.langchain.com/oss/python/deepagents/middleware.md` |
| S7 | Memory：记忆作用域、存储和后台整合 | `https://docs.langchain.com/oss/python/deepagents/memory.md` |
| S8 | Streaming：事件投影、图流和子图命名空间 | `https://docs.langchain.com/oss/python/deepagents/streaming.md` |
| S9 | Dynamic subagents：解释器动态委派入口 | S1 中的 Dynamic subagents 小节及其扩展说明 |

### 19.2 本地核验 L1

核验范围是根项目 `.venv` 中已安装包的元数据、类型定义与源码，而非以前子项目的代码。检查了：

- deepagents.graph.create_deep_agent 的参数、默认 general-purpose 生成、声明式子 Agent 配置处理、中间件和 Checkpoint/Store/cache 装配。
- deepagents.middleware.subagents.SubAgent / CompiledSubAgent 的输入字段与结果通信说明。
- deepagents.middleware.async_subagents.AsyncSubAgent 的远程服务字段与运行说明。
- deepagents.backends 的后端导出以及 FilesystemPermission 的权限模式。

本地 0.6.12 的 SubAgent 字段不包含在线文档的 fork mode。不能把“在线文档已经出现”误写成“本地已支持”。本次仅修改架构文档，未安装扩展、升级包、调用外部模型或验证远程运行。

### 19.3 修订记录

| 版本 | 变更 |
| --- | --- |
| 0.1.0 | 形成代码优先、组合、通信、审批、工作区及持久化基础架构 |
| 0.1.1 | 明确主 Agent 非必选，仅动态委派子组合使用 |
| 0.2.0 | 增加 DA01–DA24 能力矩阵、完整 HITL 与子 Agent 接入、Backend/Skills/Memory/流式/中间件、版本条件扩展及 A20–A33 验收 |
