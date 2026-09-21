# Agent 框架能力验收清单

日期：2026-09-11。实现位置：`app/harness/kernel`。对应设计文档 DA01–DA24 与 A01–A33。

## 状态定义

- **本地已验证**：实现代码存在，自动化测试实际运行对应路径；其中模型响应、远程协议或系统命令可能使用明确的测试替身。
- **SDK 接入**：实际参数/生命周期已接入，不是空端口；不把传参测试解释为已证明所有供应商行为。
- **外部待联调**：需要真实服务、部署身份、镜像或业务数据。框架代码交付不等于生产环境验收。

默认参考部署为单主机持久目录与 SQLite WAL。未交付多主机数据库集群、对象存储集群或任何特定供应商服务本身。

## DeepAgents 能力

| 编号 | 实现位置 | 验证与限制 |
| --- | --- | --- |
| DA01 单 Agent | `infrastructure/deepagents.py` | 真实 create_deep_agent + 离线模型测试，无隐藏主 Agent |
| DA02 Todo 规划 | `AgentDefinition.planning`、模型 Harness Profile | 测试确认关闭时实际工具列表不包含 write_todos |
| DA03 声明式子 Agent | `SubAgent` 绑定 | 定义转为受控 CompiledSubAgent，独立模型/Schema/审批/Scope |
| DA04 预编译图 | `CompiledAgent` | 真实 StateGraph、自定义中断、SQLite Checkpoint 恢复已测试 |
| DA05 子 Agent 并行 | Runtime admission、原生 task 包装 | 相同输入的两次原生/解释器调用保持不同 Invocation，审批恢复已测试 |
| DA06 远程后台 Agent | `RemoteAgent` | 实际 Agent Protocol 客户端；生命周期契约测试；真实远程服务待联调 |
| DA07 fork | `SubAgent(mode="fork")` | 已升级并锁定 deepagents 0.7.13；测试历史继承与独立 Scope；采用 compiled fork 语义 |
| DA08 动态子 Agent | QuickJS + `adapt_interpreter` | 实际 JS task 与 Promise.all 委派、人工批准、恢复已测试；保留 SDK beta 标识 |
| DA09 工具 HITL | Checkpoint + review/resume 映射 | approve/edit/reject、重复中断和参数修改已测试 |
| DA10 调用/输出审核 | `Approval`、Runtime.approve | 可选审核、静态步骤/动态委派人工改派、长期记忆候选已测试 |
| DA11 文件 Backend | Filesystem、Resources、ScopedStore | 只读挂载、版本化产物、跨任务隔离已测试；任意 Python 插件仍属受信任代码 |
| DA12 Shell/沙箱 | `DockerSandbox` | 隔离命令与安全标志有契约测试；没有声称已启动真实 Docker 镜像 |
| DA13 Skills | Resources + SDK SkillsMiddleware | 实际 skills 参数与只读资源路由；源内容进入定义快照 |
| DA14 Memory/Store | SDK memory、ScopedStore、Memory 服务 | 执行 Store 隔离和业务候选审核已测试；记忆文件不等同业务审核 |
| DA15 摘要/卸载 | SDK 原生中间件 + Backend | 真实 SDK 自动装配与自定义 middleware 接入；长上下文实际模型效果待质量评测 |
| DA16 流式观测 | `ExecutionAudit`、graph.astream、SSE | 图/模型/工具/子调用状态、持久游标重放已测试；默认不广播原始 token 内容 |
| DA17 Checkpoint/恢复 | AsyncSqliteSaver + durable runtime | 真实中断恢复、关闭进程后重启、未知副作用对账已测试 |
| DA18 类型与自定义上下文 | AgentDefinition schema/factory 字段 | 真实 SDK 结构化结果与应用二次校验；自定义图中断测试 |
| DA19 Middleware/Profile | DeepAgentsEngine、ProfiledModel | 独立 profile 注册与有效模型工具列表审计；不允许 profile 开启隐藏委派旁路 |
| DA20 限制/降级/PII | 调用限制、Retry、模型 fallback、官方 middleware | 有限预算、幂等重试和模型能力校验；PII/供应商质量应按具体业务继续评测 |
| DA21 缓存 | `ScopedCache`、图 cache_factory | 不同 run 缓存不互通；不缓存审批；具体图需显式 CachePolicy |
| DA22 MCP | `MCPTools` | 白名单、真实 SDK 会话装配/关闭和工具调用契约测试；真实服务器待联调 |
| DA23 静态组合 | Sequence/Parallel/Condition/Loop | 顺序绑定、并行重叠/限额、部分失败、条件/循环均已测试 |
| DA24 通信/观察/记忆 | Mailbox、Artifacts、Observer、Memory | 幂等邮箱、版本冲突、游标恢复、观察失败隔离及记忆审核均已测试 |

