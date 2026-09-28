# 智能体 Harness 工程标准（复刻自 yeasy《智能体 Harness 工程指南》）

> **来源**：https://yeasy.gitbook.io/harness_engineering_guide （多页 GitBook，共四部分 14 章 + 附录）
> **本文性质**：对上述在线标准全站内容的结构化复刻与提炼，共 267 条可核查条目，每条标注原书小节号（[方括号]），供本项目架构审计、设计与代码评审对照使用。条目 ID（DEF/ARCH/PRIN/RT/TOOL/MEM/MODEL/ORCH/MCP/PROD/REL/SEC/EVAL）为审计追溯锚点，修改代码时请在 PR/commit 中引用。
>
> **使用注意**（原书明确说明，避免误读）：
> 1. 书中 autoDream 是示意性管道（非 Claude Code 官方内置）；OpenClaw 的 70% 刷写阈值等具体数值均为示例默认值，审计时关注"机制是否存在"而非具体数值。
> 2. 多模型（带回退链）vs 单模型绑定是设计权衡而非缺陷，不应对单模型方案直接判负。
> 3. 若项目无 MCP 多版本对接需求，MCP-04/07/08 可降级为"至少固定协议版本并统一请求构造"。
> 4. Claude Code 七层压缩 vs Harness 三层记忆：渐进微压缩优先、冻结工具结果策略可借鉴；高频短周期工作流避免每次触发完整整合管道以降本。

## 总目录

- 第 1 章 简介 → DEF-01 ~ DEF-14
- 第 2 章 参考架构 → ARCH-01 ~ ARCH-29
- 第 3 章 工程原则 → PRIN-01 ~ PRIN-18
- 第 4 章 运行时 → RT-01 ~ RT-24
- 第 5 章 工具层 → TOOL-01 ~ TOOL-18
- 第 6 章 记忆 → MEM-01 ~ MEM-23
- 第 7 章 模型集成与输出治理 → MODEL-01 ~ MODEL-26
- 第 8 章 编排 → ORCH-01 ~ ORCH-26
- 第 9 章 MCP → MCP-01 ~ MCP-18
- 第 10 章 生产化 → PROD-01 ~ PROD-22
- 第 11 章 可靠性 → REL-01 ~ REL-21
- 第 12 章 安全 → SEC-01 ~ SEC-28
- 第 13 章 评测 → EVAL-01 ~ EVAL-16
- 第 14 章 未来方向（无条目，前瞻性章节）

---

## 第 1 章 简介：定义与子系统（DEF）

- **DEF-01** 系统在 LLM 与执行环境之间存在独立的 Harness 层：将 LLM 意图转化为可验证、可授权、可执行的操作，并把结果标准化回传。[1.2]
- **DEF-02** 职责边界可区分：Harness 不做业务推理/工具内部逻辑/模型优化/业务逻辑，但必须做"执行治理"——约束、验证、拒绝、重试、路由、升级人工审批。[1.2]
- **DEF-03** 存在统一工具注册表，每个工具有清晰的接口定义、权限配置、使用说明。[1.2, 1.3]
- **DEF-04** 权限为梯度化模型（Free/Ask-first/Approve-once 直至六级），高危操作可被拦截、记录意图并等待人工审批。[1.2, 2.3]
- **DEF-05** 每个工具调用都被记录、追踪、验证；工具返回意外结果（网络错误/超时/权限拒绝）时系统能识别异常并决策重试、降级或报告。[1.2]
- **DEF-06** 状态管理完整：当前步骤、已执行操作、中间结果、依赖关系可持久化，智能体中断/故障后可恢复到一致状态。[1.2]
- **DEF-07** 架构可映射到五大子系统（运行时引擎、工具层、记忆、输出治理、编排引擎）+ 安全、可观测性两大横切保障；审计时能识别缺失或混装的子系统。[1.3]
- **DEF-08** 运行时主循环完整：观察环境→理解任务→规划步骤→选择工具→执行操作→获取反馈→更新状态。[1.3]
- **DEF-09** 智能体状态机显式管理：初始化、执行、暂停、恢复、完成的状态转移有代码承载。[1.3]
- **DEF-10** 执行设有超时上限，并支持优雅中断与资源清理。[1.3]
- **DEF-11** 记忆按生命周期分三层（工作记忆/短期记忆/长期记忆+向量索引），且覆盖四项职责：存储、检索、压缩、遗忘策略。[1.3]
- **DEF-12** 输出治理覆盖五项职责：模型选择、提示词管理、输出解析、输出验证（格式/矛盾/安全边界）、降级策略（重试/调提示词/换更强模型）。[1.3]
- **DEF-13** 编排引擎覆盖五项职责：工作流定义（顺序/条件分支/并行/循环）、依赖管理、智能体分配、结果聚合、故障恢复。[1.3]
- **DEF-14** 安全是渗透式而非单模块：权限管理、沙箱隔离、输入验证（防注入）、输出过滤、审计日志分布在各子系统中。[1.3]

## 第 2 章 参考架构（ARCH）

### 分层与层间通信
- **ARCH-01** 代码模块边界符合三层+横切：接入层/编排层/智能体核心层，安全/可观测性/存储为横切关注点。[2.1]
- **ARCH-02** 接入层"薄而稳定"：仅做协议适配、身份认证、响应格式化，不含业务逻辑。[2.1]
- **ARCH-03** 编排层职责：任务分解、依赖 DAG、多智能体协调、结果聚合；简单任务可直接透传到核心层。[2.1]
- **ARCH-04** 核心层内模型调用、工具执行、记忆更新在运行时引擎同一循环中交替发生（星型拓扑），而非各自分层调用。[2.1, 2.2]
- **ARCH-05** 输出治理四步防御：JSON 解析失败→交回 LLM 自修复→语义验证（工具是否存在/参数是否合理）→安全检查（如拦截 rm -rf 类危险命令）。[2.1]
- **ARCH-06** 层间向下调用使用类型安全接口（如 Task→Result 统一契约，参数与返回值有明确类型）。[2.1]
- **ARCH-07** 层间向上反馈使用事件/回调机制（ExecutionEvent 含 timestamp/level/source/message/context），异常仅用于真正错误。[2.1]

### 运行时与工具层
- **ARCH-08** 运行时对外接口包含 initialize/step/run/pause/resume/get_state；step() 与 run() 分离，支持在每步之间插入检查点、审批或日志。[2.2]
- **ARCH-09** 执行循环六步齐全：感知（组装上下文）→推理（调 LLM）→决策（解析+验证工具调用）→执行（交工具层）→学习（写入记忆）→判断（是否继续）。[2.2]
- **ARCH-10** 终止条件至少三类：LLM 输出最终答案、最大步数限制、总执行时间限制（防无限循环/资源耗尽）。[2.2]
- **ARCH-11** 工具注册时提供结构化元数据：名称、功能描述、参数 JSON Schema、权限要求、超时限制。[2.2]
- **ARCH-12** 工具定义（ToolDefinition）与执行器分离；注册表提供 list_for_llm() 之类的导出方法封装不同厂商格式差异。[2.2]
- **ARCH-13** 工具调用必须经过统一执行管道：查找工具→权限检查→参数验证（JSON Schema）→隔离执行（超时控制）→结果标准化为统一 ToolResult。[2.2]
- **ARCH-14** 单个工具调用失败不终止整个任务循环：异常被捕获并作为错误结果反馈给 LLM 继续。[2.2]
- **ARCH-15** 执行隔离有分级实现可选：异常捕获/进程隔离/容器隔离/系统级沙箱，按操作风险选择级别。[2.2, 2.3]

### 记忆子系统
- **ARCH-16** 记忆写入分层+重要性过滤（超阈值才入长期存储）；对外统一接口（store_step/retrieve 模式）；检索支持时间优先/语义优先/混合三种策略。[2.2]
- **ARCH-17** 系统区分三类状态且不互相替代：运行时状态/事件日志（权威记录）、记忆（可检索事实/摘要/经验）、上下文（每次调模型前的投影）。[2.2]
- **ARCH-18** 星型拓扑强制：工具层与记忆子系统不直接互调，数据流全部经运行时引擎中转（可追踪/可测试/可替换）。[2.2]
- **ARCH-19** 长任务/托管场景采用三层虚拟化：Session 追加式事件日志（不可删、外部持久化、支持切片检索）、无状态 Harness 从事件流重建上下文、可替换 Sandbox；上下文溢出靠事件切片而非复杂压缩。[2.2]

