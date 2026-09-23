# 波次 10 — Python 侧 FR-10 历史/覆盖分析视图与服务

> I 线程序/统计层：在 episodic + semantic 之上做覆盖度与复发度分析，给运营/产品/治理提供
> 聚合查询能力。本波实现四个分析端点（frequency / region-supervisor / recurrence / gaps）
> + CoverageSummary 聚合视图 + 与 FR-12 治理评估的留口（`memory_metric_snapshot`）。

## 1. 架构决策

### 1.1 为什么 coverage 是独立模块，不并入 memory

任务书指定 coverage 单独成模块（`app/skill/coverage/`）。理由：

- **职责分离**：memory 是检索语义（"这个 issue 历史上如何处理"），coverage 是分析语义
  （"这批 issue 总体覆盖度如何"）。两者使用的 IO 形态完全不同：
  - memory 写多读多，依赖 pgvector 语义检索；
  - coverage 拉多算多，依赖 Java 侧聚合后做客户端计算/排序。
- **同表共用，不冲突**：coverage 写入 `memory_metric_snapshot`（与 memory 共用一张
  `memory_metric_snapshot` 表），通过 `metric_key='coverage_analysis_<period>'` 命名空间
  隔离，不污染 memory 的语义检索。
- **未来扩展**：后续 FR-11/FR-12 治理评估接入后，coverage 模块可独立演化为
  「I 线治理后端」，不影响 memory 的检索路径。

### 1.2 为什么复用 `memory_metric_snapshot` 表，不新建表

`memory/infrastructure/repository.py:192` 已存在 `MemoryMetricSnapshotORM`：
- 字段：`id`、`period_start/end`、`metric_key(64)`、`metric_value(JSONB)`、`recorded_at`；
- 已经有 `metric_key` 唯一索引（PG `ON CONFLICT (metric_key)`），覆盖分析快照
  按周期 upsert 即可；
- 避免 schema 蔓延**——FR-12 治理评估后续可直接读同一张表做效果对比。

`coverage_app.py` 的 `CoverageRepository` 直接使用 `MemoryMetricSnapshotORM`
（不再依赖 `MemoryRepository`），保持 I 线内部模块边界。

### 1.3 纯计算模式 vs 持久化模式

`CoverageAnalysisService` 接受可选 `repository=None`：
- **传入 None**：纯计算模式，只从 Java 拉数据并算 `CoverageSummary`，不落库；
  适用一次性查询（GET /coverage/analyze）。
- **传入 repo**：调用 `CoverageIngestionService.record_snapshot` 时落 `memory_metric_snapshot`，
  按 `metric_key` upsert；适用治理批跑（POST /coverage/snapshot）。

### 1.4 `_collect_all_pages` 反射式签名适配

四个 fetch 方法（frequency / region_supervisor / recurrence / gaps）签名差异较大：

| 端点 | filter 参数 |
|---|---|
| frequency / gaps | factory / area / category_l1_id |
| region_supervisor | region |
| recurrence | threshold |

`_collect_all_pages` 通过 `inspect.signature(fetch_one).parameters`
检测方法支持的可选 kwarg，只传 method 实际声明的参数。这样新增端点无需改动
`_collect_all_pages`——只要按惯例命名 filter 参数即可。

## 2. 四个分析算法

### 2.1 frequency（高频组 / 周期）

拉 Java `/api/cps/admin/coverage/frequency` 全部页（iter_all_pages）→
按 `(factory, area, category_l1_id)` 桶分组合并：
- `issue_count = sum(issue_count)`、`closed_count = sum(closed_count)`、
  `overdue_count = sum(overdue_count)`、`recurrence_count = sum(recurrence_count)`；
- `close_rate = closed_count / issue_count`（除零 → 0.0）；
- 排序：先 `issue_count desc`，再 `overdue_count desc`；
- 截 `top_k`（默认 5）。

### 2.2 region-supervisor load（主管在手/超期）

拉 `/api/cps/admin/coverage/region-supervisor` →
排序：先 `overdue_count desc`，再 `open_count desc`→
截 `top 10`（主管超期过多者优先暴露给运营）。

### 2.3 recurrence（复发问题）

拉 `/api/cps/admin/coverage/recurrence` →
过滤 `recurrence_count >= threshold`（默认 2）→
排序 `recurrence_count desc`→ 截 `top_k`（默认 5）。

### 2.4 gaps（覆盖缺口）

拉 `/api/cps/admin/coverage/gaps` →
按 `storage_room_type` 桶分组：
- `gap_severity = max(gap_severity)`、`days_since_last_record = max(days_since_last_record)`、
  `gap_count = sum(1)`；
- 排序 `gap_severity desc` → 截 `top_k`（默认 10）。

## 3. CoverageSummary 聚合视图

