"""示例 agent 目录。

每个子目录是一个 agent,内含 config.yml(必须)+ 可选 tools.py(专属工具)。
registry 启动时扫描本目录,自动发现所有 agent。

覆盖 6 种拓扑的示例:
- researcher          single + deepagents(研究助手,带工具)
- support_bot         single + agentscope(客服,持续对话)
- writer              single(子流水线成员)
- summarizer/translator/proofreader  single(顺序流水线成员)
- critic              single(并行/协作成员)
- research_team       subagent(主 agent + 子 agent 委派)
- content_pipeline    sequential(顺序流水线)
- review_squad        parallel(并行评审)
- debate_room         conversational(群聊讨论)
- dispatcher          router(意图路由)

provider 留空=用 llm.default_provider。把真实 provider 放进 local.yaml 后即可运行。
"""
