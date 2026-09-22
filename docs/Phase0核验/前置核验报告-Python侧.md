# Phase 0 前置核验报告 — Python 侧与集成环境

> 任务对应：开发计划 T0.2（Python 侧）/ T0.3（附件）/ T0.4（mock 排查）/ T0.5（静态核查 Python 部分）
> 核验日期：开发启动日（Phase 0）
> 核验方式：只读代码审查 + 对本机运行服务的只读 HTTP 探测。未修改任何业务代码、未 git commit。
> 证据格式：`路径:行号`（均相对 `/Users/csai/project/cps-agent/basic-project`）。

---

## ① 结论摘要

**总判断：可以立即启动 A5（文本规则引擎）开发；C1（周报 Skill）可启动但首日需先补齐两项工程决策（PG 表迁移 + object 存储客户端选型）。**

1. **七对象无一落 PG 表**：PRD §11 七对象全部以"原型级 SQLite JSON 文档"形态存在于 harness 内核（`app/harness/kernel/infrastructure/sqlite.py:13-104` 的 `framework_records` 表，kind 区分），alembic 迁移链只有 1 个 head（`migrations/versions/9d35bb4a1977_create_items_table.py`，仅 `items` 表）。这不是缺陷（巡检运行态按计划留在 Python 侧），但计划新增的 PG 表（`weekly_report_run`、`agent_initial_review_exec`、`cps_initial_review_item` 等）全部未建，属 A/C 线开工前置项。
2. **C-01~C-08 在 Python 侧全部缺失，仅 C-10 已满足**：`app/api/` 下 grep `rectification|room.check|speech|weekly|inspection.plan` 零命中；既有 `POST /cps/inspections`（`app/api/v1/endpoints/cps.py:65`）+ `/start`（`cps.py:120`，202）即 C-10，保留即可。
3. **附件现状 = base64 全量内嵌 SQLite**：证据图片经 `validate_image`（PNG/JPEG/WEBP、25MP、decompression bomb 防护，`app/projects/cps/infrastructure/evidence.py:14-42`）后整段 base64 + 向量存入 `cps_evidence` 记录（`app/projects/cps/application/service.py:161-172`）。Python 侧无任何 S3/RustFS 客户端代码、无 boto3/minio 依赖（`pyproject.toml` dependencies 无）、`docker-compose.cps.yml` 的 agent 不依赖 rustfs → object_key 两步演进 Python 侧 100% 绿地。
4. **模型调用无 stub，全真实链路**：`config/agents.yaml` 配置 qwen（dashscope 兼容端点），6 个专家 Agent + main + observation 均有 prompt（`app/projects/cps/prompts/*.md`）与输出 schema 校验（`domain/contracts.py`）。**但 `config/agents.yaml:8` 明文提交了真实 API key（sk-e36a…），建议立即轮换并迁移 local.yaml（该文件在 .gitignore 外）**。
5. **`skills/` 目录仅 `.keep`**：C1 报告 Skill 从零建；可复用的是 deepagents[quickjs]==0.7.13（`pyproject.toml:35`）自带 skills 机制与 `app/projects/cps/prompts/` 的 prompt 注入模式（`app/projects/cps/infrastructure/agents.py:20-50` `prompt_for()`）。
6. **集成环境全部存活（只读探测）**：RustFS `http://127.0.0.1:9000/health`→200；embedding `:8090/health`→200；vector `:8008/health`→200；FastAPI `http://127.0.0.1:8000/api/v1/cps/agents`→200；Java `:8080` TCP 通（`/` 返回 404 属正常，根路径无映射）。
7. **vector-service(8008) 已部署但应用零调用**：相似图片检索是进程内 cosine（`service.py:207-226`），向量内嵌 SQLite 记录。8008 的 `/v1/vector/{ensure,load,upsert,search}`（`infra/vector-service/main.py:42-67`）是现成扩展点，附件演进时可评估接管。

---

## ② 七对象落表核查表

