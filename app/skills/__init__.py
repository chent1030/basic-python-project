"""可复用 Skills 包。

对齐 PRD §21「报告 Skill」与设计 §3.3 / §7.2（W8）：
- Skill 负责按分类（§21）模板拼装报告内容；
- Skill 接口是普通 Python Protocol + 默认 deterministic 实现；
- 无 LLM 也应可生成骨架（确定性路径 + 数据快照 → HTML）；
- 未来允许替换实现（如基于 LangGraph 编排），但本波次不引入外部框架依赖。
"""