`summary()` 串行调用四个分析（可 `kinds` 子集过滤），聚合产出：

```python
{
  "period_start": "2024-01-01T00:00:00+00:00",
  "period_end":   "2024-01-31T00:00:00+00:00",
  "frequency_trends":       [...],
  "region_supervisor_loads": [...],
  "recurrences":             [...],
  "gaps":                    [...],
  "summary_metrics": {
    "total_issues":    <int>,  # 所有 bucket issue_count 求和
    "total_gaps":      <int>,  # gaps 桶数
    "avg_close_rate":  <float>,
    "p50_recur_count": <int>,  # recurrences.issue_count 中位数
  },
  "anomalies": {
    "top_3_high_freq":        [{factory, area, category_l1_id, issue_count}, ...],
    "top_3_overdue_loading":  [{supervisor_emp_no, region, overdue_count, open_count}, ...],
    "top_3_recurrence":       [{issue_id, recurrence_count, factory, area, category_l1_id}, ...],
    "gap_count_by_type":      {storage_room_type: count, ...},
  },
}
```

`kinds` 支持子集过滤（`["frequency", "gaps"]`）；不支持的 kind → `ValueError`。

## 4. Java 端契约（已对齐 CPS 波次 10 Java 侧）

| 端点 | 查询参数 | 返回 |
|---|---|---|
| `GET /api/cps/admin/coverage/frequency` | `start, end, factory?, area?, categoryL1Id?, page, size` | `{items, total_pages, page, size}` |
| `GET /api/cps/admin/coverage/region-supervisor` | `start, end, region?, page, size` | 同上 |
| `GET /api/cps/admin/coverage/recurrence` | `start, end, threshold?, page, size` | 同上 |
| `GET /api/cps/admin/coverage/gaps` | `start, end, factory?, area?, categoryL1Id?, page, size` | 同上 |

`CoverageClient` 走与 memory 模块一致的 `httpx.AsyncClient` + `iter_all_pages`
generator + 指数退避（默认 `(1.0, 2.0)`） + `JavaCoverageFetchError`。

## 5. 异常指标定义

| 异常 | 含义 | 用于运营决策 |
|---|---|---|
| `top_3_high_freq` | `issue_count` 最高的 3 个 (factory, area, category_l1_id) 桶 | 优先下放到现场 |
| `top_3_overdue_loading` | `overdue_count` 最高的 3 个主管 | 提醒上级关注 |
| `top_3_recurrence` | `recurrence_count` 最高的 3 个 issue | 复盘根因 |
| `gap_count_by_type` | 各 storage_room_type 的缺口计数 | 评估覆盖完整性 |

## 6. 与 FR-12 治理评估的留口

- `CoverageIngestionService.record_snapshot` 写入 `memory_metric_snapshot`，
  `metric_value` 结构 `{"snapshot": <CoverageSnapshot dict>, "kind": "coverage_snapshot"}`。
- 后续 FR-12 实现「效果评估」时，可读取 `memory_metric_snapshot` 中
  `metric_key LIKE 'coverage_analysis_%'` 的所有记录，与裁决效果（semantic 层）做
  时间维度关联评估。

## 7. API 路由

- `GET /api/v1/coverage/analyze`：
  - 必填：`periodStart`, `periodEnd`（ISO 8601）；
  - 可选：`kinds`（逗号分隔 subset）, `factory`, `area`, `categoryL1Id`；
  - 权限：`Identity.require("cps_admin")`；
  - 返回：完整 `CoverageSummary` JSON（200）/ 422（参数）/ 401/403（身份）。
- `POST /api/v1/coverage/snapshot`：
  - body：`{periodStart, periodEnd, factory?, area?, categoryL1Id?}`；
  - 行为：跑 `summary()` + `record_snapshot`；
  - 返回：snapshot JSON（201）/ 503（无 repository）/ 422 / 401/403。

## 8. 测试覆盖（36 例 ≥ 目标 25）

- `test_coverage_repository.py`（9）：upsert / metric_key idempotent /
  不同周期共存 / JSONB 互转 / period 过滤 / 缺失返回 / aggregate /
  build_metric_key / to_jsonb_safe。
- `test_coverage_application.py`（12）：四分析 / 排序 / top_k / filter /
  threshold / kinds 子集 / 未知 kinds → ValueError / record_snapshot 周期边界。
- `test_coverage_java_client.py`（5）：四端点 round-trip / iter_all_pages /
  retry-on-5xx-then-success / retry-exhausted-raises / query 参数序列化。
- `test_coverage_router.py`（10）：GET analyze round-trip / kinds subset /
  422 bad period / 422 unknown kinds / 403 forbidden / 401 unauthenticated /
  POST snapshot round-trip / 503 no-repo / 422 missing period / 403 forbidden。