**存储基底**：harness 内核 SQLite（`.agent-data/state.sqlite`，WAL，`PRAGMA user_version=1`），两表 `framework_records(tenant,kind,id,body)` + `framework_events(cursor,tenant,run,kind,body,created)`（`app/harness/kernel/infrastructure/sqlite.py:13-104`）。ORM 层（`app/models/__init__.py:1-10`）仅导出 `Item` + `ehs_contruct_item`，与七对象无关。

| PRD §11 对象 | 现状 | 现有对应物（代码证据） | PRD 字段缺口 |
|---|---|---|---|
| InspectionCase | **部分（原型级）** | `cps_case` kind：创建 `service.py:79-97`（case_id=digest([actor,idempotency_key])，幂等）；contract `app/projects/cps/domain/models.py:125-162`（status: collecting/active/needs_input/needs_human/completed/cancelled，version，evidence_version） | `thread_id` 无（以 jobs/runs 结构承载）；`area`/`supervisor` 折在 `line_info` 内非独立字段；状态枚举与 PRD 后续新增的整改/周报状态未对齐 |
| AgentDispatch | **部分（原型级）** | `cps_job` kind：`service.py:275-315`（id/agent/snapshot/evidence_version/run_id/applied/review）；调度确认卡 `app/projects/cps/application/reviews.py:51-105`（approve/modify/skip/retry/return/manual 六动作，角色校验） | `recommended_agent` 与 `selected_agent`（modify 后）未分列存储（job.agent 单字段+review 动作可推导）；`recommendation_reason` 在 review payload 内非一等字段 |
| AgentRun | **部分（框架级）** | `run` kind：`app/harness/kernel/application/runtime.py:308-335`（status/tokens_used/error_type/invocation_count/model_snapshots/policy/capabilities）；`model_snapshots` 来自 `engine.snapshot()`（`runtime.py:346-360`；deepagents 实现 `kernel/infrastructure/deepagents.py:42`） | **`model_version` 无**（R-15 落点：model_snapshots 近似但不等价，需标准化版本串）；`confidence`、`duration_ms` 无；`dispatch_id` 经 job.run_id 间接关联 |
| HumanConfirmation | **部分（三层机制）** | ① case.confirmations[key]={actor,confirmed_at,reason,evidence_version,fingerprint}：`reviews.py:173-203`；② 框架 approval kind + `runtime.approve`（`reviews.py:145-171`）；③ job.review 调度卡：`reviews.py:51-105`。依赖失效矩阵：`reviews.py`（issues→rect/report/work_plan；report→work_plan） | `confirmation_id` 无独立 ID（key=阶段名）；`stage`/`original_payload`/`edited_payload` 部分隐含在 review 与 fingerprint 中，未按 PRD 字段显式化 |
| MemoryCandidate | **部分（原型级）** | `memory` kind state=pending：观察 Agent 结果逐条 propose（`reviews.py:289-300`）；调度记忆 `_dispatch_memory`（`reviews.py:231-259`，key=cps-dispatch-{job_id}） | `confidence` 无；`source_events` 以 source_run 近似 |
| LongTermMemory | **部分（原型级）** | `memory` kind state=accepted + `memory_history` 版本：审核/启停/回滚 `app/projects/cps/application/knowledge.py:197-264`（rollback 恢复 content/state/expires；enable/disable 状态机；每次写 history） | `rollback_version` 未作一等字段（由 history 记录可推导）；`reviewer`/`reviewed_at` 在 history body 内非列 |
| Report | **部分（原型级）** | `cps_archive` kind：`knowledge.py:30-67`（archive_id=digest([case_id,confirmations['report']]) 幂等；html 整体内嵌）；HTML 预览端点 `GET /cps/inspections/{id}/report.html`（`cps.py:171-183`，带 X-CPS-Confirmed 头） | **无 `weekly_run_id` 类关联**（周报概念未落地：`app/api/` grep `weekly` 零命中）；`html_ref` 为内嵌非引用；`archive_status` 无状态机（存在即归档）；draft/confirmed 双数据由 results+confirmations 承载 |