### 安全与可观测
- **ARCH-20** 权限定义结构化：resource_id/resource_type/actions/conditions/expires_at + 默认级别（默认应为 Ask-first 而非 Full-trust），支持资源模式匹配。[2.3]
- **ARCH-21** 容器/沙箱执行有资源限制：内存上限、CPU 限制、网络禁用、执行超时。[2.3]
- **ARCH-22** 审计日志记录所有安全相关事件（permission_check/tool_execution/approval_request，含被拒绝的尝试），支持按条件查询与导出。[2.3]
- **ARCH-23** 可观测三支柱齐备：结构化 JSON 日志（带上下文 agent_id/task_id）、分布式追踪（trace_id/span_id/parent_span_id 树）、指标（耗时分位数 mean/p99、错误计数器、定期导出）。[2.3]
- **ARCH-24** 每次工具执行同时产出：权限检查结果+审计记录+指标数据（安全与可观测在执行路径上协同，而非事后拼接）。[2.3]

### 接口边界
- **ARCH-25** 统一消息格式：Message 含 role/type/content/message_id/parent_id/metadata/timestamp；ToolCallRequest 含 call_id/timeout_seconds/retry_count；ToolCallResponse 的 status 枚举覆盖 success/error/timeout/permission_denied。[2.4]
- **ARCH-26** Agent 不能绕过运行时直接调用工具：一切调用必经验证、权限、审计、结果回传管道。[2.4]
- **ARCH-27** 事件类型枚举覆盖任务/步骤/工具执行/权限/记忆五类生命周期；EventBus 发布-订阅模式中单个 handler 异常不中断其他 handler。[2.4]
- **ARCH-28** 消息/协议带版本字段，反序列化能处理多版本兼容。[2.4]

### 工程组织
- **ARCH-29** 代码按子系统分包组织（core/runtime/tools/memory/models/orchestration/reliability/security 等），配置从环境变量加载并提供 .env.example 模板，测试分 unit/integration 两层。[2.5]

## 第 3 章 五大工程原则（PRIN）

### 约束优先
- **PRIN-01** 设计顺序正确：先定义权限模型/约束边界，再在约束内赋能，最后有合规验证环节——审计应先查约束定义是否存在，而非只查功能。[3.1]
- **PRIN-02** 约束覆盖四维度：权限维度（可访问资源/工具/用户范围）、操作维度（绝对禁止/受限需审批/速率限制）、时间维度（可用时间窗/速率限制/凭证过期）、数据维度（大小限制/PII 处理/数据流向白名单）。[3.1]
- **PRIN-03** 敏感文件保护列表（protected files：如 ~/.ssh/id_rsa、~/.aws/credentials、/etc/shadow）在访问前拦截。[3.1]
- **PRIN-04** 危险模式检测：对 Agent 输出做正则检测（DROP TABLE、rm -rf、curl|sh、format 等）并阻断。[3.1]
- **PRIN-05** 约束实现分三级模式（白名单默认拒绝/黑名单/规则引擎条件化判断），并有运行时 ConstraintEnforcer：违反被记录、告警、可查询。[3.1]
- **PRIN-06** 约束文档化且机器可执行（如 SOUL.md 模式：绝对约束/条件约束/能力约束三类，运行时可验证、违反可追溯）。[3.1]

### 可验证性
- **PRIN-07** 可验证性三层递进：操作日志（timestamp/agent/operation/input/output/status/duration 完整字段）→执行追踪（trace_id+step+parent_step_id 因果链）→可重放（模拟模式返回记录结果；实际重放对比一致性并标记 diverged）。[3.2]
- **PRIN-08** 日志结构化（JSON 字段而非自然语言句子），且日志与追踪通过 trace_id 关联；对外能生成用户可读的执行摘要。[3.2]
- **PRIN-09** 有定期一致性验证机制：重放历史执行对比结果，发现不一致触发告警。[3.2]

### 渐进信任
- **PRIN-10** 信任等级提升有量化标准（最小操作数/成功率阈值/运行天数/无严重错误），晋升不自动、基于证据。[3.3]
- **PRIN-11** 存在降级机制：一次严重错误、短时间多次错误、安全事件、用户投诉均可触发降级；安全事件直接降回 Ask-First 级。[3.3]

### 故障假设
- **PRIN-12** 四类故障各有对应策略：临时故障→重试（指数退避+jitter+最大延迟上限+可重试错误类型判断）；永久故障→降级链（实时→缓存→默认值）；部分故障→隔离舱；级联故障→熔断器（失败阈值+OPEN/HALF_OPEN/CLOSED 状态机）。[3.4]
- **PRIN-13** 长流程任务有检查点：每步完成后保存检查点，失败时保存部分结果，支持从中间步骤恢复。[3.4]
- **PRIN-14** 健康监控与告警覆盖：错误率阈值、响应时间、任务积压、外部依赖可用性。[3.4]
- **PRIN-15** 具备无需部署的远程控制能力：远程功能开关可即时禁用故障子系统并回退；子机制自带连续 N 次失败自动熔断+恢复计时。[3.4]

### 智能体工学
- **PRIN-16** 所有工具遵循统一调用约定（相同的输入/输出模式），避免各工具接口风格碎片化。[3.5]
- **PRIN-17** 传给 Agent 的信息采用高密度格式（Markdown/JSON 替代 HTML/XML，删除视觉装饰性冗余），降低 token 消耗与解析成本。[3.5]
- **PRIN-18** 约束是风险基而非习惯基：审批/检查挂在实际风险点（金额阈值、AML 合规检查）上，而非照搬人工流程做全链路人工审批；操作后自动记录审计而非人工填报。[3.5]

## 第 4 章 运行时（RT）

