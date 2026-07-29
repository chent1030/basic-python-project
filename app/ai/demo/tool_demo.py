"""Demo:工具系统 —— deepagents + agentscope 两后端对比。

两后端都支持工具调用,工具定义方式完全一致(@tool 装饰器),
后端适配器自动转换格式(deepagents→LangChain BaseTool / agentscope→Tool)。
本 demo 展示:
  1) 工具的注册(全局 + 专属)
  2) 两后端 agent(tool_demo_da / tool_demo_as)调用同一套工具

运行(查看注册):
    python -m app.ai.demo.tool_demo
"""
from __future__ import annotations

from app.ai.tools import all_tools, discover_global_tools, tool


@tool("demo_echo")
async def demo_echo(text: str) -> str:
    """原样返回输入文本(就地定义的演示工具)。

    Args:
        text: 要回显的文本。
    """
    return f"echo: {text}"


def main() -> None:
    discover_global_tools()  # 发现 app/ai/tools/ 下的全局工具
    print("=== 已注册工具(全局 + 各 agent 专属) ===")
    # 注意:专属工具在 registry 加载对应 agent 时才注册;这里先看全局 + 本 demo 定义的
    for name, tdef in sorted(all_tools().items()):
        origin = "专属" if tdef.namespace != "global" else "全局"
        print(f"  [{origin}] {name}  params={tdef.params}")
        print(f"          desc: {tdef.description[:60]}")
    print()
    print("=== 两后端工具调用 agent ===")
    print("  deepagents : tool_demo_da(全局 web_search + 专属 calc/weather)")
    print("  agentscope : tool_demo_as(全局 web_search + 专属 calc/weather)")
    print("  两者工具定义完全一致,仅后端不同。触发示例见 trigger_demo 的调用方式。")


if __name__ == "__main__":
    main()