**结论**：七对象=「已实现业务语义、未按 PRD §11 落表」。按计划口径（Java=SoR，Python 侧运行态不强制 PG 化）这属于**可接受现状**；但 PRD 字段级对齐（model_version、confidence、duration_ms、confirmation_id、weekly_run_id）与计划新增 PG 表是 A/C 线开工内容。

---

## ③ 契约缺口表（C-01~C-12，Python 侧逐条）

| # | 契约 | Python 侧现状 | 缺口 | 建议落点 |
|---|---|---|---|---|
| C-01 | `POST /api/agent/rectifications`（Java→Python，202+review_task_ref，幂等 `cps-rectify-{issueId}-v{n}`） | **缺失**（`app/api/` grep `rectification` 零命中；仅 `app/projects/cps/prompts/rectification_judgement.md` 是既有整改判定 Agent 的 prompt） | 端点、幂等提交记录、初审执行记录（`agent_initial_review_exec`）、AI 初审编排（规则先行→视觉/语义并行）全无 | 新建 `app/api/v1/endpoints/agent_callbacks.py`（或 `initial_review.py`）+ `app/projects/initial_review/` 新模块；幂等参照 `cps_command` 指纹模式（`service.py:61-77`，重放异参→409 Conflict） |
| C-02 | `POST /api/callbacks/initial-review/result` 回调 Java（Python→Java） | **缺失**。全 `app/` 无 callback 代码；`config/config.yaml` 的 `doc_review.callback_timeout: 30.0` 是**死配置**（无消费者，grep `callback` 于 `app/**/*.py` 零命中）。8080 仅在 `app/core/logging_config.py:4` 作为日志格式示例出现 | 回调客户端、Java 地址配置、is_late 迟到留痕语义、技术重试≤2（30s/60s 退避，不重置 10min 计时）全无 | 复用 `app/services/http_client.py`（已有多 provider 配置与连接池）；config 新增 `cps.java_callback` 段；重试语义参照 kernel Provider `max_retries`（`app/harness/kernel/infrastructure/models.py:19`）但需自定义退避序列 |
| C-03 | `GET /api/agent/rectifications/{ref}` 状态查询（Java 兜底轮询，三态 RUNNING/FAILED/超时由 Java 判定） | **缺失**。框架级有 `GET /agent-runs/{run_id}`（`app/api/v1/endpoints/agent_runs.py:187`）与 SSE 事件流（:255），但语义是框架 run 查询，不是按 review_task_ref 的初审任务查询 | 按 ref 定位初审执行记录的状态端点 | 随 C-01 同文件实现；状态源=新增 `agent_initial_review_exec` 记录 |
| C-04 | `POST /api/agent/room-checks/judge`（TYPE_MISMATCH/JUDGED(PASS\|FAIL)/UNJUDGEABLE，同步） | **缺失**（grep `room.check` 零命中） | 端点、图片类型/清晰度判定链路、同步超时控制全无 | 新 router；视觉判定可复用 `cps_vision` 模型配置（`config/agents.yaml:13-18` Qwen2.5-VL）与 `validate_image`（`evidence.py:14-42`）；同步调用需独立于 harness 异步 run 体系（内嵌 asyncio task） |
| C-05 | 周报运行记录查询（按类型/周期/状态过滤）+ 受控下载 | **缺失**（grep `weekly` 于 `app/api/` 零命中） | `weekly_report_run` 表（未建）、查询端点、按 object_key 取流全无 | 新 `app/api/v1/endpoints/weekly_reports.py`；下载取流参照现有 `report.html` 端点模式（`cps.py:171-183`）改为对象存储读 |
| C-06 | `POST /api/agent/speech-to-text`（request_id 幂等，转写仅填表单） | **缺失**（grep `speech` 零命中；后端文档 §2.3 的 speech_transcript 表为可选） | ASR 选型（D-03 未决）、端点、音频对象存取全无 | 新 router；ASR 选型是前置决策，阻塞项标注 D-03 |
| C-07 | `POST /api/agent/inspection-plans/draft`（幂等 `plan-draft-{sourceRunId}`） | **部分基础已备**：`work_plan` 专家 Agent 已有（`domain/models.py:10-17` SPECIALISTS + `prompts/work_plan.md` + CONTRACTS DAG 无环校验 `service.py:428-503`）；`publish_plan` 已实现幂等（plan_id=digest，逐条建 `cps_task`，`knowledge.py:69-145`） | "从 sourceRunId 起草"的端点与幂等键不存在；现有发布走 `/cps/inspections/{id}/work-plan/publish`（`cps.py:193`）是另一条链路 | 新端点落在 C-01 同文件；复用 work_plan Agent 的 CONTRACTS 校验；幂等复用 digest 模式 |
| C-08 | 管理端周报下载代理（Java 侧转发） | Python 侧= C-05 的取流端点，同上缺失 | 同 C-05 | 同 C-05 |
| C-09 | 计划批准建单（Java 内部事务） | **N/A（Java 侧）** | — | — |
| C-10 | 既有 `POST /cps/inspections` + `/start` 保留 | **已满足**：`cps.py:65`（create,201）/`cps.py:120-123`（start,202→service.start） | 无（保持兼容即可） | 无 |
| C-11/C-12 | 移动端问题创建/详情 API 改造 | **N/A（mobile→Java）** | — | — |