- **RT-01** 核心循环必须实现为 Think-Act-Observe 迭代循环：工具结果作为"观察"写回上下文供下一轮决策，不允许一次性链式规划后盲执行。[4.1]
- **RT-02** 循环终止条件必须显式编码且覆盖：最大轮数上限（典型10-30轮）、token 预算耗尽、显式停止信号（用户取消/超时/停止标记）、目标达成；每次终止必须记录 termination_reason。[4.1]
- **RT-03** 流式模式下工具参数必须累积 input_json_delta 至 content_block_stop 且 JSON 解析完整后才调度执行，禁止半截 JSON 触发工具。[4.1/4.3]
- **RT-04** 上下文必须满足前缀一致性以支撑 prompt cache：MCP 工具列表按确定顺序枚举（顺序漂移=缓存未命中，Codex 真实 bug）；运行中配置变更不得改写历史，只能追加消息生效。[4.1]
- **RT-05** 消息体系必须类型化：ToolUseBlock{id 自动生成,name,input}；ToolResultBlock{tool_use_id 与 use id 匹配,content,is_error,error_type}；Message 含 timestamp+message_id 且序列化可 round-trip。[4.2]
- **RT-06** 消息窗口必须有 max_messages+max_tokens 双限制，超限从最早丢弃；系统上下文单独注入不计入限制。[4.2]
- **RT-07** 流式事件必须是完整有序、可序列化为 JSON 的事件流（agent_start→turn_start→content_block_start→delta→…→message_end→turn_end→agent_end + ERROR 事件），事件带 timestamp/metadata。[4.3]
- **RT-08** 事件通道必须实现背压：有界队列（maxsize≈100），put 满则阻塞生产者、get 带超时（≈30s），防内存溢出。[4.3]
- **RT-09** 错误处理必须按类型差异化：工具执行错误→作为观察反馈、不中断循环；超时→指数退避重试（1/2/4s，≤3次）；权限错误→不重试立即反馈；RateLimitError→按 retry_after 等待。[4.4]
- **RT-10** 一切错误必须构造 ToolResultBlock(is_error=True, error_type=异常类名) 反馈给 Agent（"错误即观察"），不得静默吞掉。[4.4]
- **RT-11** 外部依赖必须有熔断器：failure_threshold≈5、recovery_timeout≈60s、closed/open/half_open 三态，防级联失败。[4.4]
- **RT-12** 上下文溢出必须有专门恢复路径：先压缩（摘要长 assistant 响应→移除最早消息）再按更低 token 目标重试，不得直接终止。[4.4]
- **RT-13** 必须实现漂移检测信号：近5轮关键词相似度阈值（0.3）、窗口内同一工具重复调用>2次、总工具调用数>50（范围蠕变）。[4.5]
- **RT-14** 必须有结构化纠正机制：每≈5轮强制反思提示；定期保存完整检查点支持回滚；每轮推理前约束验证（工具调用≤50、消息总量≤1M字符、时长≤1h，违例终止）。[4.5]
- **RT-15** 上下文重置路径：同一行动重复3-5次或历史>窗口70% 时，先把关键事实（原始目标/已验证进展/规则/失败模式）持久化到外部存储，再清空重载；宁可直接重置而非增量压缩。[4.5]
- **RT-16** token 预算必须显式分解（系统提示/消息历史/工具Schema/用户输入/输出预留/安全裕度≈10K），按模型实际上限计算；禁止硬编码统一窗口常量。[4.6]
- **RT-17** 每次推理请求前必须前向估计：预留 expected_output_tokens（≈2000），计算 recommended_max_tokens=min(可用,阈值)；超限先自动压缩再构建消息。[4.1/4.6]
- **RT-18** 自动压缩必须有明确触发阈值与目标（示例：0.8 触发、压到预算50%）；压缩前先把关键事实刷写长期记忆。[4.6]
- **RT-19** 预算必须三级控制：Per-Request（输出上限）、Per-Task（调用次数+累计token，超限强制总结终止）、Per-Day/Month（金额，超限排队/降级/拒服务）；每次调用前 check_budget、后 record_usage。[4.6]
- **RT-20** 控制平面与数据平面分离：控制信号走独立队列，Agent 仅在安全点（工具调用前后等）检查。[4.8]
- **RT-21** 检查点必须含完整可恢复状态（agent_state/message_history/tool_context/execution_context/iteration/safe_point/metadata）；序列化仅 JSON 显式字段，反序列化不得执行任意代码。[4.8]
- **RT-22** 暂停/恢复语义：request_pause 仅在安全点生效且带 deadline（≈10s）防无限等待；终态不可暂停；resume 从指定检查点恢复。[4.8]
- **RT-23** 必须有硬性成本安全阀：CostBudget{max_tokens,max_api_calls,max_duration_seconds} 每轮检查 + kill_switch 强制终止。[4.8]
- **RT-24** 必须支持干预点钩子：BEFORE/AFTER_LLM_CALL、BEFORE/AFTER_TOOL_CALL、AT_TASK_BOUNDARY、BEFORE_STATE_COMMIT；处理器可阻断（proceeded=False）或注入修改。[4.8]

## 第 5 章 工具层（TOOL）

- **TOOL-01** 所有工具必须实现统一接口：async call(input)、name()、description()、input_schema()（JSON Schema）、check_permissions(context)、get_progress()（step/total/status/ETA）；长时工具执行中持续发出进度事件（step/total_steps/percentage/ETA）。[5.1/5.2]
- **TOOL-02** Shell 执行工具必须：命令白名单 allowed_commands；shlex.split + subprocess(shell=False)（禁止 shell=True 拼接）；subprocess 带 timeout；输出附 returncode+execution_time。[5.1]
- **TOOL-03** 文件类工具必须做路径校验（基于 base_path 的 PathValidator 防目录逃逸），源/目标拒绝符号链接。[5.1]
- **TOOL-04** 工具注册中心 register 时必须缓存 {name,description,input_schema}，并提供 get/list_tools/get_tool_schema/tool_exists/unregister。[5.1]
- **TOOL-05** 工具授权必须支持三态策略 ALLOW/DENY/REQUIRE_APPROVAL，按 agent_id×tool_name 粒度配置。[5.1]
- **TOOL-06** 工具执行必须走固定6阶段管道：查找→权限检查→输入验证→执行→结果处理→记录/缓存；任一阶段失败立即早返回错误，每阶段耗时写入 execution_record。[5.2]
- **TOOL-07** 输入验证必须在执行前完成：required 缺失、unknown 字段、类型不符（jsonschema.validate），失败返回 ParameterValidationError 而非执行。[4.4/5.2]
- **TOOL-08** 工具执行必须带超时（asyncio.wait_for，默认30s）。[5.2]
- **TOOL-09** 工具结果必须序列化并截断至 max_result_size（默认1MB），截断时附截断标记与原始大小。[5.2]
- **TOOL-10** 每次执行必须写入 execution_history 审计记录（tool_use_id、各阶段耗时、错误），支持按 id 查询；结果缓存必须有 TTL 清理（默认3600s）。[5.2]
- **TOOL-11** 并发工具调用必须有并发上限（Semaphore≈5）+ gather(return_exceptions=True)；单工具异常转该工具的 is_error 结果、不中断整批；结果与 tool_use 列表一一对应。[4.3/5.2]
- **TOOL-12** 工具必须按类别差异化控制：执行类（严格权限+超时）、网络类（速率限制+重试+令牌管理）、智能体类（递归深度限制）、专用类。[5.3]
- **TOOL-13** 每工具必须标注访问级别（SYSTEM/ADMIN/TRUSTED/PUBLIC）与资源限制（max_execution_time/max_memory/max_disk_usage），执行前 can_execute 按主体角色判定。[5.3]
- **TOOL-14** 工具元数据必须完整（ToolCapability）：input/output schema、access_level、required_permissions、estimated_execution_time、estimated_resource_usage、examples、limitations、dependencies。[5.3]
- **TOOL-15** 横切能力用装饰器叠加（timeout_wrapper、retry_wrapper 指数退避）不侵入工具本体；组合用 CompositeToolChain 声明式 input_mapping 数据流。[5.3]
- **TOOL-16** 工具加载必须支持按上下文延迟（shouldDefer）与多级优先级（系统>会话>用户>领域>内置>动态发现），不得静态硬编码。[5.4]
- **TOOL-17** 动态导入的自定义工具必须限制在受信任命名空间前缀（module.startswith(allowed_prefixes) 通过才 importlib 导入）。[5.1/5.4]
- **TOOL-18** 工具 Schema 必须缓存（TTL≈3600s）；文件状态类结果用短 TTL 缓存（≈60s）+ 按模式 invalidate，减少重复调用。[5.4]

## 第 6 章 记忆（MEM）

### 记忆分层
- **MEM-01** 记忆必须分为三层：工作记忆（完全委托 LLM 上下文窗口）、短期记忆（会话缓存）、长期记忆；长期记忆需再分为结构化（MEMORY.md 类文件）、向量索引、按日期分片的会话日志三个子系统，而非单一存储。[6.1, 06章引言]
- **MEM-02** 短期记忆缓存必须有容量上限（示例：最近 10 个会话摘要，JSONL 格式），超出后删除最旧条目，并能按 workflow/会话过滤快速取回最近 N 条。[6.1.3]
- **MEM-03** 向量索引的 embedding 维度必须与所用 embedding 模型一致（如 text-embedding-3-small=1536、text-embedding-3-large=3072、voyage-3-large=1024），且维度应可配置而非硬编码。[6.1.4]
- **MEM-04** 向量库选型与规模匹配：Hnswlib(<1M)、Qdrant(1M-10M)、pgvector(<5M，已有 PG 时)、Weaviate(10M+)；检索需返回相似度分数供上层排序。[6.1.4]

### 可写记忆
- **MEM-05** 可写记忆需满足四个特性且各有对应实现：主动性（Agent 自主决定写入时机）、原子性（写入操作原子，无部分更新状态）、可审计性（每次写入留痕、支持版本回滚）、并发安全（多会话并发写不冲突）。[6.2.1]
- **MEM-06** 记忆文件必须带结构化元数据（示例为 Frontmatter）：type、version（每次更新递增）、last_modified(ISO 8601)、modified_by(会话标识)、confidence(0-1，用于搜索排序)、expiry(支持自动清理)、tags。[6.2.2]
- **MEM-07** 记忆必须按语义类型分类并各有更新策略：user（用户档案）/ feedback（用户反馈）/ project（项目上下文）/ reference（可复用代码命令）/ episodic（事件记录）。[6.2.2, 6.2.4]
- **MEM-08** 写入决策需有分级信号与阈值：显式信号（用户说"记住这个"、错误教训、里程碑，优先级高）、隐式信号（新偏好、系统性问题）、实时信号（会话结束总结），综合评分超阈值（示例 0.6）才写入，防止记忆膨胀。[6.2.3]
- **MEM-09** 记忆检索必须是混合检索：关键词检索（精确事实）+ 向量检索（语义泛化），融合时先返回双路命中（交集）再返回单路命中（并集-交集）。[6.2.5]
- **MEM-10** 并发写入冲突解决：基于版本号的原子写（expected_version 比对）；版本冲突时执行 3-way merge（基础版/当前版/新版），双向改动标记冲突而非静默覆盖。[6.2.6]
- **MEM-11** 每次记忆写入后必须触发异步验证与索引更新（向量+关键词），保证检索不出现不一致状态；对 Agent 暴露的记忆写入接口应按类型分方法（save_user_profile/record_feedback/record_lesson 等）。[6.2.7]

