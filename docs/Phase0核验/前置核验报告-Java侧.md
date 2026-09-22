# Phase 0 前置核验报告 · Java 侧（cps 系统）

> 核验人：后端架构师（Java 侧）｜核验日期：开发启动日（Phase 0）
> 对应计划：T0.1 旧单兼容盘点 / T0.2 通道核查 / T0.4 mock 排查 + 工程事实盘点
> 性质：**只读核查**，未修改任何业务代码、未 git commit。
> 路径约定：以下相对路径均以 `cps/backend/` 为根（绝对根 `/Users/csai/project/cps-agent/cps/backend/`）。

---

## ① 结论摘要

**判定：A1 线（整改底座）可立即启动**，且无须等待 Phase 0 其余项全部关闭。理由：

1. **工程底座成熟可用**：Maven + Spring Boot 2.5.15 + MyBatis(XML) + Flyway + RustFS(MinIO SDK) 全链路在位，测试可脱离基础设施运行（详见 §4/⑦）。新表/新接口/新状态机均为增量开发，无阻塞性技术债。
2. **C-01/C-02/C-03/C-07/C-09 全部缺失**，需从零建设——这是 A1 线本身的工作量，不是启动障碍（详见 §3 契约缺口表）。
3. **C-10（既有 inspections/start 链路）已具备且运行时实证连通**：`GET /api/cps/admin/agent/runtime` 实测返回 `state=ONLINE`（含真实 metrics JSON），Python 侧 `127.0.0.1:8000` 为本机原生 python 进程（PID 42717），通道活。
4. **MySQL 数据源真相（纠正任务给定事实）**：`src/main/resources/application.yml:8` 配置 `jdbc:mysql://localhost:3306/cps`（root/明文密码，本机直连，非容器服务名）。实测 `nc 127.0.0.1 3306` **TCP 可达**，8080 运行中实例的 DB 查询返回 200（`/api/cps/issues?tab=todo` → `[]` 空表）。"3306 未监听"的既有判断不成立——`lsof` 看不到监听进程（沙箱进程可见性限制），但 TCP 层连通、业务查询正常。**注意**：docker 中无任何 MySQL 容器（`docker ps -a` 证实），3306 的宿主进程归属不可见，环境重启后能否自动恢复 MySQL **待运行验证**（风险 R-N3，见 §6）。
5. **必须先处理的两件事**（纳入 A1 前置就绪清单 §7）：(a) cps 仓库当前有 **30+ 个未提交修改文件**（远不止 pom.xml 与 Controller，含 domain/dto/mapper/service/application.yml/mapper XML，`git status --short` 证实）——当前工作区即最新事实，本报告全部结论基于此；M0 基线冻结前必须先提交。(b) `CpsAgentFrameworkClient.java:55-56` 的附件断点：新上传附件 `content==null` 被静默跳过，evidence 不再传 Python——A1 线必须改为从 RustFS 读流（详见 §3 C-10 行与 §6 R-02）。

---

## ② 现状全景（表 / 状态机 / 接口清单）

### 2.1 数据库表现状（Flyway 迁移链）

迁移目录：`src/main/resources/db/migration/`（Flyway 自动执行，`baseline-on-migrate=true, baseline-version=0`，`application.yml:20-22`）。seed 目录 `db/seed/` **不走 Flyway**，需手工导入（`db/seed/README.md`）。

| 表 | 迁移文件 | 关键结构 |
|---|---|---|
| `cps_issue` | `V20260626__cps_inspection.sql` | 主表；`issue_no UNIQUE`（已被 V20260922 DROP）；`agent_inspection_id VARCHAR(128) NULL + UNIQUE`（`V20260911__cps_agent_framework.sql`） |
| `cps_issue_attachment` | 同上 + V20260911 | `file_url VARCHAR(500) NOT NULL`；`content MEDIUMBLOB NULL`（V20260911 加，注释"原始附件内容，用于同步视觉 Agent"）；`UNIQUE(issue_id,stage,sort_no)` |
| `cps_issue_ai_suggestion` | V20260626 | `raw_request/raw_response JSON`, `confidence DECIMAL(6,4)` |
| `cps_issue_flow_log` | V20260626 | `from/to_status, action, operator/handler 快照, comment, snapshot_json JSON` |
| `cps_reminder_rule` | V20260626 | `node_status UNIQUE, duration_days, need_escalation`（**代码中无任何消费方**，死表） |
| `cps_knowledge_case` / `cps_knowledge_case_image` | V20260629 | 图片向量同步域：`file_url, file_hash, milvus_vector_id, embedding_dim, vector_status(PENDING/PROCESSING/SUCCESS/FAILED), vector_retry_count` |
| `cps_issue_ai_match` | V20260629 | `topk_json/raw_request/raw_response`, `confirmed_*` 人工确认字段 |
| `cps_problem_category` | `V20260702__cps_master_data.sql`（V20260920 DROP category_code） | 问题分类树 |
| `cps_area_person_config` | V20260702（V20260918 +审计列） | `UNIQUE(factory,area,line,process)`，line/process 空串=上级默认 |
| `cps_admin_user` | `V20260919__cps_admin_user.sql` | `emp_no UNIQUE, enabled` |

