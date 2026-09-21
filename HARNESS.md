# Agent 框架入口与旧代码迁移

新业务统一使用 `app.harness.kernel`，不再新增继承式旧拓扑。

## 当前实现入口

- [开发与部署指南](docs/Agent框架开发与部署指南.md)：DDD 分层、Python 组合、审批、通讯、隔离与部署。
- [能力验收清单](docs/Agent框架能力验收清单.md)：已实现能力、验证方式与外部联调边界。
- `app/projects/agent_examples.py`：单 Agent、顺序、并行、审批与可选 Supervisor 示例。
- `app/projects/agent_advanced.py`：高级资源与委派示例。
- `config/agents.yaml`：提供商、模型地址、每 Agent 模型与参数配置。

```bash
uv sync --locked --extra dev
uv run --locked agent-framework workflows
uv run --locked pytest tests -q
```

## 已移除的旧实现

- AgentScope 依赖、后端适配器和后端工厂分支；旧配置不再接受该后端，也不会静默降级。
- 八个继承式复合拓扑：并行、顺序、Pipeline、对话、路由、计划执行、反思、子 Agent。
- 仅被旧拓扑使用的聚合器，以及 `app/projects/harness_tests/` 中的旧演示脚本。
- 无调用入口的 `app/harness/preprocess.py`、`app/harness/sections.py` 及图片放大/切分配置字段。

旧导入 `BaseParallelAgent`、`BaseSequentialAgent`、`BasePipelineAgent`、
`PipelineStep`、`BaseConversationalAgent`、`BaseRouterAgent`、`BasePlanExecuteAgent`、
`BaseReflectionAgent`、`BaseSubagentAgent` 和 `aggregator` 已不再提供。
迁移时使用内核的 `Step`、`Sequence`、`Parallel`、`Condition`、`Loop` 与可选
`Supervisor`，对话和反思需显式定义状态与终止条件；子 Agent 使用内核委派入口。
旧类属性不是新内核配置，不能只改 import 而不重写注册与组合代码。

## 保留的业务兼容层

`app/projects/doc_review` 仍使用旧单 Agent 做事实提取，并由确定性规则引擎判断合规。
为避免本次清理改变现有审核行为，保留以下实现：

- `BaseSingleAgent`、`BaseAgent`、运行上下文与结果，以及旧通讯设施。
- `deepagents` 和 `llm` 兼容后端；模型仍通过原 `app.services.llm` 配置。
- 工具注册及 OCR、视觉、事实提取、规则与黑板工具。
- 单 Agent 中间件及其会话记忆依赖，供已有类属性配置使用。

`app.harness` 对兼容接口采用惰性导出，导入 `app.harness.kernel` 不会加载
旧 Agent、后端、中间件或工具注册包。旧通讯设施与新内核持久通讯不是同一个实现。
旧兼容层不具备新内核的持久审批、租约和任务隔离保证。

Pillow 仍被实际 OCR 图片解析使用，因此保留；不是所有旧依赖都可直接删除。
独立 CPS 旧项目及残留包已在后端迁入后删除，不保留旧前端、数据库或运行环境。
PRD 和流程设计资料不属于旧运行代码，继续保留。

后续迁移文档审核时，应显式注册 `AgentDefinition` 与工作流，映射输入输出和业务工具，
验证原有审核结果后，再移除剩余兼容层，不能以新内核的保障替代旧业务的实际验收。