### 上下文组装
- **MEM-12** 上下文必须区分静态（用户档案、系统能力、工具定义——会话内不变、可缓存）与动态（相关历史、项目进度、执行结果——按需实时生成），静态部分缓存避免重复计算。[6.3.2]
- **MEM-13** 上下文组装须为三阶段管道：①需求分析（识别本次查询需要哪些记忆源）②并行检索各记忆源 ③合并排序填充。不能简单拼接全部来源。[6.3.3]
- **MEM-14** 合并时须按固定优先级排序填充：系统提示 > 用户档案 > 项目信息 > 历史 > 参考资料，保证预算不足时关键信息不被截断。[6.3.3]
- **MEM-15** 组装须在 token 预算约束下进行：逐段估算 token 累加，超预算时对低优先级段截断（并标注 truncated），剩余空间不足最小值（示例 100 tokens）时放弃填充。[6.3.3]
- **MEM-16** 须有"保护区"机制：系统提示 + 最新 1-2 条用户消息放在固定保护区（示例约 7% 窗口），永不被动态内容侵占/截断。[6.3.4]
- **MEM-17** 若采用插件化上下文引擎：每个记忆源一个插件（name/priority/generate 接口），单插件生成失败须 try/except 降级不影响整体，剩余 token 低于最小阈值（示例 500）时停止调用后续插件。[6.3.5]
- **MEM-18** 上下文缓存须有失效管理：TTL 过期 + 按标签批量失效（用户档案变更→失效 user_profile 标签的全部缓存条目）。[6.3.6]

### 记忆整合/巩固
- **MEM-19** 整合触发须为三门或逻辑（任一满足即触发）：时间门（距上次整合>24h）、会话门（累计>5 个新会话）、显式触发（用户/系统 API）。触发器须在整合成功后重置计数器和时间戳。[6.1.2, 6.4.2]
- **MEM-20** 整合须为四阶段标准化管道：Orient（分析目标定范围，按项目相关性决定 deep/shallow）→ Gather（按类型提取：偏好/进度/教训/决策/错误）→ Consolidate（合并去重、冲突解决、更新索引）→ Prune（清理）。[6.1.2, 6.4.2]
- **MEM-21** Prune 须至少含三类策略：①过期删除（示例：>6 个月/180 天的会话摘要、按 expiry 字段）②相似度去重（示例阈值 0.95）③低相关性过滤（与当前 workflow 相关度<0.6 的偏好清除）+ 置信度阈值清理。[6.1.2, 6.4.2]
- **MEM-22** 整合持久化须为 WAL 风格：整合前深拷贝原始状态，先写临时文件再原子重命名；失败时回滚内存状态且不落盘，失败计数累加（>3 次告警人工介入），状态更新仅在持久化成功后执行。[6.4.2]
- **MEM-23** 须有被动式自动刷写兜底：上下文占用达到阈值（示例 70%）时把关键事实落盘长期记忆再清空日志；删除记忆时须同步删除向量索引与关键词索引条目；按 last_accessed 定期清理长期不访问记忆（示例 90 天）；并监控整合指标（耗时、压缩率、记忆库增长、搜索延迟、未命中率）。[6.4.3, 6.4.4, 6.4.5]

## 第 7 章 模型集成与输出治理（MODEL）

### 模型抽象层
- **MODEL-01** 须有统一 Provider 接口（Protocol/接口类），至少覆盖：complete（非流式）、stream（流式）、estimate_tokens、validate_config（启动时验证 API key 等配置）。[7.1.3]
- **MODEL-02** 模型选择须配置驱动：primary + fallback_chain（有序回退列表）+ 可选 cost_threshold/latency_threshold（超限切换）；配置外置于代码（JSON 等业务逻辑无关）。[7.1.3]
- **MODEL-03** 每个模型实例须配独立熔断器：连续失败达阈值（示例 5 次）进入 open（拒绝请求），冷却期（示例 60s）后 half-open 试探，成功才恢复 closed；选择引擎按熔断器状态挑第一个可用模型，全部不可用须显式抛错。[7.1.3]
- **MODEL-04** 回退不得静默保持权限：模型可用性回退（换模型）必须伴随权限收缩——CapabilityProfile（context_tokens/structured_output/tool_calling 等能力档位）映射到 AuthorityProfile（may_recommend/may_auto_approve/may_execute_external 权限上限），选择引擎须返回 (provider, authority, degraded) 三元组，让上层无法忽略降级事实。[7.1.4]
- **MODEL-05** 任务须分可降级/不可降级两类：可降级（提取、摘要、草稿、低风险建议）回退期间继续执行；不可降级（高价值执行、不可逆操作、受监管决策）回退期间必须暂停自动路径、显式返回"自动决策当前不可用"并转人工，而非保证服务可用。[7.1.4]
- **MODEL-06** 降级期间须留运行记录（哪些请求在降级状态处理、是否有本该人工的决策被自动放行、是否需回溯复核）；主模型恢复须过显式门槛（健康检查+抽样回归+积压高风险决策处理完）才恢复权限。[7.1.4]

### 输出解析
- **MODEL-07** 输出解析须区分内容块类型（text / tool_use / thinking），解析后消息须提供 text_content()、tool_calls()、thinking_content() 等类型安全访问器，不把原始字符串直接透传。[7.2.2]
- **MODEL-08** 流式解析须正确处理工具参数分片：input_json_delta 只能累积到 partial_json_buffer，等 content_block_stop 才整体 json.loads；解析失败须记录错误并以空对象兜底（或显式失败），不得让单个坏事件中断整条流。[7.2.2]
- **MODEL-09** 每个工具的参数须有 schema（示例 Pydantic BaseModel + field_validator 范围校验），解析后强制校验：未知工具名抛错、参数类型/范围不符抛带字段定位的错误信息。[7.2.2]
- **MODEL-10** 解析管道须检查 stop_reason：为 max_tokens 时必须视为截断错误抛出，不得当作完整结果使用。[7.2.2]