**存量数据**：运行实例查询 `/api/cps/issues?tab=todo` 返回 `[]`——`cps_issue` 当前为空表，**不存在需要按旧流程办结的存量在途单**（运行时验证；`db/seed/V999999__cps_demo_data.sql` 已过期不可用：仍 INSERT 已被 V20260922 删除的 `issue_no` 列）。这大幅降低 R-09 的实际迁移成本——"旧单兼容"主要是**代码路径兼容**而非数据迁移。

### 2.2 状态机（T0.1 核心）

- 状态枚举：`src/main/java/com/company/cps/domain/CpsIssueStatus.java:3-9` — 仅 5 值：`PENDING_FEEDBACK, PENDING_RECTIFY, PENDING_UPLOAD_PROOF, PENDING_REVIEW, CLOSED`。**无** `PENDING_AI_REVIEW`/`PENDING_REVIEWER_CONFIG`，无任何 flow 版本概念。
- 动作枚举：`src/main/java/com/company/cps/domain/CpsIssueAction.java:3-11` — 7 值：`SUBMIT, REPLY_ASSIGN, RECTIFY, UPLOAD_PROOF, REVIEW_CLOSE, REVIEW_REJECT, TRANSFER`。
- 流转表：`src/main/java/com/company/cps/service/CpsWorkflowStateMachine.java:19-27` — 全部合法流转：

```
PENDING_FEEDBACK   +REPLY_ASSIGN  → PENDING_RECTIFY
PENDING_RECTIFY    +RECTIFY       → PENDING_UPLOAD_PROOF
PENDING_UPLOAD_PROOF +UPLOAD_PROOF → PENDING_REVIEW
PENDING_REVIEW     +REVIEW_CLOSE  → CLOSED
PENDING_REVIEW     +REVIEW_REJECT → PENDING_UPLOAD_PROOF
四态均支持 +TRANSFER → 自环（换 currentHandler）
```
非法流转抛 `IllegalArgumentException("Action ... is not allowed from status ...")`（`CpsWorkflowStateMachine.java:31-36`）。**该状态机即"旧流程"，计划红线"旧单旧流程办结"意味着此文件及其流转不可删改语义，只能新增并行 V2 状态机。**

- 实体字段：`src/main/java/com/company/cps/domain/CpsIssue.java:5-38` — 含 `agentInspectionId, status, factory/area/line/process, aiCategoryL1/L2Id, categoryL1/L2Id, categoryModifiedFlag, description, creator/feedback/responsible/proof/reviewer/currentHandler(EmpNo+EmpName), reasonAnalysis, correctiveMeasure, rectifyRemark, reviewOpinion, submitTime, closeTime`。
  **计划 §2.2(a) 要求的改造列全部缺失**：无 `short_term_measure`/`long_term_measure`/`current_submission_version`/`inspection_item_id`/`flow_version`。

### 2.3 现有接口全景（9 个 Controller）

