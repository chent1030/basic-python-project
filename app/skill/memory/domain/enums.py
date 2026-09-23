"""记忆源表枚举（I-line 三层分类的根标识）。

Source 决定「这条记忆属于事实/语义/程序」哪一层；同一表结构按 source_table 字段
路由。后续 wave 接入 procedural 层时，新增 ``PROCEDURAL`` 值即可（无需新表）。
"""
from __future__ import annotations

from enum import StrEnum


class SourceTable(StrEnum):
    """记忆来源类型——决定 row 在三层记忆中的归属。"""

    ADJUDICATION = "ADJUDICATION"  # 语义层（人工裁决 → memory_skill_pattern 原料）
    EVENT = "EVENT"                # 事件层（12 类初始评审事件）
    ISSUE = "ISSUE"                # 问题单本体（结构性背景，供语义检索过滤）


__all__ = ["SourceTable"]