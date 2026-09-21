# CPS 后端迁移与接口指南

日期：2026-09-11。适用项目：`basic-project`。

## 1. 迁移范围

以 `CPS.md` 和 `CPS智能巡检系统PRD.md` 为业务依据，迁入的是后端业务能力，
不是把旧独立 FastAPI 应用再嵌套挂载一次。

- 保留并迁入八个角色的完整边界提示词及严格结构化输出契约。
- 替换旧内存编排、独立模型工厂与独立审批存储，接入 `app.harness.kernel`。
- 新增任务、图片证据、人工调度、结果确认、报告归档、计划任务、统计、记忆审核与审计接口。
- 已删除旧独立项目 `cps-inspection-agent/` 和残留包 `cps_inspection/`，包括旧前端、数据库、虚拟环境与构建产物，不保留备份。
- 仅使用 `app/projects/cps` 后端，不提供旧数据库导入工具、接口或兼容分支。需求、PRD 和流程设计资料保留，不作为运行入口。

### DDD 目录

| 层 | 路径 | 职责 |
|---|---|---|
| 领域 | `app/projects/cps/domain` | 巡检聚合、命令、Agent 契约、证据/仓储端口、确定性统计与安全报告渲染 |
| 应用 | `app/projects/cps/application` | 调度、审批、证据版本、事务 Outbox、运行结果投影、归档、任务和记忆用例 |
| 基础设施 | `app/projects/cps/infrastructure` | 内核仓储适配、DeepAgents 适配、多模态消息、图片校验、向量编码 HTTP 适配 |
| 装配 | `app/projects/cps/bootstrap.py` | 注册 Agent/工作流、连接内核引擎、挂载旁路消费者、恢复待投递作业 |
| HTTP | `app/api/v1/endpoints/cps.py` | JWT、角色及任务归属检查、请求校验、JSON/图片/HTML/SSE 输出 |
| 提示词 | `app/projects/cps/prompts` | 通用安全边界 + 八个角色职责 + 运行时 Schema |

领域层不导入 FastAPI、SQLite、LangChain 或基础设施实现；应用层通过端口接收
图片编码/校验及巡检仓储，并调用共享内核的运行与持久化服务。业务没有写进框架领域层。

## 2. 主 Agent 如何控制流程

这里没有把业务固化为“识别 → 整改 → 报告”的 LangGraph 路由图。

```text
创建/补充巡检任务
    ↓ start
内核运行 cps_main：读取业务快照、已确认结果、人工历史及已审核记忆
    ↓ 输出下一步建议
内核 after 审批：等待人工调度确认
    ├─ approve / modify / retry：持久记录最终 Agent、理由和指令
    │       ↓ 恢复主 Agent 作业，应用层投递指定专用 Agent
    │   专用 Agent 结构化草稿
    │       ↓ 独立 after 审批
    │   人工确认/编辑结果 → 更新业务事实 → 再次运行主 Agent
    ├─ skip：不执行建议，重新咨询主 Agent
    ├─ return：停止当前建议，返回采集
    ├─ manual：停止自动推进，转人工处理
    └─ approve finish：仅在必需结果、报告归档、计划发布齐备时完成
```

每个主 Agent 决策和每个专用 Agent 执行都是一个持久化内核运行，使用同一个
`task_id=inspection_id`，但拥有不同 `run_id`、Checkpoint 和隔离工作区。
业务 `cps_job` 把它们关联为完整链路；不是让不同巡检共用一个目录。

使用多个 `Step` 作业而不是预设 `Supervisor` 成员执行顺序，是为了保留 CPS 独立的
业务确认、退回采集、人工处理与跨请求恢复边界。下一位专用 Agent 由主 Agent 建议、
人工批准或改派决定；应用层只执行已确认命令，不自行猜测下一个 Agent。

本业务每次只确认一个下一步，不把一次批准扩大为批准整条链。框架原有 `Parallel`、
`Sequence` 和 `Supervisor` 能力继续可用，但 CPS 当前调度契约不会隐式并行调用多个专家。

### 两类确认不能合并

1. **调度确认**：批准调用某个 Agent，不表示接受它尚未生成的结论。
2. **结果确认**：确认或修改问题、整改、历史、覆盖、报告和计划的结构化草稿。
3. **发布动作**：报告归档、工作计划正式发布仍需管理员显式调用独立接口。

拒绝结果后进入 `needs_human`，不把草稿当事实，也不会自动调用下一位 Agent。
新增现场证据/改变任务信息会使旧结论失效；新增整改后证据保留已确认问题，
但使整改结论、报告和计划失效。整改后图片绑定问题确认版本，不能拿旧版本照片
为修改后的问题直接验收。上游结果变化也会使下游报告/计划确认失效。

## 3. 启动与模型配置