| Controller | 基路径 | 端点 |
|---|---|---|
| `CpsIssueController` | `/api/cps/issues` | `POST /`（创建，建单即 PENDING_FEEDBACK+触发 Agent）；`GET /`（tab=todo/created/related/closed+empNo）；`GET /{id}`；`POST /{id}/actions` |
| `CpsAttachmentController` | `/api/cps/attachments` | `POST /`（multipart 上传→RustFS）；`GET /{id}/content`（读流） |
| `CpsAiController` | `/api/cps/ai` | `POST /match-knowledge`（唯一 AI 端点） |
| `CpsMasterController` | `/api/cps/master` | `GET /factories /areas /lines /processes /categories` |
| `CpsAssignmentController` | `/api/cps/assignment` | `GET /feedback-handler`；`GET /reviewer` |
| `CpsAdminIssueController` | `/api/cps/admin` | `GET /overview /issues /issues/export` |
| `CpsMasterAdminController` | `/api/cps/admin/master` | `POST /auth/login`；categories 与 area-person-configs 的 CRUD/分页/导出/导入模板/新建 |
| `CpsKnowledgeAdminController` | `/api/cps/admin/knowledge` | cases/images/materials 的分页/导出/上传；`POST /cases/{id}/sync-vectors`、`/images/{id}/sync-vector`、`/images/sync-vectors` |
| `CpsAdminAgentController` | `/api/cps/admin/agent` | `GET /runtime`（代理 Python `/cps/metrics`） |

关键行为证据：
- 动作校验：`CpsIssueService.java:201-217` — `REPLY_ASSIGN` 需 reasonAnalysis/correctiveMeasure/responsibleEmpNo；`RECTIFY` 需 proofEmpNo；`UPLOAD_PROOF` 需 1–5 凭证附件（`MAX_ATTACHMENTS=5`）；`REVIEW_CLOSE/REJECT` 需 reviewOpinion；`TRANSFER` 需 targetEmpNo。
- 审核人路由现状：`CpsIssueService.java:219-270`（applyAction）+ `CpsAssignmentService.java:33-41`（findReviewer）— 按 **factory×area** 查 `cps_area_person_config`，无匹配时抛 `IllegalArgumentException("reviewerEmpNo is required when no assignment rule matches")`。**与计划/架构文档要求的 item×factory（`cps_item_factory_reviewer`）不同维度**（D-04 输入，见 §5）。
- 建单触发 Agent：`CpsIssueService.java:122`（createIssue 内）→ `CpsAgentFrameworkClient.createAndStart(...)`，成功后回写 `agent_inspection_id`。**注意：该 HTTP 调用在 `@Transactional` 事务内同步执行**（Python 挂起会拖住 DB 事务；已有 10s 超时兜底 `application.yml` `timeout-ms=10000`）。
- 流转日志：`CpsIssueService` 在 createIssue/executeAction 中写 `cps_issue_flow_log`（operator/handler 快照 + snapshot_json），是 A1 线审计口径的现成挂载点。

### 2.4 附件存储现状（T0.1/T0.3 相关）

- **现行模式 = RustFS object_key**：`CpsAttachmentService.java:27-52`（upload）— `objectKey = "cps/"+UUID+"-"+清洗文件名` → `storage.put()`（MinIO SDK）→ insert，**不再 setContent，`content` MEDIUMBLOB 列恒 NULL**。`fileUrl` 字段即 objectKey。
- 读取：`CpsAttachmentService.java:59-68`（content(id)）→ `storage.read(fileUrl)` 从 RustFS 取流。`buildLegacyFileUrl` 已成死代码（无调用方）。
- **MEDIUMBLOB 确证**：`V20260911__cps_agent_framework.sql`（cps_issue_attachment ADD content MEDIUMBLOB NULL，注释"原始附件内容，用于同步视觉 Agent"）——存量双模式兼容的"存量"侧；因 cps_issue 现为空表，实际存量≈0。
- RustFS 封装：`RustFsStorageService.java`（78 行）— `ensureBucket/@PostConstruct`、`put/get/read/publicObjectUrl`；`application.yml` `cps.storage.*`（endpoint 127.0.0.1:9000, bucket=cps-attachments）。运行时容器 `cps-rustfs` **Up 5 days (healthy)**。
- **断点**：`CpsAgentFrameworkClient.java:55-56` — `if (attachment.getContent() == null || attachment.getContent().length == 0) continue;` → 走 RustFS 的新附件 content 为 null，**被静默跳过，图片不再传给 Python**，且无按 fileUrl 读 RustFS 的回退。当前线上行为=建单触发的是"无图巡检"。

### 2.5 Java→Python 现存调用点（T0.2 全量）

唯一 HTTP 客户端：`src/main/java/com/company/cps/service/CpsAgentFrameworkClient.java`（RestTemplate + SimpleClientHttpRequestFactory，connect/read timeout=10s；headers 仅 Content-Type，**无鉴权头**——依赖 Python 侧 internal_trust，见 `README-AGENT-INTEGRATION.md`）：