---

## ④ 附件通道现状与演进落点

### 现状（全链路证据）

1. **入口**：`POST /cps/inspections/{id}/evidence`（`cps.py:99-103`）→ `service.add_evidence`（`service.py:114-185`）：`EvidenceInput.content_base64`（4~14,000,000 字符 ≈10MB，`domain/models.py:98-103`）→ `validate_image`（base64 严格解码/PNG·JPEG·WEBP/25MP/DecompressionBomb，`infrastructure/evidence.py:14-42`）→ `HTTPImageEncoder.encode(data_url)` POST `{model, image}` 到 `embeddings.url`（`evidence.py:44-69`）→ `cps_evidence` 记录存 metadata+base64+vector+embedding_model（`service.py:161-172`）。
2. **读取**：`GET /cps/inspections/{id}/evidence/{evidence_id}`（`cps.py:106-117`）把存储的 base64 解码为 image bytes 返回。
3. **相似图检索**：进程内 cosine，逐条取 vector 两两比较（`service.py:207-226`，memory_limit=5）——**未用 vector-service**。
4. **配置**：`config/cps.yaml`（embeddings.url=127.0.0.1:8090/image-embeddings，model siglip2-so400m-patch14-384，max_dimensions 8192；max_image_bytes 10000000；internal_trust+trusted_networks 回环）；docker 版 `config/cps.docker.yaml`（url=http://embedding:8090，trusted 172.16/12）。
5. **部署**：`docker-compose.cps.yml` = agent(8000→18555) + embedding(8090, siglip 模型卷挂载) + vector(8008, vector-data 卷)；**RustFS 单独在 `docker-compose.rustfs.yml`**（9000/9001，`RUSTFS_ACCESS_KEY` 默认 admin，SECRET 默认 Ct0520.0402，healthcheck wget /health，网络 basic-project_cps）。agent 服务 depends_on 不含 rustfs。
6. **探测结果（只读）**：9000/health→200；8090/health→200；8008/health→200。RustFS S3 API 未做签名请求探测（无客户端依赖），标"待运行验证"。

### object_key 两步演进在 Python 侧的落点

