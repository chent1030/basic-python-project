"""FR-10 历史/覆盖分析视图与服务（波次 10）。

为什么在 memory 模块之外另设 coverage（架构决策）：
- memory 是「检索语义」——围绕 memory_entry 沉淀历史裁决/事件/问题，供 AI 初审
  ``retrieve_context_for_issue`` 拉历史做 prompt 上下文；
- coverage 是「交易/分析语义」——围绕 Java 端聚合查询端点产出频次、主管负载、覆盖缺口，
  供 CPS 管理员做覆盖度评估与治理决策，本质是分析计算而非记忆检索；
- 两者共用底层 session_factory 模式（postgres_primary），但不复用表——coverage 落
  memory_metric_snapshot 表是借用 FR-12 评估侧的占位，不与 memory_entry 语义混线；
- 后续 FR-12 评估侧接续时，``CoverageIngestionService.record_snapshot`` 已留接口，
  本波不消费。

四分析（与 Java 端契约对齐）：
- frequency：按 (factory, area, category_l1_id) 聚合 issue/recent/closed/close_rate，
  算最高频 5 个组合；
- region-supervisor：每个主管在手/超期排序，前 10；
- recurrence：同 issue/version 在周期内被关闭多次（复发问题）；
- gaps：按 storageRoomType 聚合的覆盖缺口（无最近记录、严重度分级）。
"""
from __future__ import annotations

__all__: list[str] = []