| 方法 | 行号 | 调用 | 幂等键 |
|---|---|---|---|
| `createAndStart` | L38-73 | ①`POST {base}/cps/inspections`（payload: goal=line.description, line_info{line_id,area,supervisor_id,modifications}, required_outputs=[issues,report,work_plan]）→ ②逐附件 `POST /cps/inspections/{id}/evidence`（kind=before, content_base64, expected_version 乐观版本链）→ ③`POST /cps/inspections/{id}/start`（expected_version） | `legacy-cps-issue-{issueId}` / `legacy-cps-evidence-{issueId}-{attId}` / `legacy-cps-start-{issueId}` |
| `runtimeStatus` | L76-96 | `GET /cps/metrics` → ONLINE/UNAVAILABLE/DISABLED | — |

运行时实证（本机）：8080 运行中，`GET /api/cps/admin/agent/runtime` → `{"state":"ONLINE",...}` 含真实 metrics；8000 端口归属宿主机原生 `python3.1` 进程（PID 42717）——**注意容器 `basic-project-agent-1` 已 Exited(1) 5 天**，Python 服务实际以本机进程方式在跑，环境拓扑与 README 描述有偏差（见 §6 R-N2）。

---

## ③ 契约缺口表（C-01~C-12 · Java 侧逐条）

| 契约 | 内容 | Java 侧状态 | 证据 |
|---|---|---|---|
| C-01 | 整改提交触发初审（`POST /api/agent/rectifications`，幂等 `cps-rectify-{issueId}-v{n}`） | **缺失** | `CpsAgentFrameworkClient.java` 无任何 rectification 方法；`CpsIssueService.executeAction` 的 RECTIFY 动作仅更新 DB 字段，无外部调用 |
| C-02 | 初审结果回调接收端点（Python→Java 回写，is_late 留痕） | **缺失** | 全部 9 个 Controller 无 `/api/callbacks/**` 路由；无回调鉴权机制 |
| C-03 | 初审状态轮询（`GET /api/agent/rectifications/{ref}`） | **缺失** | 无对应客户端方法与端点；亦无 `@Scheduled` 扫描（全代码 grep `@Scheduled/@EnableScheduling` 零命中） |
| C-04 | room-checks/judge 两阶段判定（TYPE_MISMATCH/JUDGED/UNJUDGEABLE，同步） | **缺失** | 无 room/check-item 任何 domain/mapper/service/controller；`V2026*` 迁移链无对应表 |
| C-05 | 周报记录查询 | **缺失** | 无周报域任何代码 |
| C-06 | speech-to-text | **缺失** | 无语音域代码 |
| C-07 | 计划草稿请求（`inspection-plans/draft`，幂等 `plan-draft-{sourceRunId}`） | **缺失** | 无 plan 域代码 |
| C-08 | 周报管理端代理 | **缺失** | 同 C-05 |
| C-09 | 批准后建单（Java 内部事务，`plan-task-{planId}-{itemId}` UNIQUE，补建仅扫 CREATE_FAILED） | **缺失** | 无 plan/task 域；无 `@Scheduled` 补建扫描 |
| C-10 | 既有 `POST /cps/inspections` + `/start` 保留 | **已具备**（含 1 处必改断点） | `CpsAgentFrameworkClient.java:38-73`；运行时 ONLINE 实证；断点=L55-56 content==null 跳过新附件（§2.4） |
| C-11 | 移动端问题创建改造（短/长期措施、inspection_item、flow_version） | **部分具备** | `POST /api/cps/issues` 存在，但 `CpsIssueCreateRequest`/`CpsIssue.java` 无新字段（§2.2） |
| C-12 | 移动端问题详情改造 | **部分具备** | `GET /api/cps/issues/{id}` 存在，返回旧字段集 |

---

## ④ mock/占位清单（T0.4）

**主代码 grep 证据**：`src/main/java` 全量搜索 `TODO|FIXME|XXX|HACK|mock|stub|placeholder|假数据|占位` → **零命中**。Java 侧没有字面量 mock 残留，问题形态是"缺失"而非"假实现"。逐项：

