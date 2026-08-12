"""组合测试 —— 多拓扑 + 通讯 + 工具 + 中间件综合验证。

场景：「技术调研报告生成」
  1. Router: 按主题路由到「技术研究」或「市场研究」
  2. 技术研究用 PlanExecute: 规划→执行
  3. 市场研究用 Parallel: 多视角并行分析
  4. 两个分支的结果通过黑板共享
  5. 最终用 Sequential 汇总成报告
  6. 全程用 tracing 中间件 + @tool 工具

验证：拓扑组合、后端混用、通讯、工具、中间件联动。
"""
from __future__ import annotations

import asyncio
import sys
import traceback


async def main():
    from app.harness import (
        BaseParallelAgent,
        BasePipelineAgent,
        BasePlanExecuteAgent,
        BaseRouterAgent,
        BaseSequentialAgent,
        BaseSingleAgent,
        PipelineStep,
        tool,
    )

    # 确保 LLM 启动
    from app.services.llm import llm
    await llm.startup()
    print(f"LLM 就绪: {llm.providers}")

    # ==================== 工具 ====================
    @tool("word_count")
    async def word_count(text: str) -> str:
        """统计文本字数。

        Args:
            text: 要统计的文本。
        """
        return f"字数: {len(text)}"

    # ==================== Single Agents ====================
    class TechPlanner(BaseSingleAgent):
        """技术研究的规划 agent"""
        name = "tech_planner"
        backend = "llm"
        system_prompt = '你是任务规划师。把任务拆 2 步。只输出 JSON: {"steps": ["步骤1","步骤2"]}'

    class TechExecutor(BaseSingleAgent):
        """技术研究的执行 agent"""
        name = "tech_executor"
        backend = "llm"
        system_prompt = "你是技术分析师。执行给定步骤，一句话回答。"
        middleware = ["tracing"]

    class MarketAnalyst(BaseSingleAgent):
        """市场分析 agent（并行成员）"""
        name = "market_analyst"
        backend = "llm"
        system_prompt = "你是市场分析师，一句话分析市场前景。"
        middleware = ["tracing"]

    class RiskAnalyst(BaseSingleAgent):
        """风险分析 agent（并行成员）"""
        name = "risk_analyst"
        backend = "llm"
        system_prompt = "你是风险分析师，一句话分析潜在风险。"
        middleware = ["tracing"]

    class ReportWriter(BaseSingleAgent):
        """报告撰写 agent"""
        name = "report_writer"
        backend = "deepagents"
        system_prompt = (
            "你是报告撰写专家。基于前面的分析和风险信息，写一段简短的综合报告（3-5句话）。"
        )
        tools = ["word_count"]
        middleware = ["tracing"]

    # ==================== 复合拓扑 ====================

    class TechResearch(BasePlanExecuteAgent):
        """技术研究:PlanExecute(规划→执行)"""
        name = "tech_research"
        planner = TechPlanner
        executor = TechExecutor
        max_steps = 3

    class MarketResearch(BaseParallelAgent):
        """市场研究:Parallel(市场+风险并行)"""
        name = "market_research"
        members = [MarketAnalyst, RiskAnalyst]
        aggregator = "merge"

    class ResearchRouter(BaseRouterAgent):
        """路由:按主题分到技术研究或市场研究"""
        name = "research_router"
        routes = {
            "tech": TechResearch,
            "market": MarketResearch,
        }

    # ==================== 最终流水线 ====================
    class FullPipeline(BasePipelineAgent):
        """完整流水线:路由研究 → [并行分析] → 报告"""
        name = "full_pipeline"
        steps = [
            PipelineStep(run=ResearchRouter, name="research"),
            PipelineStep(
                parallel=[MarketAnalyst, RiskAnalyst],
                aggregator="merge",
                name="analysis",
            ),
            PipelineStep(run=ReportWriter, name="report"),
        ]
        middleware = ["tracing"]

    # ==================== 执行测试 ====================
    print("\n" + "=" * 60)
    print("  组合测试：技术调研报告生成")
    print("  拓扑组合: Pipeline(Router → Parallel → Single)")
    print("  后端混用: llm + deepagents")
    print("  工具: word_count")
    print("  中间件: tracing")
    print("=" * 60)

    inp = "AI Agent 框架的技术趋势和市场前景"
    print(f"\n  输入: {inp}")

    try:
        result = await FullPipeline().run(inp)
        print(f"\n  最终输出:\n  {result.output[:500]}")
        print(f"\n  topology: {result.extra.get('topology')}")
        steps = result.extra.get("steps", [])
        print(f"  步骤数: {len(steps)}")
        for s in steps:
            print(f"    - {s.get('step', '?')} ({s.get('kind', '?')})")
        tracing = result.extra.get("tracing", {})
        print(f"  耗时: {tracing.get('duration_ms', '?')}ms")

        # 断言
        assert result.output, "输出不应为空"
        assert result.extra.get("topology") == "pipeline"
        assert len(steps) == 3, f"应有 3 个步骤,实际 {len(steps)}"
        print("\n  ✅ 组合测试通过！")

    except Exception as e:
        print(f"\n  ❌ 组合测试失败: {e}")
        traceback.print_exc()
        return 1

    # ==================== 单独验证各复合拓扑 ====================
    print("\n" + "=" * 60)
    print("  子测试：单独验证各复合拓扑")
    print("=" * 60)

    # PlanExecute 单独跑
    print("\n  [PlanExecute] 技术研究...")
    r1 = await TechResearch().run("研究 RAG 技术")
    print(f"    规划: {r1.extra.get('plan', [])}")
    print(f"    输出: {r1.output[:100]}")
    assert r1.extra.get("topology") == "plan_execute"
    print("    ✅")

    # Parallel 单独跑
    print("\n  [Parallel] 市场研究...")
    r2 = await MarketResearch().run("AI 编程助手市场")
    members = r2.extra.get("members", [])
    print(f"    成员数: {len(members)}")
    print(f"    输出: {r2.output[:100]}")
    assert r2.extra.get("topology") == "parallel"
    assert len(members) == 2
    print("    ✅")

    # Router 单独跑
    print("\n  [Router] 路由...")
    r3 = await ResearchRouter().run("这个技术有什么风险")
    router_info = r3.extra.get("router", {})
    print(f"    路由: {router_info}")
    assert r3.extra.get("topology") == "router"
    print("    ✅")

    print("\n" + "=" * 60)
    print("  全部组合测试通过！")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
