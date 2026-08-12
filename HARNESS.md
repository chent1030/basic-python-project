# Harness 框架使用文档

## 目录

1. [概览](#1-概览)
2. [快速开始](#2-快速开始)
3. [9 种拓扑基类](#3-9-种拓扑基类)
4. [后端切换](#4-后端切换-deepagents--agentscope--llm)
5. [中间件（含记忆）](#5-中间件含记忆)
6. [工具系统](#6-工具系统)
7. [Skill 系统](#7-skill-系统)
8. [聚合器](#8-聚合器)
9. [Agent 间通讯](#9-agent-间通讯3-种模式)
10. [HITL 人机协作](#10-hitl-人机协作)
11. [流式输出](#11-流式输出)
12. [文件预处理](#12-文件预处理)
13. [章节提取](#13-章节提取)
14. [项目结构规范](#14-项目结构规范)
15. [完整示例：文档审核](#15-完整示例文档审核)

---

## 1. 概览

Harness 是基于**继承式基类**的通用 agent 框架。

```
app/harness/          框架核心（基类 + 后端 + 中间件 + 通讯 + 工具）
app/projects/         业务项目（继承基类）
```

**核心设计**：
- **拓扑 = 基类**：9 种拓扑，每种一个基类，继承即获得编排能力
- **配置 = 类属性**：纯 Python 声明，无 config.yml
- **后端可切换**：`backend` 类属性，一行换 deepagents/agentscope/llm
- **Agent 间通讯**：共享黑板 + 消息传递 + 事件总线
- **中间件**：洋葱模型，7 个内置（含记忆）

```python
from app.harness import BaseSingleAgent

class MyAgent(BaseSingleAgent):
    name = "my_agent"
    backend = "deepagents"
    system_prompt = "你是一个助手"
    middleware = ["tracing", "filter"]

agent = MyAgent()
result = await agent.run("你好")
print(result.output)
```

---

## 2. 快速开始

### 安装依赖

```bash
uv pip install -e ".[dev]"
```

### 配置 LLM

`config/local.yaml`：
```yaml
llm:
  default_provider: qwen
  providers:
    qwen:
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
      api_key: "sk-xxx"
      model: "qwen-plus"
```

### 第一个 Agent

```python
from app.harness import BaseSingleAgent

class HelloAgent(BaseSingleAgent):
    name = "hello"
    backend = "llm"              # 最简后端，直接调 LLM
    system_prompt = "你用中文回答"

agent = HelloAgent()
result = await agent.run("介绍一下你自己")
print(result.output)
```

---

## 3. 9 种拓扑基类

### 3.1 Single（单 Agent）

最基础拓扑，一个 agent + 工具循环。

```python
from app.harness import BaseSingleAgent

class Researcher(BaseSingleAgent):
    name = "researcher"
    backend = "deepagents"       # 带 tools 时用 deepagents
    system_prompt = "你是研究助手"
    tools = ["web_search"]       # 引用 @tool 注册的工具
    middleware = ["tracing"]

result = await Researcher().run("研究一下 RAG 技术")
```

### 3.2 Parallel（并行）

多个 agent 同时跑同一输入，结果汇总。

```python
from app.harness import BaseParallelAgent

class ReviewSquad(BaseParallelAgent):
    name = "review_squad"
    members = [CriticA, CriticB, CriticC]    # 成员 agent 类
    aggregator = "merge"                      # merge(拼接) | list(JSON) | first(取首条)

result = await ReviewSquad().run("点评这个方案")
```

### 3.3 Sequential（顺序流水线）

前者输出喂后者，串行执行。

```python
from app.harness import BaseSequentialAgent

class ContentPipeline(BaseSequentialAgent):
    name = "content_pipeline"
    members = [Summarizer, Translator, Proofreader]

result = await ContentPipeline().run("LangChain is a framework...")
# Summarizer → Translator → Proofreader
```

### 3.4 Pipeline（顺序中嵌入并行）

顺序步骤中某步可并行执行，自定义聚合。

```python
from app.harness import BasePipelineAgent, PipelineStep

class DocReviewFlow(BasePipelineAgent):
    name = "doc_review"
    steps = [
        PipelineStep(run=OcrSorter, name="ocr"),          # 步骤1：单 agent
        PipelineStep(                                       # 步骤2：并行
            parallel=[TypoChecker, SealChecker, SignerChecker],
            aggregator="merge",
            name="checks",
        ),
        PipelineStep(run=ReportWriter, name="report"),     # 步骤3：汇总
    ]

result = await DocReviewFlow().run("审核这个文件")
```

`PipelineStep` 字段：
| 字段 | 说明 |
|------|------|
| `run` | 单 agent 类（顺序步骤） |
| `parallel` | agent 类列表（并行步骤） |
| `aggregator` | 并行结果合并方式：`merge`/`list`/`first` 或自定义聚合器名 |
| `name` | 步骤名（可选，用于日志） |

### 3.5 Conversational（多 Agent 群聊）

多个 agent 轮流发言，形成讨论。

```python
from app.harness import BaseConversationalAgent

class DebateRoom(BaseConversationalAgent):
    name = "debate_room"
    members = [Proposer, Opponent]
    rounds = 3                    # 每人发言 3 轮

result = await DebateRoom().run("AI 是否会取代程序员")
```

### 3.6 Router（意图路由）

LLM 分类意图，路由到对应 agent。

```python
from app.harness import BaseRouterAgent

class Dispatcher(BaseRouterAgent):
    name = "dispatcher"
    routes = {
        "billing": BillingAgent,
        "tech": TechSupportAgent,
        "_default": GeneralAgent,    # 兜底
    }

result = await Dispatcher().run("帮我查一下订单")
```

### 3.7 Plan and Execute（规划再执行）

planner 动态拆解任务为步骤，executor 逐步执行。

```python
from app.harness import BasePlanExecuteAgent

class ResearchPlan(BasePlanExecuteAgent):
    name = "research_plan"
    planner = Planner       # 规划 agent（输出 JSON 步骤）
    executor = Researcher   # 执行 agent（逐步执行）；None=planner 自己执行
    max_steps = 5

result = await ResearchPlan().run("写一篇关于 RAG 的科普文章")
```

### 3.8 Reflection（反思重试）

executor 执行 → evaluator 评估 → 不达标带反馈重试。

```python
from app.harness import BaseReflectionAgent

class CodingLoop(BaseReflectionAgent):
    name = "coding_loop"
    executor = Coder         # 写代码
    evaluator = Reviewer     # 审查代码
    max_iterations = 3       # 最多重试 3 次
    pass_threshold = 0.8     # 评分 >= 0.8 算通过

result = await CodingLoop().run("写一个二分查找函数")
```

### 3.9 Subagent（子 Agent 委派）

主 agent 通过 deepagents 原生 `task` 工具委派子任务。

```python
from app.harness import BaseSubagentAgent

class ResearchTeam(BaseSubagentAgent):
    name = "research_team"
    system_prompt = "你是协调者，把子任务委派给子 agent"
    subagents = [Researcher, Writer]

result = await ResearchTeam().run("研究 MCP 协议并写报告")
```

> **注意**：subagent 拓扑强制使用 deepagents 后端。

---

## 4. 后端切换（deepagents / agentscope / llm）

通过 `backend` 类属性切换，**改一行即可**：

```python
class MyAgent(BaseSingleAgent):
    backend = "deepagents"     # 工具循环 + 规划 + 文件系统
    # backend = "agentscope"   # ReAct 循环
    # backend = "llm"          # 最简，直接调 LLM（无工具循环）
```

| 后端 | 特点 | 适合场景 |
|------|------|---------|
| `llm` | 最快最省，直接调 LLM | 简单判断、规则类、无工具 |
| `deepagents` | 工具循环 + 规划 + 文件系统 + skills | 复杂推理、多步任务 |
| `agentscope` | ReAct 循环 + skills | 需要 agentscope 生态 |

---

## 5. 中间件（含记忆）

中间件用**洋葱模型**：`before` 按声明顺序执行，`after` 逆序执行。

### 使用方式

```python
class MyAgent(BaseSingleAgent):
    middleware = ["tracing", "session_memory", "filter"]
```

### 7 个内置中间件

| 名称 | 作用 | before | after |
|------|------|--------|-------|
| `tracing` | 运行追踪（耗时/日志） | 记录开始时间 | 计算耗时、写日志 |
| `context_memory` | 上下文记忆 | 透传（交后端原生管理） | 标记来源 |
| `session_memory` | 会话记忆 | load 历史，拼进 messages | 存本轮对话 |
| `summarization` | 摘要压缩 | 历史超 20 条时 LLM 摘要 | — |
| `filter` | 输入/输出过滤 | 敏感词检测 | 输出审核 |

### 会话记忆（持续对话）

```python
class ChatBot(BaseSingleAgent):
    name = "chatbot"
    backend = "deepagents"
    middleware = ["tracing", "session_memory"]    # 加 session_memory

# 每次调用传 session_id，agent 自动记住上文
await ChatBot().run("我叫张三", session_id="user-001")
await ChatBot().run("我叫什么？", session_id="user-001")  # 记得张三
```

> `session_memory` 需要数据库（`config.yaml` 的 `harness.session_datasource`）。DB 不可用时静默降级。

### 摘要压缩

```python
class LongChatBot(BaseSingleAgent):
    middleware = ["session_memory", "summarization"]
    # 历史超过 20 条时自动摘要旧消息，保留最近 4 条
```

---

## 6. 工具系统

### 定义工具

```python
from app.harness import tool

@tool("search_web")
async def search_web(query: str) -> str:
    """搜索互联网并返回结果。

    Args:
        query: 搜索关键词。
    """
    # 实际逻辑
    return f"搜索结果: {query}"
```

### 使用工具

```python
class Researcher(BaseSingleAgent):
    tools = ["search_web"]       # 引用工具名
    backend = "deepagents"       # 工具需要 deepagents 或 agentscope 后端
```

### 工具注册位置

- **全局工具**：`app/harness/tools/<name>.py` 或业务项目里任意位置
- **注意事项**：`@tool` 注册的是全局注册表，确保在 agent 运行前已 import

---

## 7. Skill 系统

Skill 是「教 agent 方法论的指令文档」（Agent Skills 规范），比 tool 更高层。

### 创建 Skill

```
skills/
└── code_review/
    └── SKILL.md
```

```markdown
---
name: code_review
description: 代码评审时用此 skill，提供结构化评审方法论。
---
# code_review
## Instructions
1. 先完整读代码
2. 按严重度分组：🔴 critical / 🟡 suggestion
3. 每条都给具体修复建议
```

### 使用 Skill

```python
class CodeReviewer(BaseSingleAgent):
    name = "reviewer"
    backend = "deepagents"
    skills = ["skills/code_review"]     # 目录路径（相对项目根）
    system_prompt = "你是代码评审员，按 code_review skill 的方法论评审"
```

> **注意**：skills 需要 `deepagents` 或 `agentscope` 后端（`llm` 后端不支持）。

---

## 8. 聚合器

并行步骤的结果合并方式。内置 3 个 + 自定义。

### 内置

| 名称 | 行为 |
|------|------|
| `merge` | 拼接所有输出（带 agent 名标注） |
| `list` | 返回 JSON 数组 `[{agent, output}]` |
| `first` | 取第一个（声明顺序） |

### 自定义聚合器

```python
from app.harness import aggregator

@aggregator("structured_summary")
def structured_summary(outputs: list[tuple[str, str]]) -> str:
    """outputs: [(agent名, 输出文本), ...]"""
    return "\n".join(f"[{n}]\n{o}" for n, o in outputs)

# 在 PipelineStep 里引用
PipelineStep(parallel=[AgentA, AgentB], aggregator="structured_summary")
```

---

## 9. Agent 间通讯（3 种模式）

所有通讯设施在 `agent.run()` 时自动创建，通过 `context` 共享。

### 9.1 共享黑板（Blackboard）

所有 agent 读写同一空间，解耦。

```python
class Researcher(BaseSingleAgent):
    name = "researcher"
    async def run(self, message):
        result = await self._invoke_backend(self.context)
        self.write("research_result", result)     # 写到黑板
        return AgentResult(output=result)

class Writer(BaseSingleAgent):
    name = "writer"
    async def run(self, message):
        research = self.read("research_result")    # 读 Researcher 写的
        prompt = f"基于以下研究写报告:\n{research}"
        result = await self._invoke_backend(...)
        return AgentResult(output=result)
```

API：`self.write(key, value)` / `self.read(key)` / `self.has(key)`

### 9.2 消息传递（MessageBus）

agent 间直接互发消息。

```python
class Critic(BaseSingleAgent):
    name = "critic"
    async def run(self, message):
        result = await self._invoke_backend(...)
        await self.send("writer", f"建议修改: {result}")     # 发通知
        return AgentResult(output=result)

class Writer(BaseSingleAgent):
    name = "writer"
    async def run(self, message):
        msgs = self.receive_messages()     # 取收件箱
        for m in msgs:
            print(f"收到 {m.sender}: {m.content}")
        ...
```

API：
- `await self.send(target, content)` — 发通知（不等回复）
- `await self.request(target, content)` — 发请求（等回复）
- `await self.reply(msg_id, content)` — 回复请求
- `self.receive_messages()` — 取收件箱（取后清空）

### 9.3 事件总线（EventBus）

发布/订阅，松耦合触发。

```python
class OcrAgent(BaseSingleAgent):
    name = "ocr"
    async def run(self, message):
        result = await self._invoke_backend(...)
        await self.publish("ocr_done", result)     # 发布事件
        return AgentResult(output=result)

class Notifier(BaseSingleAgent):
    name = "notifier"
    def setup(self):
        self.subscribe("ocr_done", self.on_ocr)    # 订阅事件

    async def on_ocr(self, event):
        print(f"OCR 完成了: {event.data}")

    async def run(self, message):
        return AgentResult(output="等待事件中...")
```

API：`await self.publish(event_type, data)` / `self.subscribe(event_type, handler)`

### 通讯使用场景

| 模式 | 适合场景 |
|------|---------|
| 黑板 | 多 agent 共享中间结果（研究→写作） |
| 消息 | agent A 直接请求 agent B 做某事 |
| 事件 | 「X 完成后自动触发 Y」（OCR 完成触发通知） |

---

## 10. HITL 人机协作

高风险操作前需人审批。

```python
class RiskyAgent(BaseSingleAgent):
    name = "risky"
    backend = "deepagents"
    hitl_require_confirmation = True     # 开启人工确认

# 第一次调用返回 pending（不执行）
result = await RiskyAgent().run("删除数据库")
# result.extra["status"] == "awaiting_confirmation"
# result.extra["run_id"] == "xxx"

# 确认后执行
result2 = await RiskyAgent().confirm(result.extra["run_id"], decision="approve")
# 或拒绝
# result2 = await RiskyAgent().confirm(run_id, decision="reject")
```

---

## 11. 流式输出

```python
class ChatAgent(BaseSingleAgent):
    backend = "deepagents"
    system_prompt = "你是助手"

agent = ChatAgent()
async for chunk in agent.stream("讲个故事"):
    print(chunk, end="", flush=True)
```

---

## 12. 文件预处理

用于文档审核场景：下载文件 → 类型判断 → 图片分割放大。

```python
from app.harness.preprocess import preprocess_file, download_file, detect_file_type, split_and_enlarge

# 预处理：下载 + 判断类型 + 图片分割放大
await preprocess_file(ctx)     # ctx 是 AgentRunContext

# 单独用
file_bytes = await download_file("https://example.com/doc.pdf")
file_type = detect_file_type(file_bytes, "doc.pdf")    # "image" / "pdf" / "docx"
images = split_and_enlarge(file_bytes, enlarge_ratio=8, split_count=4)
```

图片处理参数在 `config.yaml`：
```yaml
doc_review:
  enlarge_ratio: 8            # 放大倍数
  split_count: 4              # 长图分割份数
  long_image_threshold: 2000  # 高度超过此值视为长图（像素）
```

---

## 13. 章节提取

从 OCR markdown 拆分章节，检查项按需取对应章节。

```python
from app.harness.sections import extract_sections, pick_sections

sections = extract_sections(ocr_markdown)
# {"cover": "...", "一、项目概况": "...", "tail": "..."}

content = pick_sections(sections, ["项目概况", "cover"])
# 模糊匹配标题关键词，取对应章节内容
```

特殊章节名：
- `"full"` — 全文
- `"cover"` — 封面区（第一个标题前）
- `"tail"` — 末尾区（签字盖章）

---

## 14. 项目结构规范

### 框架核心（不改）

```
app/harness/
├── base.py                 # BaseAgent（底层基类）
├── agents/                 # 9 拓扑基类
├── backends/               # 3 后端（deepagents/agentscope/llm）
├── middleware/             # 7 中间件
├── communication/          # agent 间通讯（黑板/消息/事件）
├── tools/                  # @tool 工具系统
├── aggregators/            # @aggregator 聚合器
├── memory/                 # 持久化存储
├── context.py              # AgentRunContext / AgentResult
├── preprocess.py           # 文件预处理
└── sections.py             # 章节提取
```

### 业务项目（你的代码）

```
app/projects/
└── <你的项目名>/
    ├── __init__.py
    ├── agents.py            # 继承基类定义 agent
    ├── service.py           # 业务编排（可选）
    └── endpoint.py          # HTTP 端点（可选）
```

### 新建业务项目

```bash
mkdir -p app/projects/my_project
touch app/projects/my_project/__init__.py
```

```python
# app/projects/my_project/agents.py
from app.harness import BaseSingleAgent

class MyAgent(BaseSingleAgent):
    name = "my_agent"
    backend = "deepagents"
    system_prompt = "..."
```

---

## 15. 完整示例：文档审核

见 `app/projects/doc_review/`。

```python
from app.harness import BasePipelineAgent, BaseSingleAgent, PipelineStep, tool

# 工具
@tool("check_name_conflict")
async def check_name_conflict(name1: str, name2: str) -> str:
    """检查两人是否同一人。"""
    ...

# Agent
class TypoChecker(BaseSingleAgent):
    name = "typo"
    backend = "deepagents"
    system_prompt = "检查错别字"
    middleware = ["tracing"]

class SealChecker(BaseSingleAgent):
    name = "seal"
    backend = "deepagents"
    tools = ["check_seal_supplier"]
    middleware = ["tracing"]

# Pipeline
class DocReviewFlow(BasePipelineAgent):
    name = "doc_review"
    steps = [
        PipelineStep(run=OcrSorter),
        PipelineStep(parallel=[TypoChecker, SealChecker], aggregator="merge"),
        PipelineStep(run=ReportWriter),
    ]
    middleware = ["tracing"]

# 使用
flow = DocReviewFlow()
result = await flow.run("审核这个文件")
```

---

## 类属性速查表

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | str | `""` | agent 名（必填） |
| `backend` | str | `"deepagents"` | `deepagents`/`agentscope`/`llm` |
| `provider` | str | `""` | LLM provider（空=默认） |
| `system_prompt` | str | `""` | 系统提示词 |
| `tools` | list[str] | `[]` | 工具名列表 |
| `middleware` | list[str] | `[]` | 中间件名列表 |
| `skills` | list[str] | `[]` | skill 目录路径 |
| `model` | str/None | None | 覆盖 provider 默认模型 |
| `temperature` | float/None | None | 温度 |
| `recursion_limit` | int | 25 | 执行步数上限 |
| `hitl_require_confirmation` | bool | False | 需人工确认 |

## 拓扑特有属性

| 拓扑 | 属性 | 说明 |
|------|------|------|
| Parallel | `members`, `aggregator` | 成员 agent 类列表，合并方式 |
| Sequential | `members` | 成员 agent 类列表 |
| Pipeline | `steps` | PipelineStep 列表 |
| Conversational | `members`, `rounds` | 成员列表，发言轮数 |
| Router | `routes` | 意图→agent 类映射 |
| PlanExecute | `planner`, `executor`, `max_steps` | 规划/执行 agent 类 |
| Reflection | `executor`, `evaluator`, `max_iterations`, `pass_threshold` | 执行/评估 agent 类 |
| Subagent | `subagents` | 子 agent 类列表 |