| # | 项 | 性质 | 证据 | 处置建议 |
|---|---|---|---|---|
| M-1 | `NoopMilvusVectorService` | **配置型降级**（非 mock 残留）：`cps.milvus.enabled=false` **或缺失时**激活，search 恒返回空列表 | `NoopMilvusVectorService.java:10,25-27` | 保留（合理的运行时降级），但验收环境必须 `cps.milvus.enabled=true`（当前 `application.yml` 已=true，HttpMilvus 生效 `HttpMilvusVectorService.java:22`） |
| M-2 | `CpsIssueService.empName()`：`return empNo;` | **占位实现**：姓名用工号顶替 | `CpsIssueService.java:404` | A1/C 线接真实人员主数据时替换；当前无人员表，属已知简化 |
| M-3 | `resolveCurrentEmpNo` 默认 `"DEV_EMP"` | **无鉴权**：身份完全由请求参数声明，无 JWT/Filter/Interceptor（grep `OncePerRequestFilter|HandlerInterceptor|WebMvcConfigurer` 零命中） | `CpsIssueController.java:76-81`、`CpsAttachmentController` 同款 | 开发期约定；C-02 回调端点上线时必须同步引入 internal_trust 校验（§5） |
| M-4 | 管理端登录=工号+姓名比对 `cps_admin_user`，无密码/JWT/session | 弱认证现状 | `CpsMasterDataService.java:66-73` | 记录为工程事实，不在本轮范围 |
| M-5 | `db/seed/V999999__cps_demo_data.sql` | **过期脚本**：INSERT 已删除的 `issue_no` 列，执行必失败 | `V20260922__cps_issue_no_drop.sql` vs seed | Phase 0 结束前重写或删除 |
| M-6 | AI 匹配链（`CpsAiMatchService` 214 行） | **真实实现**：RustFS 取图→embedding(:8090,容器 Up)→Milvus(:8008,容器 Up)→落 `cps_issue_ai_match` 全留痕 | `CpsAiMatchService.java:64-96` | 非 mock；验收时可作真实链路样板 |

**《验收必须真实接口》清单（Java 部分，对照 PRD §26.1/§26.2）**——下列接口验收时**必须**为真实实现，禁止 mock/降级结果顶替：

1. AC-01：短/长期措施保存 + Agent 雷同性审核触发（C-01/C-02/C-03 全链，即 A1 线全部新接口）。
2. AC-02：整改前后图片送视觉比对——**含 C-10 evidence 链修复**（§2.4 断点），图片必须真实到达 Python。
3. AC-04/AC-05：周报文件上传 RustFS + 运行记录关联 + 管理端查询下载 + 推送"未配置"显式提示（C-05/C-08）。
4. AC-06：计划批准→三类业务任务建单（C-07/C-09），补建扫描真实运行。
5. AC-07/08/09/10/11：room-checks judge 同步判定 + 提交锁定 + 评分（C-04 及其 Java 落库/评分）。
6. AC-03：speech-to-text（C-06）。
7. 既有 `/api/cps/ai/match-knowledge`（M-6）保持真实依赖 8090/8008。

---

## ⑤ D-01 / D-04 决策输入建议

### D-01（★旧单兼容切换判定字段）——flow_version 最小侵入落点

**建议方案：`cps_issue` 单表加列 + 代码层路由，不建新表。**

| 落点 | 改动 | 侵入度 |
|---|---|---|
| DDL | 新 Flyway 版本（如 `V2026MMDD__cps_flow_version.sql`）：`ALTER TABLE cps_issue ADD COLUMN flow_version VARCHAR(16) NOT NULL DEFAULT 'legacy'` | 存量行自动=legacy；空表现状下零数据风险 |
| 实体 | `CpsIssue.java` +`flowVersion` 字段；`CpsIssueMapper.xml` 的 insert/find/update 增列 | 3 处 XML 增列 |
| 状态机 | **`CpsWorkflowStateMachine` 保持不动**（旧单专用）；新建 `CpsWorkflowStateMachineV2`（PENDING_RECTIFY→PENDING_AI_REVIEW→PENDING_REVIEW→PENDING_REVIEWER_CONFIG→CLOSED，对应新 `CpsIssueStatus` 枚举值新增 `PENDING_AI_REVIEW/PENDING_REVIEWER_CONFIG`——枚举加值不影响旧 Map 键） | 零修改旧文件 |
| 路由点 | `CpsIssueService.executeAction`（`CpsIssueService.java:133-165`）按 `issue.getFlowVersion()` 选择状态机实例；`createIssue` 写入 `v2` | 单点路由，旧路径原样 |
| 查询 | `listByTab/adminIssueFilter/countByStatus`（`CpsIssueMapper.xml`）暂不加 flow_version 过滤（状态值本身可区分新旧流程）；管理端列表如需筛选再加可选参数 | 可后置 |