```bash
uv sync --locked --extra dev
uv run --locked agent-framework workflows
uv run --locked uvicorn app.main:app --host 0.0.0.0 --port 8000
```

原项目的数据源、LLM、HTTP 客户端与调度器生命周期不变；启动应用前也要按项目主指南
配置所需数据库。CPS 本身使用内核的 SQLite 持久化，不要求启动旧 CPS 服务。

默认注册模块为：

```text
app.projects.agent_examples,app.projects.cps.bootstrap
```

若部署已设置 `AGENT_WORKFLOW_MODULES`，需要显式加入 CPS；只运行 CPS 可设置：

```bash
export AGENT_WORKFLOW_MODULES=app.projects.cps.bootstrap
export AGENT_DATA_DIR=/srv/basic-project/agent-data
export CPS_CONFIG=config/cps.yaml
```

`AGENT_RUNTIME_ENABLED=false` 或未注册 CPS 时，其接口返回 503，不悄悄退回旧实现。
`AGENT_EMBEDDED_WORKER=false` 时，另行运行现有 `agent-framework worker` 进程。
API 和 Worker 必须使用相同的模型配置、模块集合及持久数据目录。

### 每个 Agent 的独立模型映射

继续使用根目录 `config/agents.yaml`，当前默认提供商为通义千问 Qwen：

| Agent | 默认配置档位 |
|---|---|
| `cps_main` | `reasoning_large` |
| `cps_issue_identification` | `cps_vision` |
| `cps_rectification_judgement` | `cps_vision` |
| `cps_history_analysis` | `reasoning_large` |
| `cps_coverage_analysis` | `reasoning_large` |
| `cps_report` | `reasoning_large` |
| `cps_work_plan` | `reasoning_large` |
| `cps_observation` | `default` |

工程师在 `providers` 配置提供商、`base_url` 和凭据环境变量，在 `models` 配置真实模型名、
参数及能力声明，再通过 `agents` 对每个角色独立映射。默认模型名是可替换的部署示例，
不代表本机已经部署这些模型；不会为了迁移而自动启动或下载模型。

视觉角色及其 fallback 必须声明并实际支持 `vision`、`tools`、`structured_output`；
启动注册阶段会检查配置能力，不允许视觉角色退回纯文本模型。能力声明不能代替实际模型联调。
原型的独立模型 YAML 不再作为配置来源，统一以根项目配置为准。

### CPS 业务配置

`config/cps.yaml` 控制开关、最大主 Agent 轮次、图片上限、统计窗口和向量编码服务。
默认每任务最多 20 张图、每张最多 10MB、最多 2500 万像素、40 次主 Agent 决策。
默认每次最多注入 5 条同作用域的有效记忆，优先最近审核版本，避免记忆无限堆入上下文。
达到轮次预算会转人工，不无限循环。网关也应设置请求体大小和速率限制。

## 4. 身份与权限

所有 CPS 接口强制验证 access JWT，与旧接口的可选认证开关无关。
身份从签名 Token 的 `sub`、`tenant_id`、`roles`、`exp` 读取，禁止请求体指定确认人或租户。

| 角色 | 权限 |
|---|---|
| `cps_employee` | 创建、读取和补充本人的巡检，查看本人负责的已发布任务并更新状态 |
| `cps_dispatcher` | 同租户巡检读取、下一步 Agent 调度确认、退回/停止 |
| `cps_supervisor` | 同租户调度、问题/整改/历史/覆盖结果确认、统计与任务管理 |
| `cps_admin` | 同租户全部 CPS 操作，报告/计划确认、归档/发布、记忆审核 |

新接口不接受旧 `/auth/token` 演示接口产生的缺失租户/角色声明的 Token。
生产环境应对接组织的认证签发流程并更换演示签名密钥；不要开放“传入角色即可签发”的新接口。
仅本地开发时，可以在可信终端使用项目已有的 `create_access_token` 生成测试身份：

```bash
uv run --locked python -c 'from app.core.security import create_access_token; print(create_access_token({"sub":"developer","tenant_id":"factory_demo","roles":["cps_admin"]}))'
```

不同租户的任务、图片、历史、候选记忆及有效记忆均隔离；员工在同租户内仍受任务归属限制。
CPS 用户通常不需要授予通用 `run_operator`、`approver` 等框架管理角色。
即使直接提交通用 `agent-runs` 执行 CPS Agent，缺少业务 Outbox 授权也会被拦截；
原生审批本身不能替代 CPS 的调度记录或业务结果确认。

## 5. HTTP 接口

