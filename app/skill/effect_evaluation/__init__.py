"""效果评估（FR-12）。

依赖已有 ``memory_metric_snapshot`` 表 + Java 端 ``/api/cps/admin/coverage/effect`` 端点：

- 周期内拉取四 metric（ai_pass_rate / human_override_rate / recurrence_rate_30d /
  coverage_gap_count），落 ``memory_metric_snapshot``（metric_key 唯一）；
- 跨周期 diff：delta_pct + significance（按阈值打标）；
- 回归检测：ai_pass_rate 下降 >5% / human_override_rate 上升 >10% /
  recurrence_rate_30d 上升 >20% → 标记 regression。
"""

__all__: list[str] = []