**否决备选**：独立 `cps_issue_v2` 表——破坏单号连续性、雷同审核需跨版本查询、双表 JOIN 复杂度不值（存量≈0）。
**默认值语义**：`legacy` 默认 + "旧单旧流程办结"红线天然满足：即使应用层漏写 flow_version，行为退回旧状态机，不会出现旧单进新流程。

### D-04（事项必选/厂区映射/提醒渠道）——审核人配置模型

- 现状：审核人=factory×area 维度（`cps_area_person_config`，`CpsAssignmentService.java:33-41`），服务于**问题单**旧流程。
- 目标：**巡检事项**维度 item×factory（`cps_inspection_item_permission` 无行=开放 + `cps_item_factory_reviewer UNIQUE(item_id,factory)`）。
- 建议：**新建两张表，不改造 `cps_area_person_config`**——两域语义不同（问题单兜底审核人 vs 巡检事项审核人）；旧流程读取路径保持零改动。旧流程 `findReviewer` 在 A1 新流程中不再使用（新流程审核路由走 item 维度），旧函数仅服务存量 legacy 单。
- 提醒渠道：现状无任何推送代码（推送只留接口为计划红线），无决策债务。

### 附带决策点（新增）：C-02 回调端点的信任机制

Java 现状无任何鉴权 Filter（M-3）。Python→Java 回调端点建议沿用 `README-AGENT-INTEGRATION.md` 的 internal_trust 语义：绑定内网网卡/校验来源 CIDR，或加共享 secret 头。**需在 A1 设计确认，不建议裸暴露。**

---

## ⑥ 风险对照（R-01/R-02/R-09 + 新发现）

| 风险 | 计划判定 | 核验结论 | 计划是否需修正 |
|---|---|---|---|
| R-01 适配入口≠接通（高） | 成立 | **成立且已实证**：runtime ONLINE 仅证明 `/cps/metrics` 通；C-01~C-09 九项全缺（§3）。"创建问题时启动 Agent"确为唯一现存接通点 | 不需修正；Phase 0 排查正是对症 |
| R-02 附件不可靠（中高） | 成立 | **部分成立、具体化**：新附件 RustFS 链路本身健全（上传/读取均真实），**断点在 `CpsAgentFrameworkClient.java:55-56`**——content==null 即跳过，新附件根本不进 evidence。修复=按 fileUrl 从 RustFS 读流再 base64（或与 Python 约定 object_key 直传） | 不需修正；A1 线纳入必改项 |
| R-09 旧单兼容范围不明（中高） | 成立 | **成立但风险降级**：cps_issue 现为空表（运行时验证），兼容问题≈纯代码路径问题；D-01 落点建议已给（§5） | 不需修正；建议在 D-01 决策记录中注明"存量≈0"以简化验收 |
| **R-N1（新）** 事务内同步 HTTP | 未识别 | `CpsIssueService.java:122`：`createAndStart` 在 `@Transactional` 内，Python 挂起→DB 事务被拖（10s 超时兜底但连接占用真实存在）；且 Python 不可用时**建单整体失败回滚**（README 声称 enabled=false 可独立运行，但 enabled=true 时强依赖） | 建议纳入 A1 技术设计：初审任务表+异步化已规避新链路；旧链路维持现状（红线：不改旧流程语义），仅监控 |
| **R-N2（新）** 环境拓扑漂移 | 未识别 | agent 容器 `basic-project-agent-1` Exited(1) 5 天，8000 实为宿主机原生 python 进程——机器重启后 Python 大概率不自启，Java 建单将失败 | 环境项：补启停脚本/文档，归 Python 侧核验报告跟进 |
| **R-N3（新）** MySQL 无容器化、进程不可见 | 未识别 | 3306 TCP 可达、业务正常，但 `docker ps -a` 无 MySQL 容器、`lsof` 不可见监听进程（沙箱限制）——MySQL 启动方式不明，重启后可恢复性**待运行验证** | 环境项：建议 MySQL 容器化或明确宿主服务清单 |