默认基础路径：`/api/v1/cps`；完整请求 Schema 可通过应用的 `/docs` 或 `/openapi.json` 查看。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/agents` | 可选角色目录、结果编辑 Schema 与审核角色；不提供前端模型配置 |
| POST / GET | `/inspections` | 创建/分页列出巡检 |
| GET / PATCH | `/inspections/{id}` | 详情/修改目标、拉线信息和备注 |
| POST | `/inspections/{id}/evidence` | 上传实际图片内容，关联整改前后证据 |
| GET | `/inspections/{id}/evidence/{evidence_id}` | 经授权读取原始图片 |
| POST | `/inspections/{id}/start` | 投递主 Agent，异步返回 |
| POST | `/inspections/{id}/dispatch` | approve/modify/skip/retry/return/manual |
| POST | `/inspections/{id}/results/review` | 确认、完整编辑或拒绝专用 Agent 输出 |
| POST | `/inspections/{id}/results/manual` | 无活动执行时录入人工确认结果，仍遵循对应 Schema |
| POST | `/inspections/{id}/stop` | 停止运行并取消任务或退回采集，不接受迟到结果 |
| POST | `/inspections/{id}/sync` | 重放业务 Outbox、应用已完成运行；不直接调用模型 |
| GET | `/inspections/{id}/report.html` | 预览草稿/确认报告，文本转义并设置 CSP |
| POST | `/inspections/{id}/archive` | 确认报告转结构化历史快照；保留 HTML 和证据引用 |
| POST | `/inspections/{id}/work-plan/publish` | 确认计划生成正式、可跟踪任务 |
| GET | `/inspections/{id}/events` | 聚合整条业务链及所有子运行事件；支持 SSE |
| GET | `/history`、`/statistics`、`/metrics` | 已归档历史、确定性统计及记忆效果评估 |
| GET / PATCH | `/tasks`、`/tasks/{task_id}` | 查看任务、更新状态和执行说明 |
| GET | `/memories`、`/memories/{id}/versions` | 候选/有效记忆及版本历史 |
| POST | `/memories/{id}/review` | 接受、拒绝、停用、启用、回滚；审核时可修正知识与作用域 |

详情同时返回业务 `status`、运行 `active_run.status`、当前 `phase`、待确认 `approvals`、
已确认 `results` 和 `confirmations`，不能将模型草稿混同为已确认结果。
投递或投影异常通过 `outbox_error` 和 `needs_operator_recovery` 暴露，不阻塞其他巡检的投递。
修复配置后可调用 `sync` 重试；过期或冲突审批可用 `stop` 退回采集，再重新启动。

### 创建任务

```json
{
  "goal": "检查 A 区拉线改造后的安全问题，复核整改并形成下一步工作安排",
  "line_info": {
    "line_id": "line_A1",
    "area": "area_A",
    "supervisor_id": "supervisor_01",
    "modifications": "调整输送线布局",
    "expected_scenarios": ["机械防护", "通道", "用电"]
  },
  "required_outputs": ["issues", "rectification", "history_analysis", "coverage", "report", "work_plan"],
  "idempotency_key": "pda-inspection-001"
}
```

只进行单项分析时，可在创建任务时缩小 `required_outputs`，不是强制每次运行所有 Agent。
Agent 选择仍由主 Agent 建议并人工确认，而不是照这个数组逐项执行。

### 图片上传

提交 `expected_version`、`idempotency_key`、`kind`、`content_base64`，可附 `issue_id` 与 `note`。
`content_base64` 是 PNG/JPEG/WEBP 原始字节的严格 Base64，不是文件名、URL 或 data URL。
整改后证据 `kind=after` 必须关联已确认问题的 `issue_id`。
后端解码并校验真实图片，然后仅向授权的视觉 Agent 传递带来源 ID 的多模态消息。

### 调度改派

```json
{
  "expected_version": 3,
  "idempotency_key": "dispatch-review-001",
  "action": "modify",
  "selected_agent": "coverage_analysis",
  "reason": "先核对改造区域覆盖情况，再生成报告",
  "instruction": "重点查看新调整区域是否缺少图片证据"
}
```

版本号必须以最新详情为准，不能固定使用示例值。`approve`/`retry` 不允许偷偷换 Agent；
改派必须用 `modify`。不能选 `main`、`observation`、框架示例或任意外部 Agent。
视觉执行前还会检查必要图片和问题确认版本，人工也不能绕过这些前置条件。

### 结果审核与发布

`results/review` 的 `edited` 是对应 Agent 的**完整输出对象**，不是 JSON Patch。
服务端再次校验 Schema、来源、统计口径、问题引用和计划依赖。
报告与计划草稿只能由 `cps_admin` 确认，随后才允许 `archive` / `work-plan/publish`。
发布任务必须有负责人、带时区的截止时间和验收标准；依赖不能成环，前置任务完成前
不能启动/完成依赖任务。替换已发布计划前须先关闭或取消旧计划中未完成的任务。

写命令使用 `expected_version` 和 `idempotency_key`；相同请求重试不重复调用模型、
重复归档或重复生成任务。更换请求内容/操作人复用同一键返回 409。
修改任务信息的 PATCH 使用乐观版本；成功后重复提交旧版本会返回 409。

### 事件流

请求 `/inspections/{id}/events?stream=true`，可使用 `Last-Event-ID` 续传。
事件包含业务动作、主 Agent/专家执行、审核及恢复事件；Token 到期后流会停止。
不开启 stream 时按全局游标分页返回 JSON，适合服务端审计与断线补读。

## 6. 图片向量与历史统计

向量编码器是独立领域端口；提供了可配置 HTTP 实现。未配置时不会伪称执行了向量检索，
输入快照明确标注能力缺口。图片本身仍能交给多模态模型识别。

需要检索时，在 `config/cps.yaml` 配置真实编码服务：

```yaml
embeddings:
  url: http://127.0.0.1:8090/image-embeddings
  model: factory-image-embedding-v1
  api_key_env: CPS_EMBEDDING_API_KEY
  timeout: 30
  max_dimensions: 8192
