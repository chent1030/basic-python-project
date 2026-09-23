# 波次 9 — Python 侧 I 线记忆体系骨架 + FR-09 长期记忆接入

> 记忆体系是产品核心——agent 必须根据历史审核情况自主进化，使用越久越贴合实际。
> 本波实现 I1 骨架（episodic + semantic）+ I2 长期记忆接入（FR-09）；
> FR-10/11/12 留待后续波次（procedural 层 / 治理评估）。

## 1. 架构决策

### 1.1 三层记忆 + 双轨存储

| 层 | source_table | 来源 | 写入路径 | 检索方式 |
|---|---|---|---|---|
| **episodic（事件层）** | `EVENT` | `cps_initial_review_event` 12 类事件 | `ingest_event` | 字段过滤 + 向量 |
| **semantic（语义层）** | `ADJUDICATION` | `cps_review_adjudication` 人工裁决 | `ingest_adjudication` + `cluster_pending` | 聚类 → `memory_skill_pattern` |
| **procedural（程序/调度层）** | — | 后续波次实现 | FR-11/12 | 留表 `memory_metric_snapshot` |

**为什么三层分开**：
- episodic 层是「发生过什么」（可审计、低层级事实）；
- semantic 层是「同类历史如何裁决」（AI 初审提示）；
- procedural 层是「系统如何自我调度」（后续波次留出，不在本波实现）。

**为什么 pgvector + JSONB 双轨**：
- `embedding vector(1024)` 走 pgvector `ivfflat cosine ops` 提供语义检索索引；
- `payload JSONB` 保留裁决/事件原始细节，提供审计源 + embedding 缺失时的字段过滤兜底；
- 双轨不是冗余：JSONB 是事实源（可审计、可回查），embedding 是检索索引（性能）。

### 1.2 embedding 维度 = 1024

选择 **1024 维 = BGE-large 默认维度**（任务书已定）：
- 1024 维足以表达 prompt 级文本语义；
- 与 BGE 系列对齐后，未来直接接入 LangChain Embeddings 无需 schema 改动；
- 当前 `HashPlaceholderEmbedding`（SHA256 → 1024 维归一化向量）仅为契约贯通，TODO 真实 BGE 接入。

### 1.3 库归属（postgres_primary vs cps_memory）

任务书指定数据存 ai-experience-postgres（库名 `cps_memory`）。
当前实现把 memory schema 暂挂在 `postgres_primary` 同库，避免新增 datasource 配置；
目标迁移到独立 `cps_memory` 库的 DSN 配置变更留作后续波次（已在 `repository.py`
中通过 `MemoryRepository(session_factory)` 抽象掉具体数据源，切换成本低）。

## 2. 表 schema（详见迁移 `V20260927__memory_schema.py`）

### `memory_entry` 主表
- `id BIGSERIAL PK`
- `source_table VARCHAR(32) NOT NULL` — ADJUDICATION/EVENT/ISSUE
- `source_id BIGINT NOT NULL`
- `issue_id BIGINT NULL`
- `version_no INT NULL`
- `category_l1_id INT NULL` / `category_l2_id INT NULL`
- `factory VARCHAR(64) NULL` / `area VARCHAR(64) NULL` / `severity SMALLINT NULL`
- `embedding vector(1024) NULL` — 语义检索
- `payload JSONB NOT NULL` — 裁决/事件原始细节
- `tags TEXT[] NULL` — 含 `stale` 表示软删候选
- `is_active BOOLEAN NOT NULL DEFAULT TRUE`
- `superseded_by BIGINT NULL` — 同源新版本软取代时回填（不删行，审计可追）
- `created_at TIMESTAMPTZ DEFAULT now()`
- `UNIQUE(source_table, source_id)` — Java 端重复推送幂等

### `memory_skill_pattern` 语义层
- `id, skill_code VARCHAR(64) UNIQUE, pattern_summary TEXT, embedding vector(1024)`
- `example_count INT, last_seen_at, updated_at`