---

## ⑦ A1 开发前置就绪清单（工程约定，后续开发照此执行）

1. **版本管理**：开工前必须先提交 cps 仓库当前 30+ 未提交文件（`git status --short`：pom.xml、CpsIssue/CpsIssueAttachment/CpsAreaPersonConfig 等 domain、dto、mapper 接口+XML、CpsIssueService/CpsAttachmentService/CpsAiMatchService 等 service、application.yml）——由用户/牵头人执行；M0（W1.5）基线冻结以此为准。**不得还原/覆盖这些改动**（本核验已遵守）。
2. **技术栈锁定**：Spring Boot **2.5.15** + **Java 1.8**（`pom.xml:10,21`——禁用 var/record/新 switch 等高版本语法）；MyBatis 2.2.2（XML mapper + `map-underscore-to-camel-case`，`application.yml:14-16`）；Flyway（`classpath:db/migration`）；MinIO SDK 8.2.2；easyexcel 3.3.4。无 spring-security/lombok——新代码同样不引入。
3. **命名约定**：类名 `Cps` 前缀 + 域名词（现状平铺包结构 `controller/service/domain/mapper/dto/config/bootstrap/support`，新域按此平铺，不建子包）；mapper XML 与接口同名放 `resources/mapper/`。
4. **迁移约定**：新迁移文件 `V2026MMDD__<描述>.sql` 日期递增（现状链 V20260626→V20260922）；禁止修改已发布迁移；demo 数据走 `db/seed/` 手工导入（并先修复 M-5 过期脚本）。
5. **鉴权约定**：延续 internal_trust（`README-AGENT-INTEGRATION.md`：受信网段免 JWT）；Java 侧新增面向 Python 的端点（C-02）必须带来源校验；面向人的端点延续 empNo 参数显式声明（含 DEV_EMP 兜底）直至 C 线引入统一身份。
6. **附件约定**：新附件一律 RustFS object_key（`CpsAttachmentService` 现行模式），**禁止**回写 MEDIUMBLOB content；跨系统传图优先"读流转发"，object_key 直传需与 Python 侧约定凭据后启用。
7. **测试约定**：JUnit5（spring-boot-starter-test）；现状测试全部为**无基础设施依赖**的单元/静态契约测试（mapper contract 测试读 XML 断言字符串，如 `src/test/java/com/company/cps/mapper/CpsIssueMapperContractTest.java:14-31`——无 H2/testcontainers/嵌入式 MySQL）。可执行命令：`cd cps/backend && mvn test`（无需 DB；工具链 Maven 3.9.14 + JDK 25.0.2 编译目标 1.8，PATH java=17.0.18——多 JDK 并存，**编译目标兼容性待运行验证**）。`mvn spring-boot:run` 需本机 MySQL(3306)+RustFS(9000)+Python(8000) 就绪。
8. **状态机约定**：旧状态机 `CpsWorkflowStateMachine` 冻结（只读语义）；新流程一律走 V2 状态机 + flow_version 路由（§5 D-01）；所有流转必须写 `cps_issue_flow_log`（沿用 operator/handler 快照 + snapshot_json 模式）。
9. **AI 依赖清单**（运行时全 Up，实测）：embedding `127.0.0.1:8090`（容器 Up 5 天）、Milvus 网关 `127.0.0.1:8008`（容器 Up 5 天）、RustFS `127.0.0.1:9000`（healthy）、Python agent `127.0.0.1:8000`（宿主原生进程）。

---

### 运行时验证记录（本报告实证项）

| 验证 | 结果 |
|---|---|
| `curl :8080/api/cps/admin/agent/runtime` | 200，`state=ONLINE`，含真实 metrics（dispatch_count=0 等） |
| `curl :8080/api/cps/issues?tab=todo&empNo=DEV_EMP` | 200，`[]`（cps_issue 空表） |
| `nc 127.0.0.1 3306` | 连接成功（纠正"3306 未监听"判断） |
| `lsof :8000` | `python3.1` PID 42717（宿主原生，非容器） |
| `docker ps -a` | rustfs/embedding/vector Up；agent Exited(1) 5d；**无 MySQL 容器** |
| 标"待运行验证"项 | mvn test 在 JDK25/目标1.8 下编译；MySQL 重启后自恢复；8080 进程所用 JDK 版本 |

— 报告完 —
