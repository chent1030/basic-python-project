"""Harness 框架逐拓扑测试 —— 每种拓扑独立测试，有输入有输出。

顺序执行 9 种拓扑 + 3 种通讯，每种打印输入/输出。
用真实 qwen LLM 调用。
"""
from __future__ import annotations

import asyncio
import json
import sys
import traceback


def header(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def show(label: str, value: str, max_len: int = 300) -> None:
    v = value[:max_len] + ("..." if len(value) > max_len else "")
    print(f"  {label}: {v}")


async def test_single_llm():
    """1. Single 拓扑 - llm 后端（最简）"""
    from app.harness import BaseSingleAgent

    class GreetAgent(BaseSingleAgent):
        name = "greet"
        backend = "llm"
        system_prompt = "你用中文回答，简洁。"

    header("1. Single / llm 后端")
    inp = "一句话介绍 Python"
    show("输入", inp)
    r = await GreetAgent().run(inp)
    show("输出", r.output)
    assert r.output and len(r.output) > 5, "输出不应为空"
    print("  ✅ 通过")


async def test_single_deepagents():
    """2. Single 拓扑 - deepagents 后端（带工具循环）"""
    from app.harness import BaseSingleAgent, tool

    @tool("calc")
    async def calc(expression: str) -> str:
        """计算数学表达式。

        Args:
            expression: 数学表达式。
        """
        try:
            return str(eval(expression))  # noqa: S307 演示用
        except Exception:
            return f"无法计算: {expression}"

    class CalcAgent(BaseSingleAgent):
        name = "calc_agent"
        backend = "deepagents"
        system_prompt = "你是计算助手。用 calc 工具算数学题，然后用中文回答。"
        tools = ["calc"]
        recursion_limit = 25

    header("2. Single / deepagents 后端（带工具）")
    inp = "帮我算一下 15 乘以 23"
    show("输入", inp)
    r = await CalcAgent().run(inp)
    show("输出", r.output)
    assert "345" in r.output, "15*23=345 应在输出中"
    print("  ✅ 通过")


async def test_parallel():
    """3. Parallel 拓扑 - 多 agent 并行"""
    from app.harness import BaseParallelAgent, BaseSingleAgent

    class Critic(BaseSingleAgent):
        name = "critic"
        backend = "llm"
        system_prompt = "你是评论员，一句话点评。"

    class ReviewSquad(BaseParallelAgent):
        members = [Critic]
        aggregator = "merge"

    header("3. Parallel 拓扑（3 个 Critic 并行）")

    class ReviewSquad3(BaseParallelAgent):
        members = [Critic, Critic, Critic]
        aggregator = "merge"

    inp = "用 LLM 做客服的方案"
    show("输入", inp)
    r = await ReviewSquad3().run(inp)
    show("输出", r.output)
    assert r.extra.get("topology") == "parallel"
    assert len(r.extra.get("members", [])) == 3
    print("  ✅ 通过")


async def test_sequential():
    """4. Sequential 拓扑 - 流水线"""
    from app.harness import BaseSequentialAgent, BaseSingleAgent

    class Summarizer(BaseSingleAgent):
        name = "summarizer"
        backend = "llm"
        system_prompt = "把输入压缩成一句话摘要。只输出摘要。"

    class Translator(BaseSingleAgent):
        name = "translator"
        backend = "llm"
        system_prompt = "把输入翻译成中文。只输出译文。"

    class Pipe(BaseSequentialAgent):
        members = [Summarizer, Translator]

    header("4. Sequential 拓扑（摘要→翻译）")
    inp = "LangChain is a framework for building applications powered by LLMs."
    show("输入", inp)
    r = await Pipe().run(inp)
    show("输出", r.output)
    assert r.extra.get("topology") == "sequential"
    assert len(r.extra.get("steps", [])) == 2
    print("  ✅ 通过")


async def test_pipeline():
    """5. Pipeline 拓扑 - 顺序中嵌入并行"""
    from app.harness import BasePipelineAgent, BaseSingleAgent, PipelineStep

    class Analyst(BaseSingleAgent):
        name = "analyst"
        backend = "llm"
        system_prompt = "你是分析师，一句话分析。"

    class Reporter(BaseSingleAgent):
        name = "reporter"
        backend = "llm"
        system_prompt = "你是报告员，基于分析结果写一句结论。"

    class Flow(BasePipelineAgent):
        steps = [
            PipelineStep(run=Analyst, name="analyze"),
            PipelineStep(parallel=[Analyst, Analyst], aggregator="merge", name="multi"),
            PipelineStep(run=Reporter, name="report"),
        ]

    header("5. Pipeline 拓扑（分析→[并行分析]→报告）")
    inp = "AI 对就业市场的影响"
    show("输入", inp)
    r = await Flow().run(inp)
    show("输出", r.output)
    assert r.extra.get("topology") == "pipeline"
    assert len(r.extra.get("steps", [])) == 3
    print("  ✅ 通过")


async def test_conversational():
    """6. Conversational 拓扑 - 群聊"""
    from app.harness import BaseConversationalAgent, BaseSingleAgent

    class Proponent(BaseSingleAgent):
        name = "pro"
        backend = "llm"
        system_prompt = "你是正方，一句话陈述支持观点。"

    class Opponent(BaseSingleAgent):
        name = "con"
        backend = "llm"
        system_prompt = "你是反方，一句话陈述反对观点。"

    class Debate(BaseConversationalAgent):
        members = [Proponent, Opponent]
        rounds = 2

    header("6. Conversational 拓扑（正反方辩论 2 轮）")
    inp = "远程办公是否应该普及"
    show("输入", inp)
    r = await Debate().run(inp)
    show("输出", r.output)
    assert r.extra.get("topology") == "conversational"
    assert len(r.extra.get("transcript", [])) >= 4  # 2 人 × 2 轮
    print("  ✅ 通过")


async def test_router():
    """7. Router 拓扑 - 意图路由"""
    from app.harness import BaseRouterAgent, BaseSingleAgent

    class BillingBot(BaseSingleAgent):
        name = "billing"
        backend = "llm"
        system_prompt = "你是账单助手，一句话回答。"

    class TechBot(BaseSingleAgent):
        name = "tech"
        backend = "llm"
        system_prompt = "你是技术支持，一句话回答。"

    class Dispatcher(BaseRouterAgent):
        routes = {"billing": BillingBot, "tech": TechBot}

    header("7. Router 拓扑（意图路由）")
    inp = "我的账单金额不对"
    show("输入", inp)
    r = await Dispatcher().run(inp)
    show("输出", r.output)
    show("路由意图", str(r.extra.get("router", {})))
    assert r.extra.get("topology") == "router"
    print("  ✅ 通过")


async def test_plan_execute():
    """8. PlanExecute 拓扑 - 规划再执行"""
    from app.harness import BasePlanExecuteAgent, BaseSingleAgent

    class Planner(BaseSingleAgent):
        name = "planner"
        backend = "llm"
        system_prompt = (
            '把任务拆成 2-3 步。只输出 JSON: {"steps": ["步骤1","步骤2"]}'
        )

    class Executor(BaseSingleAgent):
        name = "executor"
        backend = "llm"
        system_prompt = "执行给定步骤，一句话完成。"

    class Flow(BasePlanExecuteAgent):
        planner = Planner
        executor = Executor
        max_steps = 4

    header("8. PlanExecute 拓扑（规划→执行）")
    inp = "组织一次技术分享会"
    show("输入", inp)
    r = await Flow().run(inp)
    show("规划步骤", str(r.extra.get("plan", [])))
    show("最终输出", r.output)
    assert r.extra.get("topology") == "plan_execute"
    print("  ✅ 通过")


async def test_reflection():
    """9. Reflection 拓扑 - 反思重试"""
    from app.harness import BaseReflectionAgent, BaseSingleAgent

    class Coder(BaseSingleAgent):
        name = "coder"
        backend = "llm"
        system_prompt = "你是程序员。按要求写 Python 代码，只输出代码。"

    class Reviewer(BaseSingleAgent):
        name = "reviewer"
        backend = "llm"
        system_prompt = (
            '你是代码审查员。只输出 JSON: {"pass": true/false, "score": 0.0-1.0, '
            '"feedback": "改进建议"}'
        )

    class CodingLoop(BaseReflectionAgent):
        executor = Coder
        evaluator = Reviewer
        max_iterations = 2
        pass_threshold = 0.8

    header("9. Reflection 拓扑（写代码→审查→重试）")
    inp = "写一个二分查找函数"
    show("输入", inp)
    r = await CodingLoop().run(inp)
    show("迭代次数", str(r.extra.get("total_iterations", "?")))
    show("通过", str(r.extra.get("passed")))
    show("最终输出", r.output[:150])
    assert r.extra.get("topology") == "reflection"
    print("  ✅ 通过")


async def test_subagent():
    """10. Subagent 拓扑 - 子 agent 委派"""
    from app.harness import BaseSubagentAgent, BaseSingleAgent

    class SubResearcher(BaseSingleAgent):
        name = "sub_researcher"
        backend = "deepagents"
        system_prompt = "你是研究员，一句话总结。"

    class SubWriter(BaseSingleAgent):
        name = "sub_writer"
        backend = "deepagents"
        system_prompt = "你是写手，一句话成稿。"

    class Team(BaseSubagentAgent):
        name = "team"
        system_prompt = "你是协调者，把任务委派给子 agent"
        subagents = [SubResearcher, SubWriter]
        recursion_limit = 40

    header("10. Subagent 拓扑（主→子 agent 委派）")
    inp = "研究 RAG 技术并写一段简介"
    show("输入", inp)
    r = await Team().run(inp)
    show("输出", r.output)
    assert r.extra.get("topology") == "subagent"
    print("  ✅ 通过")


async def test_communication():
    """11. 通讯测试 - 黑板 + 消息 + 事件"""
    from app.harness import BaseSequentialAgent, BaseSingleAgent

    header("11. Agent 通讯（黑板）")

    class Writer(BaseSingleAgent):
        name = "comm_writer"
        backend = "llm"
        system_prompt = "你是写手，用一句话写内容。"

        async def run(self, message, **kw):
            from app.harness.context import AgentResult, AgentRunContext
            ctx = AgentRunContext(agent_name=self.name, messages=self._build_messages(message))
            self._context = ctx
            text = await self._invoke_backend(ctx)
            self.write("draft", text)           # 写到黑板
            return AgentResult(output=text)

    class Reader(BaseSingleAgent):
        name = "comm_reader"
        backend = "llm"
        system_prompt = "你总结收到的内容。"

        async def run(self, message, **kw):
            from app.harness.context import AgentResult, AgentRunContext
            draft = self.read("draft")           # 从黑板读
            ctx = AgentRunContext(
                agent_name=self.name,
                messages=[{"role": "user", "content": f"总结: {draft}"}],
            )
            self._context = ctx
            text = await self._invoke_backend(ctx)
            return AgentResult(output=text)

    class CommPipe(BaseSequentialAgent):
        members = [Writer, Reader]

    inp = "AI 的未来发展趋势"
    show("输入", inp)
    r = await CommPipe().run(inp)
    show("最终输出", r.output)
    print("  ✅ 通过（黑板通讯）")


async def main():
    # 确保 LLM 服务已启动（独立脚本需要手动调）
    from app.services.llm import llm
    await llm.startup()
    print(f"LLM 就绪: providers={llm.providers}")

    tests = [
        ("Single/llm", test_single_llm),
        ("Single/deepagents", test_single_deepagents),
        ("Parallel", test_parallel),
        ("Sequential", test_sequential),
        ("Pipeline", test_pipeline),
        ("Conversational", test_conversational),
        ("Router", test_router),
        ("PlanExecute", test_plan_execute),
        ("Reflection", test_reflection),
        ("Subagent", test_subagent),
        ("Communication", test_communication),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            await fn()
            passed += 1
        except Exception as e:
            print(f"\n  ❌ {name} 失败: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"  结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")
    print(f"{'='*60}")
    return failed


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