- **依赖与配置**：pyproject 增 S3 兼容客户端（boto3 或轻量 minio）；config 新增 `storage` 段（endpoint/access_key/secret/bucket，参照 RustFS compose 变量）。
- **写入分支**：`service.add_evidence`（`service.py:114-185`）改造为：新上传 → validate 后 PUT RustFS（对象名按后端设计 §4：`cps-issues/{issueId}/before|after/{attId}.{ext}` 等）→ `cps_evidence` 记录存 object_key（保留向量计算路径）；存量 base64 模式保留（联调双模式）。
- **读取分支**：`cps.py:106-117` 证据端点与 C-05 周报下载改为按 object_key 从 RustFS 取流（受控引用，不生成公开链接——后端设计 §4:217）。
- **一致性**：运行记录↔object_key 对账 job（后端设计 §4:218）落在 `app/tasks/`（@scheduled 机制现成，见 ⑦）。
- **风险提示**：`cps_evidence` 的 base64 内嵌使 `.agent-data/state.sqlite` 随图片数线性膨胀（现 24KB，m00098 实测）；向量亦内嵌——附件演进时一并评估将 vector 检索切到 8008（`infra/vector-service/main.py:42-67` 已有 upsert/search API，零代码接入是现成收益）。

---

## ⑤ mock/占位清单

| # | 位置 | 性质 | 处置建议 |
|---|---|---|---|
| 1 | `app/api/v1/endpoints/auth.py:30-33` | **占位**：demo 登录不做密码校验直接发 token（`TODO: verify_password`）；`auth.enabled` 默认 false（`config/config.yaml` auth 段） | P10 联调前必须替换或确认 internal_trust 方案；C-01~C-08 的 Java→Python 调用鉴权口径依赖它 |
| 2 | `app/tasks/demo_tasks.py:23` | **示例代码**：daily_report TODO 真实逻辑；heartbeat/sync_external_data 均示例 | 不阻塞；其 `@scheduled(cron=...)` 写法即 C-05 周报定时任务的模板 |
| 3 | `skills/` 仅 `.keep` | **空目录** | C1 起点：从零建周报 Skill；复用 deepagents skills 机制 + `app/projects/cps/prompts/` 的注入模式（`infrastructure/agents.py:20-50`） |
| 4 | `prompts/`（顶层）joke.txt / summarize.j2 / translate.yaml | demo 模板 | 无影响 |
| 5 | `tests/cps/conftest.py:52-` ScriptedEngine | 测试替身（合法，非生产 mock） | 保留；C1/A5 新测试沿用此模式（不依赖真实 LLM） |
| 6 | `app/harness/backends/base.py:24` NotImplementedError | 抽象方法（合法） | 无需处理 |
| 7 | `config/agents.yaml:8` | **非 mock 但属 T0.4 风险**：真实 dashscope API key 明文提交进 git | 立即轮换该 key；密钥迁 `config/local.yaml`（已 gitignore）或 enc: 加密（`python -m app.core.crypto`，`config/config.yaml` crypto 段） |
| 8 | `config/config.yaml` `doc_review.callback_timeout` | 死配置（无消费者） | C-02 实现时决定复用或删除，避免误导 |
| 9 | 模型调用链路 | **无 stub**：harness 走 `config/agents.yaml` qwen 真实端点；`app/services/llm.py` 是另一套多 provider LangChain 服务（doc_review/chat 用，与 harness 双轨） | 双轨并存是既成事实，A/C 线开发明确用 harness 轨道即可 |

---

## ⑥ D-22 文本规则口径常量表确认

规格源：`docs/后端架构与技术设计考虑.md:182-193`（§3.4）+ PRD §29.2（`CPS智能巡检系统PRD.md` 29.2 节）+ 计划 D-22（`docs/CPS智能巡检系统开发计划.md:383`）。PRD 29.2.3 明确把 `……` 的 L 计量交给技术设计定，§3.4 承接。

### 无歧义项（可直接进常量表+单测）

