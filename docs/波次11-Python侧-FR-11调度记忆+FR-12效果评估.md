# 波次 11 — Python 侧 FR-11 procedural 调度记忆 + FR-12 效果评估

> 本波次实现「程序/调度层（procedural）」与「治理评估层（effect）」两块补齐，
> 完成记忆三层的最后一层（episodic / semantic / procedural）+ 治理闭环的指标入口。
>
> FR-11（procedural）回答"系统在过去面对相似场景时**如何自我调度**"，
> FR-12（effect）回答"上次治理动作**是否真的让核心指标变好**"。

## 1. 架构决策

### 1.1 三层记忆的语义边界

| 层 | source_table | 数据源 | 检索方式 | 决策价值 |
|---|---|---|---|---|
| **episodic（事件层）** | `EVENT` | `cps_initial_review_event` 12 类事件 | 字段过滤 + 向量 | "发生过什么"（审计事实） |
| **semantic（语义层）** | `ADJUDICATION` | `cps_review_adjudication` 人工裁决 | 聚类 → `memory_skill_pattern` | "同类历史如何裁决"（AI 初审提示） |
| **procedural（程序/调度层）** | `PROCEDURAL` | Java 端 `/api/cps/admin/memory/dispatcher` | 三段加权评分 + top_k | "系统如何自我调度"（Agent 调度器直接消费） |

**procedural 与 semantic 的关键区别**：

- semantic 层的产出是 `memory_skill_pattern`，聚类的是「裁决偏好」——回答"AI 出过这条建议
  之后人类一般怎么改"；
- procedural 层的产出是 `dispatch_pattern`（本波新建表），记录的是「调度偏好」——回答
  "面对这个 (cat1, area, decision, ai_relation) 组合，历史上系统选了什么策略"；
- procedural 不走 embedding 相似检索：调度是离散枚举 + 字段精确匹配，引入向量会引入
  不可解释的"接近但错配"问题；改用三段加权可解释评分。

**procedural 不复用 episodic/semantic 的 source_table 枚举值**：本波为
`app/skill/memory/domain/enums.py` 添加 `PROCEDURAL` 值（注释已在波次 9 留位），
不再复用 `EVENT/ADJUDICATION/ISSUE`，避免未来调度数据量增长后稀释那两个枚举的语义。

### 1.2 procedural 评分算法（三段加权）

`retrieve_dispatch_patterns(scenario, top_k)` 采用可解释的**三段加权评分**：

```
weights = {
  category_l1_id: 0.34,   # 一级分类：最强语义锚点
  area:           0.33,   # 区域：地理隔离让 area 错误代价极高
  decision:       0.33,   # 决策方向：接受/驳回/补件
}

score(pattern, scenario) =
  sum(weights[k] for k in (pattern, scenario) if scenario.k is not None
       and pattern.k == scenario.k)
  /
  sum(weights[k] for k in EFFECT_METRIC_KEYS  if scenario.k is not None)
```

**为什么这样设计**：

- 三段权重和 = 1.0，输出天然落在 [0, 1]，直接当 `scenario_similarity_score`；
- 归一化分母只算 query 中**非 None 的段**——避免 query 只给 cat1 时被 area/decision 的
  默认 0 分稀释到 0.34；
- 字段不匹配的段不进分子也不进分母（即所谓 "剩余归一化"），保证语义可解释；
- 工厂/二级分类**不进权重表**——它们通常是变化的标识符（同一问题可能多工厂流转），
  仅作为辅助过滤条件保留在 `DispatchQuery`，不参与 score。

**与 FR-10 覆盖率 top_k 评分对齐**：都是"先拉全集 → 评分 → 排序 → top_k"，
不引入 HNSW / ivfflat 这类黑盒索引。

### 1.3 procedural 合并（consolidate）策略

`consolidate_patterns(since_days=7)` 扫描过去 7 天的 patterns，按
`(category_l1_id, area, decision)` 三元组合并：

```
source_id = uuid5(NAMESPACE_OID, "<cat1>|<area>|<decision>")
```

- 三元组相同的 pattern **共享同一个 source_id**——`_source_id_for` 是 uuid5 确定性函数，
  跨调用、跨进程稳定，保证 consolidate 是幂等的；
- 合并时 `sample_count` 累加、`sample_reasons` 去重并截断到 5 条、`last_at` 取 max；
- 合并的副作用：scoring 时如果两批 pattern 在 7 天内被重复上报（典型场景：多个 agent
  独立决策落库），consolidate 把它们"折叠"成一条，避免检索时 top_k 被同质 pattern 撑满。

### 1.4 effect 评估的口径（FR-12）

四个核心 metric + 三档阈值：

| metric_key | 含义 | regression 阈值 |
|---|---|---|
| `ai_pass_rate` | AI 初审通过率 | **下降 > 5%** 触发 regression |
| `human_override_rate` | 人工覆盖 AI 决策的比例 | **上升 > 10%** 触发 regression |
| `recurrence_rate_30d` | 30 天内同类问题复发率 | **上升 > 20%** 触发 regression |
| `coverage_gap_count` | 覆盖率缺口数量 | 上升不回退 regression（仅监控） |

**为什么阈值不对称**：AI 通过率每掉 1pp 都是质量问题（漏检代价高），所以容忍下限是 5%；
人工覆盖是"AI 不可靠"的体现，容忍度更高（10%）因为它是合理反馈机制；
30 天复发率基线波动大，给 20% 容差避免偶发震荡触发误报。

`compare_periods` 输出 `EffectComparison`（含 baseline + current + delta_pct + significance）；
`detect_regression` 是 `compare_periods` 的特例——只输出被标记为 `regression` 的
metric_key 列表，方便上层做"是否告警"决策。