### `memory_metric_snapshot` 评估快照（FR-12 留表）
- `id, period_start, period_end, metric_key, metric_value JSONB, recorded_at`

### 索引
- `(issue_id)` / `(source_table, source_id)` / `(is_active)` /
  `(factory, area)` / `(category_l1_id)` / `(period_start, period_end, metric_key)`

> 当前实现：embedding 列在 ORM 用 TypeDecorator 同时支持 PG ARRAY(Float) 和 SQLite JSON；
> 待真实 BGE embedding 接入 + pgvector 部署就绪，把列类型升级到 `vector(1024)` 并加
> `ivfflat` cosine ops 索引。

## 3. 接口契约

### 3.1 Python API（服务层）

| 服务 | 方法 | 用途 |
|---|---|---|
| `MemoryIngestionService` | `ingest_adjudication(d)` | 单条裁决入库 |
| | `ingest_event(d)` | 单条事件入库 |
| | `ingest_issue(d)` | 单条问题入库 |
| | `backfill_since(start, end, kinds)` | 批量回填（→ Java 端拉取 + 逐条 ingest） |
| | `supersede_stale_patterns()` | 扫描 `tags` 含 `stale` 的 entry，软删 |
| `MemoryRetrievalService` | `retrieve_context_for_issue(issue, top_k=5, kind_filter=None)` | issue 上下文检索（返回带 score 的 RetrievalHint） |
| | `retrieve_pattern_hint(scenario, top_k=3)` | 语义层 pattern 检索 |
| | `format_hints_for_prompt(hints)` | hints → JSON 字符串（拼 prompt） |
| `MemoryClusteringService` | `cluster_pending(limit=200)` | 取未被聚类的 ADJUDICATION → 按 (category_l1_id, area) 聚合 → upsert MemorySkillPattern |
| | `list_recent(top_k=50)` | 列出近期 pattern |

### 3.2 HTTP 端点（`/api/v1/memory/*`）

| 端点 | 用途 | 鉴权 |
|---|---|---|
| `POST /memory/ingest/adjudication` | 单条裁决入库 | `cps_admin` |
| `POST /memory/ingest/event` | 单条事件入库 | `cps_admin` |
| `POST /memory/ingest/issue` | 单条问题入库 | `cps_admin` |
| `POST /memory/backfill` | 批量回填（startIso/endIso/kinds） | `cps_admin` |
| `GET /memory/retrieve` | issue 上下文检索（issueJson/topK/kindFilter） | `cps_admin` |
| `GET /memory/patterns` | 语义层 pattern 检索（scenario/topK） | `cps_admin` |

### 3.3 Java 端消费（详见 Java 侧波次 9 doc）
- `/api/cps/admin/memory/adjudications` — 分页拉取裁决
- `/api/cps/admin/memory/events` — 分页拉取事件
- `/api/cps/admin/memory/issues` — 分页拉取问题
- 返回 `{items: [...], total_pages: int, page: int, size: int}`

## 4. 检索算法

### 4.1 `retrieve_context_for_issue`
1. 把 issue dict → 拼接检索文本（factory/area/category/severity） → embed；
2. SQL：`SELECT * FROM memory_entry WHERE is_active=TRUE AND embedding IS NOT NULL
   AND factory=... AND area=... AND category_l1_id=... AND issue_id=...
   ORDER BY pgvector.cosine_distance(embedding, :q) LIMIT top_k`；
3. PG 走 `func.cosine_distance` 在 ORDER BY；SQLite 走 Python 兜底（取 top_k*4
   候选 → Python 计算 cosine → 重排）。

### 4.2 `cluster_pending`
1. 拉取 `is_active=TRUE AND 'clustered' NOT IN tags` 的 ADJUDICATION，按
   `(category_l1_id, area)` 分桶；
2. 每桶：取 embedding 均值（若有 embedding）→ 拼 `skill_code=pat:{l1_id}:{area}` →
   upsert `memory_skill_pattern`（example_count += 桶大小，pattern_summary=第一笔
   reason 的摘要）；