| 口径 | 冻结值 | 证据 |
|---|---|---|
| 计数单位 | Unicode code point（Python `str` 天然） | §3.4:186 |
| 换行剔除集合 | `{"\n", "\r", "\u2028", "\u2029"}`，剔除后 S，`L=len(S)` | §3.4:186 |
| 空格计入 L | 是（不再排除空格） | §3.4:186；PRD 29.2.1 |
| 空格计入 P | **是**（"空格也作为标点计数"） | PRD 29.2.2；§3.4:187（U+0020 必算） |
| P 的含义 | 出现次数（非种类数） | §3.4:187 |
| `……` 状态机 | 遇 U+2026 且下一字符亦 U+2026 → 整体消耗 P+=1，不计连续；单个 U+2026 → 计一标点；不得折叠其他重复标点 | §3.4:188 |
| 连续标点 | S 中相邻两字符均属"标点/空格类"即违规（`， 。` 中空格属类，插空格不能绕过）；合法 `……` 已被整体消耗不触发 | §3.4:189 |
| 判定 | `length_ok=(L>=15)`；`ratio_ok=(10*P<=L)` 整数精确比较（无除法，天然防除零） | §3.4:190 |
| 三字段独立 | 原因/短期/长期分别计算，禁合并凑字数 | §3.4:190；PRD 29.1 |
| 边界用例 | L=15,P=1✓；L=15,P=2✗；L=20,P=2✓（恰 10% 不放宽）；L=14,P=0✗；空文本✗；`， 。`✗；`……` P=1 不违规 | §3.4:191 |
| 落库 | L/P 实际值随意见明细存 `cps_initial_review_item` | §3.4:192 |
| 语义检查归属 | 无意义/敷衍/雷同由模型出意见，不直接退回 | §3.4:193 |

### 歧义/待冻结项（A5 开工前必须定，对应 D-22★）

1. **`……` 的 L 贡献矛盾（最高优先）**：§3.4:186 主规则"按 code point 计 L"，但 §3.4:188 示例"两个 U+2026 → L 贡献 6"。code point 应为 **2**；6 对应 UTF-8 字节数（E2 80 A6 ×2）或"六点等价折算"。两处自相矛盾。**建议冻结为 2**（与 `L=len(S)` 主规则一致）；若业务坚持 6，须同时修订 §3.4 第 1 条计数单位定义。此值直接影响边界用例（如 14 字符+`……` 是否达 L=15/16），必须先定再写单测。
2. **U+3000 全角空格**：§3.4:187 "建议同算并在口径文档记录"——未决。影响 P 计数与连续判定（全角空格夹在标点间是否违规）。建议冻结为"计入标点/空格类"并写入常量表（保守口径，防绕过）。
3. **标点集合全量清单未列尽**：§3.4:187 中文标点以"等"省略（明列：，。、；：？！""''（）【】《】—…；英文：`,.;:?!'"()[]{}-`）。A5 需产出精确字符集常量（建议含中文省略号专名号《》已列、补 `·～—…` 等逐一评审）后冻结，禁"等"。
4. **verdict 组合语义未显式定义**：返回结构含三布尔+verdict，但 verdict=三布尔 AND 的关系未写明。建议冻结 `verdict = length_ok AND ratio_ok AND no_consecutive_punct`，并在常量表注明。
5. **其他空白字符**：剔除集合仅 4 种换行；`\t`/`\v`/`\f` 保留计 L 且不属标点类。PRD 只约束换行，属合理缺省，但建议常量表显式声明"制表符不剔除、不计 P"，避免实现分歧。

---

## ⑦ A5/C1 开发前置就绪清单（工程约定）

### 可以立即开工的依据
- 模型链路真实可用（qwen 已配，探测 200）；pytest 基础设施完整（`asyncio_mode=auto`，`pyproject.toml:86-87`；tests/cps 4 个套件不依赖真实 LLM）；harness 内核（run/approval/SSE/幂等）成熟且有测试覆盖（tests/harness 8 套件）。
- A5 是纯函数+常量表+单测，零外部依赖，唯一前置=⑥ 的 5 个口径冻结项。

### 工程落点约定

