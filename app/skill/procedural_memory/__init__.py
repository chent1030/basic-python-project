"""Procedural 调度记忆（FR-11）。

三层记忆中的「程序/调度」层：记录「分类 + 区域 + 决策」三元组的成功/失败样例，
供 AI 初审召回时拼接到 prompt，让模型看到历史同类问题怎么处置。

- 与 episodic（事件层）按 issue 维度的「单条事实」区别；
- 与 semantic（语义层 pattern）的「分类 + 区域」中心向量区别；
- procedural 更窄：必须带 ``decision`` + ``ai_relation``（裁决一致性），并按
  ``category_l1_id + area + decision`` 完全一致合并（consolidate）。
"""

__all__: list[str] = []