```

这是本项目定义的适配器协议，不是声称任意提供商都兼容此接口：

- 请求：`{"model": "配置模型名", "image": "data:image/png;base64,..."}`。
- 响应：`{"embedding": [0.12, -0.34, 0.56]}`。
- 拒绝空、零或非有限向量；配置了服务但调用失败时上传失败，可重试，不保存伪造向量。
- 向量与图片一并持久化；候选只来自同租户**已归档**历史，排除当前任务，按余弦相似度排序。
- 当前是持久向量的精确扫描检索，不冒充外部 ANN 数据库。大规模库可替换领域端口/仓储适配。

正式历史只来自人工确认后显式归档的数据。同一任务的多个归档版本，统计采用最新版本，
避免重复计数。代码计算问题分类、区域/主管计数、复发记录和关联措施复查信号。
指标数值、单位、分母由代码校验，模型只解释。没有观察证据时不能宣称“整改后永不复发”
或把相关性解释为某项措施的因果有效性。

## 7. 旁路观察和长期记忆

- `CPSLifecycle` 跟随 Worker 消费链路事件，投影业务结果并恢复待投递作业。
- 人工调度的原始建议、最终选择、理由、补充指令及实际输出形成待审核记忆；失败/跳过也记录。
- 报告归档和任务完成会投递独立 `cps_observation` 作业；它没有业务工具或委派权限。
- 观察输入包含决策和结果历史、最近 500 条完整链路事件及被省略事件数量；完整审计事件仍保存在库中。
- 观察结果不会自动生效；管理员接受后，只有同租户、匹配产线或明确 `tenant` 范围的有效记忆
  才进入后续主 Agent 快照。停用、拒绝、过期记忆不参与决策。
- 每次审核、停用和回滚保存历史版本；回滚创建新版本，不覆盖旧审计记录。
- `/metrics` 给出改派、结果修正、记忆显式引用和完成时长，并对有/无记忆输入组提供观察性比较。
  这些指标不构成因果实验，不自动修改 Prompt、工具权限或线上策略。

## 8. 持久化与恢复

复用内核同一 SQLite WAL 仓储的命名空间：`cps_case`、`cps_job`、`cps_command`、
`cps_evidence`、`cps_archive`、`cps_plan`、`cps_task`，并复用原生 run、approval、memory 和事件。

业务状态变更与 Outbox 意图在同一事务中保存，随后用稳定幂等键提交内核运行。
业务审核意图先持久化，再投递原生审批，重启或请求重试可恢复中间窗口。
执行结果只投影一次；恢复已完成 Agent 不重新调用模型。调用未知/失败时转人工，
不会把失败当正常结论继续推进。Worker 通过租约和检查点保证执行所有权。

API/Worker 重启必须保留 `AGENT_DATA_DIR` 全目录，包括状态库、Checkpoint、工作区。
沿用当前框架的单机/共享本地磁盘部署边界，不声称 SQLite 已提供跨地域高可用。

## 9. 验证

```bash
uv run --locked pytest tests/cps -q
uv run --locked pytest tests -q
uv run --locked ruff check app/projects/cps app/api/v1/endpoints/cps.py tests/cps
uv build
```

自动测试覆盖动态调度、人工改派、独立结果审核、退回/跳过、禁止越权、证据隔离、
安全 HTML、幂等归档/任务发布、记忆版本、Worker、重启与审核投递崩溃窗口。
包含真实 DeepAgents/LangGraph 图的离线结构化输出测试，不只是绕过 SDK 的服务层模拟。

实际模型服务、企业认证系统、PDA 上传链路和图片向量服务仍须按部署配置联调；
自动测试不会替代生产图片识别准确率、整改判断效果和统计口径的业务验收。