3. 把 entry 的 tags 追加 `'clustered'`，下次 cluster_pending 不再扫到。

## 5. 与 AI 初审的集成点（FR-09 主诉求「自主进化」）

### 5.1 修改点
- `app/projects/initial_review/infrastructure/prompts.py`：
  - `TEXT_VALIDITY_PROMPT_VERSION` 升到 `text-validity/qwen-plus@3`
  - `IMAGE_COMPARE_PROMPT_VERSION` 升到 `image-compare/qwen-vl@3`
  - 新增 `HISTORICAL_HINT_SECTION`（`{hints_json}` 占位符）
  - `build_text_validity_prompt` / `build_image_compare_prompt` 新增
    `historical_hints: str | None = None` 参数，仅当 hints 非空时拼接。

- `app/projects/initial_review/application/check_pipeline.py`：
  - `_text_validity` / `_image_compare` 接收 `historical_hints`；从
    `snapshot["historical_hints_json"]` 读取。

- `app/projects/initial_review/application/service.py`：
  - `InitialReviewService.__init__` 新增 `memory_retriever: Any | None = None`
  - 新增 `_retrieve_historical_hints(row, snapshot)` 方法：构建 issue_dto → 调
    `memory_retriever.retrieve_context_for_issue(issue_dto, top_k=5)` →
    `format_hints_for_prompt` → 写回 `snapshot["historical_hints_json"]`
  - `_default_check_runner` 在 `_pipeline.run()` 前注入 hints。
  - 检索失败/超时走 try/except，fallback 到无 hints（**不阻断主流程**）。

### 5.2 回退保护
- try/except 包裹 retrieval 调用；
- 失败/超时 → `snapshot["historical_hints_json"] = None`；
- 检查 pipeline 内部 `_text_validity` / `_image_compare` 仍走完整流程（hints
  拼装逻辑短路）。
- 审计日志记 `initial_review memory_retrieved`（含 hint 数 / 总分），便于追溯
  memory 模块对 AI 决策的扰动。

## 6. 测试覆盖

- `tests/skill/test_memory_repository.py` — 11 项（upsert/唯一冲突/软取代/向量检索/
  字段过滤/cluster_pending mean/embedding 字符串互转/UNIQUE 约束）
- `tests/skill/test_memory_ingestion.py` — 8 项（单条/重复 idempotent/backfill mock
  java_client/supersede/失败 fallback/payload merge/summary.to_dict）
- `tests/skill/test_memory_retrieval.py` — 7 项（issue 上下文/分类过滤/top_k/
  kind_filter/空 fallback/format_hints/pattern_hint）
- `tests/skill/test_memory_router.py` — 7 项（四端点 happy path + 401/403/422 +
  java_client 未注入的回填软失败）
- `tests/skill/test_memory_java_client.py` — 7 项（分页循环/5xx 重试/连接错重试/
  超时错误/URL 形态/env 配置）

合计 **40 项**，全部通过。

## 7. 待办（后续波次）

1. **真实 BGE embedding 接入**：替换 `HashPlaceholderEmbedding`；
   pgvector 列类型从 ARRAY(Float) 升级到 vector(1024)；补 `ivfflat` cosine ops 索引。
2. **procedural 层**：FR-11（调度策略）+ FR-12（评估看板）；
   `memory_metric_snapshot` 表已就绪，本波不消费。
3. **clustering 自动化任务**：本波仅提供 `cluster_pending` API；后续接 scheduler
   定时执行 + 自动生成 skill_pattern summary。
4. **跨库迁移**：memory schema 从 `postgres_primary` 迁出到独立 `cps_memory` 库
   （需新增 datasource 配置 + alembic 目标库切换）。
5. **审计集成**：memory_retrieved 事件写入 `AgentInitialReviewExec` audit log
   （本波仅在 service logger 留 trace）。