| 事项 | 约定/现状 | 证据 |
|---|---|---|
| router 组织 | 新端点=新文件入 `app/api/v1/endpoints/`，在 `app/api/v1/router.py:11-22` include（现有 10 个 router 的既有模式）；C-01~C-08 建议 1~2 个新文件承载（如 `agent_callbacks.py`、`weekly_reports.py`） | `app/api/v1/router.py` |
| service 组织 | 运行态业务沿用 `app/projects/<域>/{domain,application,infrastructure,prompts}` 四层模式（参照 `app/projects/cps/`）；需 Java 可见/对账的数据按计划落 PG 表 | `app/projects/cps/` 目录结构 |
| PG 表与迁移 | `app/models/` 增 ORM + `uv run alembic revision --autogenerate`；当前 head=`9d35bb4a1977`，default_datasource=postgres_primary，env.py 从 settings 读 DSN 并把 asyncpg 切 psycopg2（dev extras 已含） | `migrations/env.py:1-40`；`pyproject.toml` dev extras |
| A5 建议落点 | `app/projects/initial_review/domain/text_rules.py` 纯函数 `check_text_rules(text)` + 常量表模块；`tests/test_text_rules.py` 固化 §3.4:191 边界集+⑥ 冻结值 | — |
| C1 建议落点 | Skill 落 `skills/`（deepagents 机制）；周报调度落 `app/tasks/weekly_report.py` 用 `@scheduled(cron="0 8 * * 1")`——**timezone 已全局配置 Asia/Shanghai**（`config/config.yaml` scheduler.timezone，coalesce/max_instances 同段）；`weekly_report_run` 表走 alembic；下载端点参照 `cps.py:171-183` | `app/core/scheduler.py`；`app/tasks/demo_tasks.py` |
| 幂等模式 | 统一 digest 指纹（重放异参→409），参照 `cps_command`（`service.py:61-77`）与 `submission`（`runtime.py:317-323`） | — |
| 模型配置 | `config/agents.yaml`（env `AGENT_CONFIG` 可覆盖，`app/harness/kernel/bootstrap.py:21`）；Provider kind=openai/anthropic/ollama（`kernel/infrastructure/models.py:14-31`）；Agent→模型映射在 `agents:` 段 | — |
| 额度钩子 | **无**（grep `quota|rate_limit|budget` 零命中）。C1 周报批量调用与 A 线视觉并行调用需自建并发/限额闸门（APScheduler max_instances=1 仅防重入，非模型额度） | — |
| R-15 model_version | 现状=run.model_snapshots（提交时快照，`runtime.py:310,330`；deepagents `snapshot()` `kernel/infrastructure/deepagents.py:42`）。需在 AgentRun 暴露层补标准化 `model_version` 字符串（provider/model/参数摘要） | — |
| Java 回调地址 | **无任何配置**。C-02 开工时新增 config 段 + `app/services/http_client.py` 复用；internal_trust 网段模式参照 `config/cps.yaml` trusted_networks | — |
| 测试命令 | `uv run pytest -q`（全量）/ `uv run pytest tests/cps -q`（CPS 域）/ `uv run pytest tests/test_text_rules.py -q`（A5）；lint：`uv run ruff check .`（line-length=100, py311，migrations/versions 已排除）；迁移：`uv run alembic upgrade head`。可运行性标"待运行验证"（本轮按只读约束未执行） | `pyproject.toml:82-87` |
| 集成环境 | 本机 8000/9000/9001/8090/8008/5432/8080 全部存活（见①-6）；`.agent-data/state.sqlite` 为现有运行数据勿删；**仓库未提交改动（app/harness/agents/ 删 8 文件等）不得还原/覆盖** | — |

### 开工前必须关闭项（阻塞清单）

1. D-22 口径 5 项冻结（⑥，A5 首日）——尤其 `……` L 贡献 2 vs 6 的矛盾。
2. `config/agents.yaml` API key 轮换与迁移（安全，建议当日）。
3. C-01~C-04 的 internal_trust 鉴权口径（auth.py 占位如何替代）——建议 A 线开工前与 Java 侧约定（同源于后端架构师 Java 侧报告）。
4. C1 首日工程决策：S3 客户端选型（boto3 vs minio）+ `weekly_report_run` 迁移文件编号规划（head 现为 9d35bb4a1977 单节点）。