### 质量门控
- **MODEL-11** 工具执行前须过六层门控（任一 FAIL 即拒绝执行）：①格式检查（id/name/input 非空、命名规范）②工具存在性（已注册且 enabled）③参数校验（schema）④业务逻辑（频率/大小/域名等规则）⑤权限检查 ⑥参数注入检测。每层返回结构化 ValidationReport（PASS/WARN/FAIL + errors + suggestion）。[7.3.2]
- **MODEL-12** 工具不存在时须用编辑距离给出相似工具建议（示例 cutoff 0.6），供自修正使用，而非仅报错。[7.3.2, 7.4.2]
- **MODEL-13** 业务逻辑检查须覆盖：调用频率限制（示例 100 次/分钟）、文件大小上限、HTTP 请求域名白名单。[7.3.2]
- **MODEL-14** 注入检测须扫描参数字符串中的危险模式（示例：rm -rf、DROP TABLE、<script>、${jndi:），命中即 FAIL。[7.3.2]
- **MODEL-15** 权限检查须独立于参数校验：每个工具声明所需权限集合（read/write/delete/execute），校验用户权限是否为超集；权限策略应消费统一决策服务而非门控内自建第二套。[7.3.2]
- **MODEL-16** 复杂任务须采用 Planner-Generator-Evaluator 三角色分离：规划者（高推理预算，产出计划+验证标准）、生成者（执行）、评估者（独立验证），评估者必须使用独立提示和上下文，不包含生成过程信息，以消除自我确认偏差。[7.3.3]
- **MODEL-17** 评估失败须有分级重试路径：小问题反馈给生成者修正重试；多次生成失败反馈给规划者重新规划；重试耗尽须抛显式错误而非无限循环。[7.3.3]
- **MODEL-18** 使用 LLM 评估须防范 judge 偏差（位置偏差/长度偏好/风格偏好/self-preference）：采用多评估者投票或定期人工抽样验证。[7.3.3]

### 幻觉检测
- **MODEL-19** 须有三层幻觉检测且按序短路：层1 工具名（不存在即返回，不继续查参数）→ 层2 参数 → 层3 事实。每条检测结果须结构化：is_hallucination/confidence/hallucination_type/evidence/correction。[7.4.2]
- **MODEL-20** 参数幻觉检测须覆盖：未在 schema 定义的参数、数值超 min/max、值不在 enum 列表——合法但不合理也算幻觉。[7.4.2]
- **MODEL-21** 事实幻觉须有 FactChecker 机制：对特定工具的关键事实参数（示例：API 端点是否在已知列表、权限声明是否与权限存储一致）对照知识库核实。[7.4.2]
- **MODEL-22** 检测到幻觉须走自修正而非直接失败：把 evidence + correction 构建成纠正提示追加到对话历史，让模型重新生成；自修正结果仍须再过检测，且须有尝试次数上限。[7.4.2]

### 推理预算
- **MODEL-23** 推理投入须支持四档策略并可配置：DISABLED（简单任务不思考）/ ADAPTIVE（模型自决）/ BUDGET_BASED（按成本+复杂度条件触发）/ REQUIRED（关键任务强制思考）。[7.5.2]
- **MODEL-24** 须有任务复杂度评估函数（基于任务类型映射 + 输入规模），作为是否启用思考的输入；预算制策略须同时检查成本余量与会话思考 token 余量。[7.5.2]
- **MODEL-25** 须有会话级预算跟踪器：累计 thinking_tokens、总成本、请求次数、平均思考占比；每次调用后记账，提供 get_budget_status()（used/limit/remaining）。[7.5.2]
- **MODEL-26** 强制思考（REQUIRED）须限定关键任务类型清单（示例：security_decision/financial_transaction/code_review/medical_advice），并对思考深度做下限校验（示例：思考内容≥500 字符否则视为不深入）；计费上思考 token 已含在 output_tokens 内（与输出同价），不得重复计费；thinking 参数须按模型兼容性分支（旧模型仅支持 enabled+budget_tokens，新模型仅支持 adaptive，传错返回 400）。[7.5.2, 7.5附注]

## 第 8 章 编排（ORCH）

### 任务分解
- **ORCH-01** 任务依赖建模为 DAG，加载/提交时必须校验：所有被依赖任务存在（缺失即报错）、无循环依赖（DFS检测）；存在拓扑排序确定执行顺序。[8.1]
- **ORCH-02** 任务 ID 全局唯一且格式规范（{parent_id}#{task_type}#{sequence}），嵌套任务可追溯父链。[8.1]
- **ORCH-03** 任务生命周期为显式五态 pending→running→completed|failed|killed；failed→重试→running、killed→重新入队→pending 等转移由引擎统一驱动，任务不得自行改状态。[8.1]
- **ORCH-04** 每个任务定义必须含 timeout_seconds（默认300）与 max_retries（默认1），并有 per-task on_failure 策略（fail-fast vs continue）。[8.1]
- **ORCH-05** 任务粒度有书面规约：单任务预期执行 10秒~10分钟；分解时区分数据依赖与控制流依赖。[8.1]
- **ORCH-06** 调度器支持并行分组：同层任务互不为依赖方可并行，且存在断言保证所有任务都被分配（无孤立节点）。[8.1]

### 状态机
- **ORCH-07** 工作流用显式状态机定义（YAML或代码），状态类型枚举 initial/normal/final/error/wait/parallel；initial 有且仅一个，final 可多个。[8.2]
- **ORCH-08** 条件分支必须存在默认兜底分支（condition: true）；重试自环必须绑定尝试计数器并与 max_retries 比较，耗尽后进入显式 error 状态。[8.2]
- **ORCH-09** 工作流引擎有全局迭代上限（如 max_iterations=100）防失控。[8.2]
- **ORCH-10** 每步状态转移写入 execution_history（timestamp、state、context 快照），形成可审计执行历史与决策日志。[8.2]
- **ORCH-11** 标记 side_effect:true 的动作执行前必须暂停等待人工审批（pending_approvals），审批通过后恢复执行；恢复时必须跳过已执行动作（审批后幂等续跑，否则重复挂起）。[8.2]
- **ORCH-12** 工作流中的 tool_call 仅为意图声明；实际执行必须进入运行时的权限校验、参数验证、审计与追踪路径。[8.2]
- **ORCH-13** 支持检查点持久化（current_state+context+execution_history+timestamp）与从检查点恢复。[8.2]
- **ORCH-14** error_handlers 按状态+错误类型分级（验证错误→notify+fallback_state；工具失败→retry_with_backoff 上限3；超时→kill_workflow），支持通配 on_state:"*"。[8.2]
- **ORCH-15** 声明式工作流应具备确定性执行（同输入同路径同输出）；确定性子流程封装为可测试、可版本化的模块（"外层裁量、内层确定"）。[8.2]

### 多 Agent
- **ORCH-16** 编排配置中每个 agent_invoke 状态显式声明：专属 system_prompt、max_tokens、tools、timeout、on_success/on_failure 转移；阶段间上下文传递显式（input_from / state_outputs），禁止隐式共享可变全局状态。[8.3]
- **ORCH-17** 并发子 Agent 数量有上限（parallel_workers/Semaphore max_concurrent），每任务独立超时；批量并发用 gather(return_exceptions=True)，单 Agent 异常不得崩掉整批。[8.3]
- **ORCH-18** Agent 失败以结构化 error_event（type/source/state/error_type/error_message/timestamp）广播；每个 agent 可注册错误处理器，决策为 retry（指数退避 2^attempt，上限3）/fallback（降级到缓存或备选结果状态）/abort（抛 WorkflowAbortedError），可配通知渠道。[8.3]
- **ORCH-19** 上下文层级隔离：子 ExecutionContext 继承父级 local_vars 的只读副本；set 只写本地作用域；向父级回传必须显式 commit_to_parent(keys)。[8.3]
- **ORCH-20** 关键产出走 Planner/Generator/Evaluator 三角色分离 + 对抗评估闭环：评估者多维打分（1-5），平均分低于阈值（如4.0）触发带反馈的再生成，迭代有上限（2-3次）并记录每轮 iteration_history。[8.3]

### Agent 间通信
- **ORCH-21** Agent 间消息有统一结构：id、source_agent_id、target_agent_ids、message_type、payload、priority(LOW/NORMAL/HIGH/CRITICAL)、timestamp、ttl_seconds、require_acknowledgment；跨渠道（webhook/email/slack/db）经 ChannelAdapter 转换但不破坏该结构。[8.4]
- **ORCH-22** 可靠通信三原则落地：至少一次送达（失败重试）；幂等消费（重复消息不产生重复副作用）；确认机制（send 等待 ACK，超时5s+指数退避+重试上限3，未确认消息保留 pending）。[8.4]
- **ORCH-23** 需要顺序保证的场景使用按键分区的 FIFO 队列。[8.4]
- **ORCH-24** 实现背压：每接收方有队列上限（如1000），发送前 can_send() 检查容量，并向发送方反馈积压率（0-1）。[8.4]
- **ORCH-25** 共享内存区（Scratchpad）线程安全（RLock）；条目含 writer_id、version（写时+1）、read_only 标志；只读键拒绝覆盖/删除；全部读写删操作记录 access_log（含双端ID、时间戳、版本）。[8.4]
- **ORCH-26** 外发渠道（如 webhook）配置重试策略（max_retries 3 + 指数退避）；路由规则（trigger→channels+format+priority）显式配置。[8.4]

## 第 9 章 MCP（MCP）

- **MCP-01** 集成必须是 Host/Client/Server 模型：MCP Client 由 Host 统一管理，与每个 Server 一对一隔离连接；只消费 Tools/Resources/Prompts 三原语。[09]
- **MCP-02** Server 清单必须配置驱动（name/command/args/transport: stdio|streamable_http）而非硬编码；每 Server 配健康检查（interval≈30s+timeout≈5s）并持续监控。[9.1]
- **MCP-03** 每个请求必须在 params._meta 携带 protocolVersion+clientCapabilities（clientInfo 建议）；能力声明收敛到统一请求构造函数，禁止散落调用点（漏发→-32602/HTTP 400）。[9.1]
- **MCP-04** 必须实现版本探测降级：先按新版发（HTTP 带 MCP-Protocol-Version 头）；400 时查响应体，可识别新版错误（-32020/-32022）纠正重试，无法识别才回退 initialize 握手；stdio 用 server/discover 探测。[9.1]
- **MCP-05** 注册时用 server/discover 一次获取 supportedVersions/capabilities/instructions/ttlMs/cacheScope；不支持目标版本则停用该 Server（enabled=false）。[9.1/9.4]
- **MCP-06** 不得依赖连接级会话：Server 不得从同一连接历史请求推断状态，跨请求状态必须由客户端显式标识符回传；长期存活的 stdio 进程≠会话。[9.1]
- **MCP-07** 结果处理必须兼容 resultType：缺失按 "complete"（旧 Server）；仅 prompts/get、resources/read、tools/call 可返回 input_required。[9.1/9.4]
- **MCP-08** MRTR 输入回传必须闭环：input_required→按 inputRequests 键收集→inputResponses（键一致）+ requestState 原样回传→用新 JSON-RPC id 重试原请求；必须设轮数上限（默认10）防坏 Server 拖死。[9.4]
- **MCP-09** requestState 按攻击面处理：客户端只透明转发不解析；自研 Server 若其影响授权/资源/逻辑，必须 HMAC/AEAD 完整性保护+绑定调用主体+短有效期+防重放。[9.4]
- **MCP-10** -32021 能力不足必须读 data.requiredCapabilities 给出可诊断配置提示；此类错误重试无效。[9.1/9.4]
- **MCP-11** Schema 必须多级缓存（L1 内存/L2 磁盘/L3 远程），新鲜度优先用服务端 ttlMs、共享范围用 cacheScope；旧版 Server 回退哈希比对+变更通知。[9.1/9.4]
- **MCP-12** cacheScope=private 的缓存必须按授权上下文隔离，禁止跨上下文复用；无授权上下文的私有结果只用不存。[9.4]
- **MCP-13** 每次调用前必须过权限网关：按 server→tool 配 allowed/denied（路径 glob/表/操作枚举）；匿名调用默认拒绝；高风险操作人工审批（带超时≈30s，超时即拒绝）。[9.1/9.4]
- **MCP-14** 全部调用必须记审计日志（timestamp/action/agent_id/tool_name/arguments/reason），覆盖 allowed/denied/rejected/timeout 全分支，可按时间导出。[9.4]
- **MCP-15** 必须有错误降级：按工具注册 fallback handler，主调用失败→降级执行→结果标记 source=primary|fallback；单 Server 故障不得拖垮工作流。[9.4]
- **MCP-16** 调用必须带超时+重试（timeout≈30s、max_retries 2-3）与幂等原子性：状态临时文件+原子 rename，重复执行返回缓存结果。[9.1/9.4]
- **MCP-17** 大资源读取必须有边界：二进制放 BlobResourceContents.blob（base64）不透传 body；超大文件分页/资源模板/对象存储引用；Accept 同时声明 json 与 SSE 并按 Content-Type 分支解析；HTTP 连接池上限+超时。[9.1]
- **MCP-18** 弃用特性要有迁移台账：Roots/Sampling/Logging 弃用未移除（≥12个月窗口）；HTTP+SSE 传输应优先迁移；logging/setLevel 与 roots/list_changed 已移除，日志级别改走每请求 _meta。[09/9.1]

## 第 10 章 生产化（PROD）

### 系统提示词
- **PROD-01** 系统提示词模块化：至少分 Core Identity（不可缓存）/Capabilities/Domain Knowledge（可缓存）/Context-Specific（动态）四层，每段标记 cacheable 与 priority。[10.1]
- **PROD-02** 静态模块在会话内必须字节级不变：{{变量}}替换只允许发生在动态模块；高频变化内容置于提示词末尾——否则前缀缓存（要求字节完全一致，一个空格差异即失效）永不命中。[10.1]
- **PROD-03** 提示词组装顺序固定（identity→capabilities→knowledge→context），组装器输出缓存元数据（cache_key=sha256(static_part)、静态/动态 token 估算、缓存边界）。[10.1]
- **PROD-04** Token 预算控制：总量超限告警；预算压缩按优先级贪心裁剪，priority>=10 的段落永不删除。[10.1]
- **PROD-05** 提示词变更纳入版本控制，且变更须先经评估测试再上线；利用 cache_control ephemeral 标记与缓存预热。[10.1]

### 插件/扩展
- **PROD-06** 扩展体系四层：Plugin（自描述 manifest+独立版本）> Skill（原子能力；工具型 Skill 必须声明参数 JSON Schema+required+返回类型）/ Hook（流程插桩）/ Command（用户入口）。[10.2]
- **PROD-07** 插件 manifest 必须声明 permissions[{resource,action,level}]，运行时经权限校验；权限至少分 Free/Ask-first/Approve-once 三级。[10.2]
- **PROD-08** 关键插桩点齐全：before_tool_call（参数验证/转换）、tool_result_persist（审计日志）、before_model_resolve/before_prompt_build（提示词与上下文注入）、agent_end（资源清理）、command:new（命令参数校验）。[10.2]
- **PROD-09** 技能调用链路固定：前置钩子→处理器→结果持久化钩子；插件加载走 Discover→Load→Validate→Register 生命周期并校验 manifest。[10.2]
- **PROD-10** 插件隔离：每插件独立 env_vars、文件访问白名单、api_quota；版本兼容性检查+依赖拓扑排序加载；热更新须先向后兼容检查再 pause→unload→load→resume。[10.2]
- **PROD-11** "错误作为观察"：工具错误不抛异常打断流程，转为结构化观察 {type:"tool_error", skill, error_code, message, timestamp, recoverable, suggested_action} 注入观察流。[10.2]
- **PROD-12** Skill 作为可版本化知识资产：Skill 变更走评估流水线（成败轨迹采集→优化器小范围编辑→held-out 任务集决定接受），线上只加载最终文件。[10.2]

### 性能与成本
- **PROD-13** 工具 Schema 用哈希缓存（sha256 前16位→占位符），重复请求不重发完整 Schema；区分并分别度量三层缓存：Schema 缓存（应用层）、前缀缓存（推理层）、结果缓存（请求完全匹配）。[10.3]
- **PROD-14** LLM 响应流式输出降低首字节延迟，流处理带背压的分块缓冲；无依赖的工具调用并发执行且各有独立超时。[10.3]
- **PROD-15** 模型路由按任务复杂度分级（简单→低价/中等→中档/复杂+长上下文→高档），路由规则显式可配置；选择器按"能力≥复杂度 且 成本≤预算，取最低成本"。[10.3]
- **PROD-16** 成本三级预算（per-request/per-task/daily），50%与80%阈值告警；CostMonitor 逐请求记录 {model,cost,tokens,timestamp} 并按模型统计。[10.3]
- **PROD-17** 北极星指标为 cost_per_successful_task（≈C_token×E[尝试次数]/成功率），而非总 token 或总成本；缓存命中率按工具统计并有 TTL 调优规则（命中率>0.7→86400s、>0.4→3600s、否则600s）。[10.3]

### 配置与特性门控
- **PROD-18** 配置三层优先级：环境变量（统一前缀，最高）>项目配置>全局配置，逐层覆盖；环境变量做类型推断（true/yes/1→bool、数字→int）。[10.4]
- **PROD-19** 配置显式枚举 environment（development/testing/staging/production）；超时、重试、缓存、日志级别等行为差异由配置驱动而非散落硬编码；项目配置可含 features{} 与 rate_limits{}。[10.4]
- **PROD-20** 特性开关有强制命名约定（如 tengu_ 前缀，违规抛错）；未知 flag 默认关闭；flag 有缓存与定期刷新。[10.4]
- **PROD-21** 运行时门控支持按用户哈希的百分比灰度（hash(user_id:feature)%100 < pct）与属性分流。[10.4]
- **PROD-22** 灰度分阶段推进（PAUSED 0%→CANARY 10%→25%→50%→100%），auto_advance 基于 record_metric(success)；错误率超阈（如0.05）且样本≥100 才自动回滚。[10.4]

## 第 11 章 可靠性（REL）

### 目标与可观测性
- **REL-01** 系统有显式可靠性 SLO 并被持续度量：可用性99.9%、MTBF>1000h、MTTR<5min、错误率<0.1%、P99<10s。[11.0]
- **REL-02** 可观测三支柱齐备：Metrics（counter/gauge/histogram 带标签，含P50/P99）、结构化 JSON 日志（timestamp/level/service/message+结构化字段）、Traces（trace_id+span树：span_id/parent_span_id/operation_name/duration_ms/tags/status ok|error）。[11.1]
- **REL-03** Agent 专属指标存在：tool_calls_total{tool,success}、tool_call_duration_ms、tool_tokens_used、agent_iteration_ms、agent_tokens_per_iteration、agent_tools_per_iteration。[11.1]
- **REL-04** 工具执行统一埋点模式：入口开 trace+span，finally 中 span.finish()，成功/失败分别记 metrics+logs，异常记 critical 且 span.status=error。[11.1]
- **REL-05** Agent 决策有日志：log_agent_decision(agent_id, decision, reasoning, confidence)——决策、理由、置信度三要素齐备。[11.1]
- **REL-06** 健康状态由错误率阈值推导（>1% warning、>5% critical）并有聚合仪表（成功率/平均延迟/P99/总token）。[11.1]
- **REL-07** 可观测性驱动控制闭环：关键子系统可经远程 feature flag 禁用/降级而无需发版；告警基于与历史基线偏差（如>3倍中位数）；P99 连续5次超阈自动降级。[11.1]

### 反馈闭环与 HITL
- **REL-08** 危险操作执行前经风险评估：加权因子（数据敏感性0.3、财务影响0.25、不可逆性0.2、置信度缺口0.15、授权状态0.1）×100 得分分级；LOW 自动执行/MEDIUM 人工审批/HIGH 需求确认/CRITICAL 升级+留痕；敏感参数（password/token/secret/key等）与不可逆动作（delete等）有专项规则。[11.2]
- **REL-09** 审批请求为状态机（pending/approved/rejected/needs_clarification/expired），含 required_approvals（CRITICAL≥2）、approval_timeout（默认60min）过期检查与事件回调。[11.2]
- **REL-10** Ask-first 决策有记忆与失效：用户决策记住24h或直至上下文变化后重新询问；信任分级模型（Manual-only…Full-trust 六级）显式可配。[11.2]
- **REL-11** 用户反馈结构化采集（positive/negative/correction/partial/irrelevant + rating 1-5 + category），并按负面反馈频率聚合出改进领域排序。[11.2]
- **REL-12** 长任务支持运行时指令注入：独立控制通道与数据通道并行、优先级队列（URGENT/HIGH/NORMAL/LOW），仅在安全点（before_llm、after_tool_exec、task_boundary、before_state_update）处理，支持 STOP/PAUSE。[11.2]

### 容错与恢复
- **REL-13** 外部调用具备四模式级联 retry→circuit breaker→bulkhead→timeout：重试（max_attempts 3、指数退避base 2、max_delay 60、10%抖动、retryable_exceptions 白名单）；熔断（CLOSED/OPEN/HALF_OPEN，failure_threshold 5、success_threshold 2、timeout 60s）；隔舱（每操作独立 Semaphore 并发上限+独立超时）；超时（全部 await 有界，可按 P95×1.2 自适应）。[11.3]
- **REL-14** 副作用操作实现幂等：key=sha256(operation+params sort_keys)；in_progress 等待复用而非重复执行；completed 缓存结果；failed 记录状态；TTL 清理（3600s）。[11.3]
- **REL-15** 长流程每步完成即存检查点；失败结果携带 failed_step 与 can_resume=True；支持从最后检查点恢复与回滚 N 步。[11.3]
- **REL-16** 跨系统多步副作用事务采用 SAGA：每步配补偿动作，失败时按逆序补偿且包含失败步骤本身；补偿必须幂等（幂等键如 booking_id）；补偿失败 logger.critical+转人工；SagaError 携带 failed_step、cause、compensated 列表；明确最终一致性而非 ACID。[11.3]
- **REL-17** 错误分类降级而非中断：timeout（severity 3、可恢复、retry_after 5）/rate_limit（可恢复、退避重试 retry_after 60）/tool_error（severity 4、不可恢复、report_to_user），转为观察注入 agent_context['observations']。[11.3]

### 幻觉防护
- **REL-18** 工具调用前验证管线三层：Schema（必填字段+类型+自动转换）、语义（URL/日期有效性、数值范围截断）、上下文（权限如 viewer 不得 delete_file、与上次 query 重复检测）；验证级别 STRICT拒绝/MODERATE修正/LENIENT接受可配，STRICT 下失败拒绝调用并要求修正。[11.4]
- **REL-19** tool_result.tool_use_id 必须与 tool_use.id 配对校验；工具输出（网页/邮件/第三方API）一律视为不可信输入，做来源校验与注入防护。[11.4]
- **REL-20** 输出交叉检验四项：格式（符合工具预期类型/形状）、逻辑约束（如无 None）、与输入一致性（查询词命中结果）、幻觉指标（自相矛盾、过度自信措辞 definitely/100%/always、不可验证断言 according to my knowledge/i believe、数值异常如负计数）；置信度低于阈值（0.8/0.6/0.4 档）标记 is_suspicious 并建议人工确认/换参重试。[11.4]
- **REL-21** 综合置信度多信号加权评估（logprobs 0.25、token概率 0.20、语义一致性 0.20、事实核查 0.20、连贯性 0.10、知识接地 0.05），与前置验证、输出检验三方均值路由：>0.8 执行、>0.6 谨慎执行、>0.4 人工审核、否则拒绝并重试。[11.4]

## 第 12 章 安全（SEC）

### 威胁建模
- **SEC-01** 项目维护书面威胁模型，至少覆盖 11 类 Harness 威胁：恶意工具调用、路径穿越、权限提升、沙箱逃逸、提示注入劫持、凭据外泄、资源耗尽、模型供应链攻击、间接提示注入、智能体间信任滥用、记忆投毒；每类有风险评分（影响×可能性÷检测难度）与优先级排序。[12.1]
- **SEC-02** 防御为深度防护链：工具调用依次经过 Schema 验证→路径校验→权限检查→命令 AST 分析→执行超时多层检查点，不允许单层防护。[12.1]
- **SEC-03** 失败安全：防护无法判断时默认拒绝；权限引擎对未知工具名、Schema 验证失败的调用一律 DENY。[12.1/12.2]
- **SEC-04** 所有安全决策（允许/拒绝/审批）写审计日志，包括被拒的路径穿越尝试与危险命令拦截。[12.1/12.4]
- **SEC-05** 间接提示注入防护：工具/网页返回内容做脱敏消毒、信任级别标记或输出沙箱处理，不直接并入可信推理链。[12.1]
- **SEC-06** 凭据防护：日志与错误消息对 credential 模式正则脱敏，异常不透出 API 密钥/密码，凭据走环境变量隔离。[12.1]
- **SEC-07** 资源耗尽防护：每次工具调用强制超时（默认 30s 可配置）+ 内存/CPU 限制 + 调用计数，防 fork 炸弹/大文件生成/无限递归。[12.1]
- **SEC-08** 多智能体系统有 Agent 身份认证与请求签名验证、委托链可控；长期记忆有来源追踪与定期审计清洁机制（防信任滥用与记忆投毒）。[12.1]

### 权限与沙箱
- **SEC-09** 权限分级体系：工具调用按风险映射到显式等级（参考六级 manual_only/approve_always/approve_once/ask_first/auto_with_notification/full_trust，外加 DENY 与管理员 OVERRIDE），每个工具声明默认等级。[12.2]
- **SEC-10** 任何"绕过权限"模式（如 bypassPermissions）必须可被组织策略禁用，且仅限隔离环境使用。[12.2]
- **SEC-11** 决策权归属前置门：转账、授权变更、不可逆删除等 USER_ONLY 类操作无论风险分数多低都不自动放行；类别裁决优先于风险分数，不可被风险分类器下调；越出用户授权范围即回落人工决策。[12.2]
- **SEC-12** 权限决策引擎固定流程：Schema 验证→策略查找→等级评估→决策（ALLOW/DENY/ASK_USER），并提供异步版本支持并发与 I/O。[12.2]
- **SEC-13** 审批缓存键为（用户×工具×规范化路径×风险等级）四元组；敏感路径与高风险参数不入长期缓存，ask-first 记忆有时效（如 24h）。[12.2]
- **SEC-14** 子智能体继承父会话权限模式、无独立提权；提权请求必须冒泡到父上下文或用户审批。[12.2]
- **SEC-15** 沙箱三级分级明确（进程/容器/VM，生产至少容器级），架构为"权限决策+沙箱执行"两层协同；容器配置须含 cap_drop=ALL、no-new-privileges、只读根文件系统+tmpfs(noexec,nosuid)、network_mode=none（按需）、mem/cpu/pids 限制、超时与 auto_remove。[12.2]

### 工具调用护栏
- **SEC-16** 维护危险命令黑名单，覆盖 rm/dd/mkfs/shred/sysctl/iptables/insmod/rmmod/kill -9/reboot/shutdown/chown/chmod/sudo/passwd/useradd/userdel/crontab/visudo/at/cryptsetup 及包管理器（apt/yum/zypper）等。[12.3]
- **SEC-17** 危险命令检测基于 AST/shlex 分词而非字符串前缀匹配：主命令剥离路径（/bin/rm→rm）、管道/逻辑链逐段检查、shlex 解析失败直接判危险；包管理器用安全子命令白名单（apt 仅 list/search/show）。[12.3]
- **SEC-18** 只读工具强制只读约束：禁止 rm/touch/mkdir/mv/cp/tee/sed -i、输出重定向 >、dd of= 等写操作。[12.3]
- **SEC-19** 数值参数范围约束强制校验：timeout∈(0,300]s、memory∈(0,2048]MB、file_size∈(0,1024]MB，越界拒绝。[12.3]
- **SEC-20** 护栏为三明治结构：输入层清洗（剥离注入模式+信任标签）→推理层有界（工具白名单+步数限制+Token 预算）→输出层验证（敏感泄露检查+格式合规）；可用 access/pre-execution/post-execution webhook 钩子插策略。[12.3]
- **SEC-21** 护栏规则外部化为配置文件（如 guardrails.yaml），每类规则支持 deny/ask/allow 动作，检查结果返回结构化 recommendation（ALLOW/ASK/DENY）。[12.3]

### 路径校验
- **SEC-22** 文件访问执行 5 层递进校验且顺序固定：①长度检查（≤4096）→②URL 解码→③Unicode NFC 规范化→④平台规范化（\ 统一为 /、合并 //、normpath）→⑤realpath 解析+边界检查。[12.4]
- **SEC-23** URL 解码迭代至收敛（防 ..%252f 双重编码），设迭代上限（如 20 次），未收敛判攻击拒绝；不得以"% 数量不变"提前退出。[12.4]
- **SEC-24** 边界检查用 os.path.commonpath 比较 resolved 路径与 base 目录，禁止 startswith 前缀匹配（防 /tmp/agent-evil、..notes 误判）。[12.4]
- **SEC-25** 先 os.path.realpath 解析符号链接再查边界（防 link→/etc/passwd 逃逸）；realpath 失败回退 abspath 仍做边界检查；注意大小写不敏感文件系统的 /ETC/PASSWD 绕过。[12.4]
- **SEC-26** 路径白名单优先：whitelist_dirs 显式列出允许目录；/etc、/root、/var/log、/.ssh 等为绝对黑名单。[12.4]
- **SEC-27** 路径校验缓存 TTL≤1 秒；删除、改权限等关键操作禁用缓存、每次 realpath（TOCTOU 防护）。[12.4]
- **SEC-28** 工具 Schema 标记路径类参数（path_params），执行前对全部路径参数强制校验，通过后用规范化路径替换原参数值。[12.4]

## 第 13 章 评测（EVAL）

### 评估方法论
- **EVAL-01** 评估覆盖三层且各有可计算指标：步骤级（工具准确率/参数准确率/执行成功率）、轨迹级（效率=最优步数/实际步数、错误恢复率、重复调用率）、任务级（成功率、平均耗时、Token/成本效率、满意度）。[13.1]
- **EVAL-02** 综合评分有显式权重写入代码（参考：任务成功率40%+轨迹效率30%+步骤准确率20%+成本10%）；不同量纲指标不横向比较，只看同一指标版本间变化。[13.1]
- **EVAL-03** 离线评估（测试集）与在线评估同时实施；仅有可观测性不构成评估（行业落差：可观测性 89% vs 离线评估 52.4%、在线 37.3%）。[13.1/13.4]
- **EVAL-04** 迭代 Skill/提示词需受控闭环：训练 rollout 与 held-out 验证集分离、小范围受控文本编辑、候选仅在验证集严格优于当前版本才接受、保留拒绝编辑记录；版本比较时模型/工具/权限/任务集固定一致。[13.1]

### 端到端测试
- **EVAL-05** E2E 双轨并存：Mock LLM/Mock 工具（<100ms、确定性、验证调用顺序与编排）+ 真实 LLM/真实工具（验证实际行为与泛化），并按 Mock/真实、合成/真实数据、离线/在线、自动/人工维度明确分类。[13.2]
- **EVAL-06** 真实测试断言具体化：产物文件存在且非空、工具调用次数≥下限、交互迭代有 max_iterations 上限。[13.2]
- **EVAL-07** 测试夹具标准化：独立临时数据目录、多类型样本（txt/json/csv）、集中 agent 配置（model/max_iterations/timeout/启用工具清单）。[13.2]
- **EVAL-08** 回归基线：指标持久化（JSON 基线文件）+ 方向性阈值——accuracy/success/rate 类降超 5% 报警，time/cost/latency 类升超 5% 报警。[13.2]
- **EVAL-09** 套件场景覆盖：简单任务、多步工作流、错误恢复、边界情况（空文件/大文件/特殊字符）、Token 效率、性能基线对比。[13.2]

### 基准测试
- **EVAL-10** 按场景选基准并定期回归（比追排行榜更重要）：GAIA（通用推理+工具，466 任务 3 级）、WebArena（真实网页交互 812 任务 4 领域）、SWE-Bench Verified（500 人工验证代码任务）、AgentBench（8 环境）；复现时固定等级与样本量、逐任务记录结果与错误。[13.3]
- **EVAL-11** 代码类任务评估双维度：FAIL_TO_PASS 通过（问题确实修复）且 PASS_TO_PASS 保持通过（未引入回归）。[13.3]
- **EVAL-12** 基准报告不只给分数：披露评测主张类型（能力激发/安全防护/模型比较）、harness/工具/上下文/护栏与 turn/token/retry/time/cost 预算、是否做过 reward hacking/拒答掩盖/样本污染/坏题/sandbagging 检查、人工复核如何影响判定。[13.3]

### 持续评估
- **EVAL-13** 生产监控指标集完整：可用性（uptime/错误率/崩溃率）、质量（成功率/平均耗时/超时率）、效率（平均 Token/工具调用准确率/错误恢复率）、成本（总成本/每成功任务成本），按滑动时间窗口（如 60min）计算。[13.4]
- **EVAL-14** 异常检测：关键指标维护历史窗口，统计方法（如最近 10 个观测 Z-score>2.5）判定异常并输出针对性处置建议。[13.4]
- **EVAL-15** A/B 测试：用户分组用稳定哈希（如 sha256(user_id)，禁用带进程随机盐的内置 hash()），显著性判定要求最小样本量（≥30/组）且差异阈值（如 5%）。[13.4]
- **EVAL-16** 可观测性集成：每次执行生成 trace、按工具调用记录 span（输入/输出/成功标记），接入 Langfuse；导出 Prometheus 指标（agent_tasks_total、agent_task_duration_seconds 直方图、agent_success_rate、agent_tokens_total）。[13.4]

## 第 14 章 未来方向

原书为前瞻性章节（无强制条目），涉及记忆主动演化、跨会话个性化、评测自动化等方向，可作为路线图参考。

---

*本文档由 2026-09 架构审计时从原站全文提炼复刻；若原站更新，请以原站为准并同步修订本文。*
