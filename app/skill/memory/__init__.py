"""I-line 长期记忆体系（波次 9，FR-09 主诉求：agent 必须根据历史审核情况自主进化）。

三层记忆架构（episodic / semantic / procedural）：
- **episodic（事件层）**:``source_table=EVENT``——单条事件事实（12 类初始评审事件）；
- **semantic（语义层）**:``source_table=ADJUDICATION``——人工裁决 → 聚合出 skill pattern；
- **procedural（程序/调度层）**:本波次**预留不实现**（FR-11/12 后续波次）。

数据落 ai-experience-postgres（库名 ``cps_memory``，与 postgres_primary 分库）。
本仓 alembic 维护 schema 形态；运行期连接由 :mod:`infrastructure.bootstrap` 装配。
"""
from __future__ import annotations

__all__: list[str] = []