## 业务验收追踪

| 编号 | 测试入口或实现检查 |
| --- | --- |
| A01 | `test_sequence_bindings_no_coordinator`、`test_real_sdk_single_without_hidden_subagent` |
| A02 | `test_sequence_bindings_no_coordinator`、`test_invalid_bindings_rejected_before_execution` |
| A03 | `test_parallel_overlaps_and_limits`、`test_shared_database_enforces_cross_runtime_capacity` |
| A04 | `test_parallel_failure_policy` |
| A05 | Supervisor Decision 白名单执行；免审批由 Approval.none 选择，不额外创建协调者 |
| A06 | `test_supervisor_edited_dispatch_proposes_memory` |
| A07 | `test_restart_reuses_completed_steps`、`test_process_crash_requires_reconciliation_and_does_not_repeat_effect` |
| A08 | `test_workspace_private_and_path_restrictions`、`test_scoped_store_persistence` |
| A09 | `test_idempotency_and_tenant_scope`、不同 run 工作区/缓存测试 |
| A10 | `test_artifacts_versions_sharing_and_cross_tenant`、`test_mutation_cannot_change_other_branch_or_input_snapshot` |
| A11 | Mailbox.receive/acknowledge 与 ExecutionAudit 的模型前/后安全点 |
| A12 | `test_mailbox_idempotency_and_acknowledgement`、`test_nested_request_does_not_deadlock_with_one_slot`；Runtime cycle/depth 检查 |
| A13 | `test_api_auth_tenant_and_validation`、`test_worker_approval_resume_and_event_replay` |
| A14 | `test_observer_failure_isolated_and_replay`、`test_observer_candidates_are_reviewed_not_automatically_active` |
| A15 | `test_inherited_policy_and_explicit_none` |
| A16 | `test_model_request_uses_configured_endpoint_and_model`，实际 HTTP 请求经 MockTransport 断言 |
| A17 | 无证据示例提示词明确禁止捏造；缺少配置/密钥真实报错；生产模型质量不由假数据补齐 |
| A18 | Supervisor 是组合节点，可被 Sequence/Parallel 嵌套；没有全局主 Agent |
| A19 | `test_no_supervisor_step_can_be_reassigned` |
| A20 | `test_leaf_disables_native_and_interpreter_hidden_delegation` |
| A21 | `test_real_sdk_tool_hitl_and_resume`、`test_repeated_tool_interrupts_and_rejection` |
| A22 | 原生/CompiledGraph/Remote 三套适配测试；远程策略的真实部署验证仍需服务方完成 |
| A23 | 子定义经过同一模型选择、输入输出校验和 Scope 分配；不会自动继承父工具 |
| A24 | `test_readonly_resources_and_store_backend`、Docker 参数测试；不把宿主 Python 代码说成安全沙箱 |
| A25 | 只读 Skills/Memory 路由和源快照；SDK 摘要保留在 run 私有 Backend；长文本效果需实测 |
| A26 | `test_real_sdk_native_subagent_goes_through_runtime`、图事件与 SSE 测试 |
| A27 | `test_remote_lifecycle_and_idempotent_collection`；断连/远程取消属于真实服务联调重点 |
| A28 | DeploymentPolicy 在注册/执行/中断路径校验；不支持的 PTC+HITL 组合明确失败 |
| A29 | 模型 fallback 只包模型调用；业务 Retry 必须有幂等契约；不是整段业务无条件重试 |
| A30 | `test_fork_has_isolated_identity_and_inherited_messages` |
| A31 | `test_native_and_interpreter_dispatch_approval_cannot_bypass`、`test_identical_parallel_native_calls_remain_distinct` |
| A32 | `test_mcp_allowlist_and_session_lifecycle`、`test_scoped_cache_isolation` |
| A33 | run 的 definition/model/policy 快照、engine.capabilities、model.tools 事件 |

这个表区分测试证明和实现检查，不能据此声称每一项生产场景都经过了现场验收。

## 生产接入前必做

1. 配置真实模型地址、已部署模型名、环境变量密钥；逐角色验证工具调用和结构化输出能力。
2. 如启用远程 Agent，验证服务端租户身份、工具权限、取消、中断和网络失联对账。
3. 如启用 MCP，确认工具 Schema 和最小权限凭据；如启用沙箱，提供经过审核的固定 digest 镜像并实际执行隔离测试。
4. 固定所有 Worker 的代码、模型配置、部署策略和并发限额；不在不兼容配置之间静默恢复任务。
5. 按实际规模做容量、超时、成本和备份恢复演练。无外部证据时不报告通过率或 SLA。