### 1.5 与 AI 初审 / 裁决的接续

- **AI 初审**：semantic 层 `memory_skill_pattern` 给 AI 提供"同类历史裁决偏好"；
  procedural 层 `retrieve_dispatch_patterns` 给 Agent 调度器提供"系统面对这种场景
  一般选什么策略"——两层一起作为 AI 决策上下文。
- **裁决接续**：`consolidate_patterns` 周期性运行后，下游裁决系统在新裁决落库时
  可以基于合并后的稳定 source_id 做增量合并，不会出现"同一裁决两条 pattern"。
- **运营治理**：`detect_regression` 输出 regression metric_key 列表 → 触发
  `cps-adjudication` 端回查 → 必要时把样本回灌给 semantic 层重训 pattern，
  形成"评估 → 发现回退 → 重训 → 再评估"的闭环。

## 2. 接口契约

### 2.1 FR-11 procedural 三端点（`/api/v1/procedural/...`）

| Method | Path | Body | 返回 | 角色 |
|---|---|---|---|---|
| POST | `/procedural/retrieve` | `{categoryL1Id, categoryL2Id?, factory?, area?, decision?, topK?}` | `list[DispatchHint]` | `cps_admin` |
| POST | `/procedural/record` | `{pattern_id?, categoryL1Id, ...}` | `{pattern_id, sampleCount, ...}` (202) | `cps_admin` |
| POST | `/procedural/consolidate` | `{sinceDays?}` | `{consolidatedGroups, mergedCount}` | `cps_admin` |

`DispatchHint.decision_recommendation` 取自 pattern.decision 的高水位——
即该 pattern 历史 sample 中出现频率最高的决策方向；无历史时回退到 pattern.decision。

### 2.2 FR-12 effect 三端点（`/api/v1/effect/...`）

| Method | Path | Body / Query | 返回 | 角色 |
|---|---|---|---|---|
| POST | `/effect/snapshot` | `{periodStart, periodEnd, scopeKey?}` | `{metricKey, recordedAt}` (202) | `cps_admin` |
| GET | `/effect/compare` | `?baselineStart=&baselineEnd=&currentStart=&currentEnd=&scopeKey?` | `list[EffectComparison]` | `cps_admin` |
| GET | `/effect/regression` | `?periodStart=&periodEnd=&scopeKey?` | `list[str]`（regression metric_key） | `cps_admin` |

所有日期参数用 ISO-8601；非法 ISO 返回 422（沿用 FR-10 的 `_validate_iso` 模式）。

## 3. 持久化

### 3.1 `dispatch_pattern` 表（FR-11 新增）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGSERIAL (SQLite: INTEGER) | 自增主键 |
| `source_table` | VARCHAR(32) | 常量 `'PROCEDURAL'` |
| `source_id` | BIGINT | `uuid5(NAMESPACE_OID, "<cat1>\|<area>\|<decision>")` → int |
| `category_l1_id` | INT | 一级分类 |
| `category_l2_id` | INT NULL | 二级分类 |
| `factory` | VARCHAR(64) NULL | 工厂标识 |
| `area` | VARCHAR(64) NULL | 区域 |
| `decision` | VARCHAR(32) | 决策方向 |
| `ai_relation` | VARCHAR(32) | AI 关联（accept / override / escalate） |
| `sample_count` | INT NOT NULL DEFAULT 1 | 累计样本数 |
| `last_at` | TIMESTAMPTZ | 最近一次上报时间 |
| `sample_reasons` | JSONB (TEXT on SQLite) | 最近 5 条 reason，去重 |
| `tags` | TEXT[] NULL | 含 `'procedural'`、`'procedural:<source_id>'`、`'factory:<f>'` |
| `payload` | JSONB | 原始 pattern 详情 |
| `is_active` | BOOLEAN | 是否有效 |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

`UNIQUE(source_table, source_id)` 保证幂等 upsert（与 episodic/semantic 一致）。

### 3.2 `memory_metric_snapshot` 表复用（FR-12）

直接复用波次 10 的 `MemoryMetricSnapshotORM`（`memory_metric_snapshot` 表），
无新增 schema。

```
metric_key = effect_evaluation_<start>_<end>_<scope_key>
metric_value = {
  "ai_pass_rate": ...,
  "human_override_rate": ...,
  "recurrence_rate_30d": ...,
  "coverage_gap_count": ...,
  "period_start": ...,
  "period_end": ...,
  "scope_key": ...,
  "kind": "effect_snapshot",
}
```

## 4. 测试覆盖（30 个新增）

| 文件 | 测试数 | 关注点 |
|---|---|---|
| `tests/skill/test_procedural_memory_repository.py` | 4 | upsert 幂等 / 三元组合并 / reason 去重截断 / 列表过滤 |
| `tests/skill/test_procedural_memory_application.py` | 5 | 三段加权评分 / 剩余归一化 / top_k 截断 / 上报 → upsert / consolidate 合并 |
| `tests/skill/test_procedural_memory_router.py` | 7 | 三端点 round-trip / 401 / 403 / 422 |
| `tests/skill/test_effect_evaluation_repository.py` | 3 | snapshot upsert / 同 metric_key 幂等 / scope 过滤 |
| `tests/skill/test_effect_evaluation_application.py` | 5 | 上报落库 / 无数据兜底 / 比较返回 significance / baseline 缺失 422 / 回归检测 |
| `tests/skill/test_effect_evaluation_router.py` | 6 | 三端点 round-trip / 非法 ISO 422 / 403 |

**全套结果**：`634 passed, 2 failed (pre-existing), 1 skipped`
（基线 604p + 28 个本波 + 2 个上一波未列），
ruff 与基线持平（21 errors，与任务书一致，无新增）。
