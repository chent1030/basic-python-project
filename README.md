# fastapi-demo

基于 [FastAPI](https://fastapi.tiangolo.com/) 的生产级模板,内置:

| 能力 | 说明 |
|---|---|
| 🗄️ **多数据源** | PostgreSQL / MySQL / Redis 任意数量配置;**注解切换** |
| ♻️ **全异步 + 启动即连接** | 所有连接在 `lifespan` 启动时建好,关闭时释放 |
| 🔐 **可选认证** | 默认关闭,接口加 `@require_auth` 注解才开启 |
| 🤖 **LLM(LangChain)** | **多 provider**(多个 NewAPI 地址 + 模型),调用时切换;兼容国产模型 + LCEL 编排 |
| 📜 **Prompt 系统** | `prompts/` 目录管理提示词,YAML/TXT/Jinja2,变量插值 + 文件名加载 |
| 🌐 **HTTP 客户端** | httpx 异步封装,`get/post/put/patch/delete` 开箱即用 |
| 📝 **Spring Boot 风格日志** | 时间到毫秒 + PID + 请求 ID + logger 缩写;彩色控制台 / JSON / 文件 |
| ⏰ **定时任务** | `@scheduled` 装饰器(cron + 固定间隔),自动扫描 `app/tasks/`,运行时管理 |
| 🔄 **数据库迁移** | Alembic,数据源从 config 读,支持多数据源切换,密码加密复用 |
| 📄 **YAML 配置** | 全部走 `config/*.yaml`,不使用 `.env` |
| 🧱 **分层架构** | api / services / **repositories(SQL)** / models / schemas / core / db |

---

## 目录结构

```
fastapi_demo/
├── app/
│   ├── main.py                      # 应用入口:lifespan、异常处理、路由挂载
│   ├── __init__.py
│   │
│   ├── core/                        # ── 核心基础设施 ──────────────────
│   │   ├── config.py                #   YAML 配置加载 (Settings 单例)
│   │   ├── datasource.py            #   多数据源管理 + @use_datasource 注解
│   │   ├── auth.py                  #   @require_auth 认证注解 + AuthUser 类型
│   │   ├── prompt.py                #   📜 Prompt 加载/渲染器(YAML/TXT/Jinja2)
│   │   ├── logging_config.py        #   📝 Spring Boot 风格日志 + 请求 ID 中间件
│   │   ├── crypto.py                #   🔐 AES-GCM 密码加解密 + CLI 工具
│   │   ├── scheduler.py             #   ⏰ @scheduled 定时任务 + 自动扫描
│   │   └── security.py              #   JWT / 密码哈希工具
│   │
│   ├── api/                         # ── API 层 ──────────────────────
│   │   └── v1/
│   │       ├── router.py            #   v1 总路由聚合
│   │       └── endpoints/
│   │           ├── items.py         #   📌 多数据源注解切换示例 (3 种风格)
│   │           ├── auth.py          #   📌 @require_auth 认证示例
│   │           ├── chat.py          #   📌 LLM 调用 + LCEL 多步骤编排示例
│   │           ├── datasources.py   #   📌 数据源自描述 + Redis PING
│   │           └── examples.py      #   📌 HTTP 客户端 + Prompt 示例
│   │
│   ├── services/                    # ── 业务服务层 ────────────────────
│   │   ├── llm.py                   #   LangChain ChatOpenAI 封装 + LCEL chain
│   │   └── http_client.py           #   🌐 HTTP 客户端工具类 (httpx 封装)
│   │
│   ├── tasks/                       # ── ⏰ 定时任务(@scheduled 装饰器)─
│   │   └── demo_tasks.py            #   示例:cron + 固定间隔
│   │
│   ├── repositories/                # ── 📌 数据访问层(所有 SQL 写这里)─
│   │   ├── base.py                  #   BaseRepository:通用 CRUD + 原生 SQL
│   │   └── item_repository.py       #   ItemRepository:items 表的全部查询
│   │
│   ├── models/                      # ── SQLAlchemy ORM 模型 ──────────
│   │   └── item.py                  #   示例 Item 模型
│   │
│   ├── schemas/                     # ── Pydantic 请求/响应模型 ───────
│   │   ├── item.py
│   │   └── chat.py
│   │
│   └── db/
│       └── base.py                  #   Declarative Base 共享
│
├── config/                          # ── YAML 配置 (不使用 .env) ──────
│   ├── config.yaml                  #   全局默认配置
│   ├── local.yaml.example           #   本地覆盖示例(复制为 local.yaml)
│   └── local.yaml                   #   本地密钥,已被 .gitignore 忽略
│
├── prompts/                         # ── 📜 提示词目录 ────────────────
│   ├── translate.yaml               #   结构化示例(system + user + 变量声明)
│   ├── joke.txt                     #   纯文本 + 可选 front-matter
│   └── summarize.j2                 #   Jinja2 模板示例
│
├── migrations/                      # ── 🔄 Alembic 数据库迁移 ────────
│   ├── env.py                       #   运行环境(从 settings 读数据源)
│   └── versions/                    #   迁移脚本(autogenerate 生成)
│
├── alembic.ini                      # Alembic 配置(URL 留空,由 env.py 注入)
│
├── tests/                           # ── 测试 ─────────────────────────
│   ├── smoke_test.py                #   端到端冒烟(认证/数据源/路由)
│   └── test_prompt_http.py          #   Prompt + HTTP 客户端单测
│
├── docker/                          # ── Docker 辅助 ──────────────────
│   └── local.yaml.example           #   容器内运行时配置示例(DSN 用服务名)
│
├── Dockerfile                       # 多阶段构建(builder + runtime)
├── docker-compose.yml               # 一键全栈(app + PG + MySQL + Redis)
├── .dockerignore
├── pyproject.toml                   # 依赖 + 工具配置(uv / ruff / pytest)
├── .gitignore
├── .python-version
└── README.md
```

### 层级职责

| 层 | 职责 | 不应做 |
|---|---|---|
| `api/v1/endpoints` | 接收 HTTP 请求、调用 service / repository、返回响应 | **写 SQL**、业务规则 |
| `services` | 业务逻辑、外部服务封装(LLM、HTTP 等) | 关心 HTTP 细节、写 SQL |
| `repositories` | **所有 SQL / 数据库访问** | 业务规则、HTTP |
| `models` | ORM 模型(表结构定义) | 业务逻辑 |
| `schemas` | Pydantic 入参/出参模型 | 数据库实现 |
| `core` | 基础设施:配置、数据源、认证、安全、prompt | 业务规则 |
| `db` | `DeclarativeBase` 等共享 DB 基础 | — |

### SQL 写在哪?→ `app/repositories/`

**约定:endpoint 和 service 层不写任何 SQL,所有数据库操作集中在 repository 层。**

目录:
```
app/repositories/
├── __init__.py
├── base.py              # BaseRepository:通用 get/list/create/delete + 原生 SQL helper
└── item_repository.py   # ItemRepository:items 表的所有查询(示例)
```

**两种风格并存**,按需选用:

```python
from app.repositories import ItemRepository

# ── ORM 风格(推荐:类型安全、跨数据库可移植)──────────────────
repo = ItemRepository(db)
item = await repo.get(1)                          # 主键查询
items = await repo.list(limit=50)                 # 分页列表
new_item = await repo.create(name="x")            # 插入
items = await repo.search_by_name("关键词")        # 领域特有查询

# ── 原生 SQL 风格(复杂查询/聚合/性能优化)──────────────────────
# BaseRepository 提供 fetch_all / fetch_one / execute
rows = await repo.fetch_all(
    "SELECT id, name FROM items WHERE name ILIKE :pattern LIMIT :limit",
    {"pattern": "test%", "limit": 10},
)
stats = await repo.raw_stats()                     # 聚合查询示例
```

**新增表的步骤:**
1. 在 `app/models/` 定义 ORM 模型(如 `user.py`)
2. 在 `app/repositories/` 建 `user_repository.py`,继承 `BaseRepository[User]`
3. endpoint 里 `repo = UserRepository(db)` 调用

参考:`app/repositories/item_repository.py` 同时演示了 ORM 和原生 SQL 两种风格。

---

## 快速开始

### 1. 安装依赖(推荐 uv)

```bash
# 安装 uv(如尚未):https://docs.astral.sh/uv/
uv venv
uv pip install -e ".[dev]"
```

或用 pip:

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -e ".[dev]"
```

### 2. 配置数据源与密钥

```bash
cp config/local.yaml.example config/local.yaml
# 然后编辑 config/local.yaml,填入真实的 DB DSN 和 OpenAI API Key
```

默认配置见 `config/config.yaml`。**无任何环境变量** —— 加载规则纯文件驱动:

- 始终加载 `config/config.yaml`
- 如果 `config/local.yaml` 存在,自动深合并覆盖(逐字段)

切换环境 = 在 `config/` 下放(或不放)不同内容的 `local.yaml`,不需要改代码或设环境变量。

```bash
# 本地:有 config/local.yaml,自动覆盖默认配置
fastapi dev

# 生产:不放 local.yaml(或放生产版),用 config.yaml 默认值或卷挂载生产配置
fastapi run
```

### 3. 启动

```bash
fastapi dev          # 开发模式(热重载,默认 http://127.0.0.1:8000)
fastapi run          # 生产模式
```

启动后:
- Swagger 文档:http://127.0.0.1:8000/docs
- ReDoc:http://127.0.0.1:8000/redoc
- 健康检查:http://127.0.0.1:8000/health

---

## 核心能力详解

### 1️⃣ 多数据源 + 注解切换

数据源在 `config.yaml` 的 `datasources:` 下任意添加。启动时全部建立长连接。

**切换数据源的三种写法**(详见 `app/api/v1/endpoints/items.py`):

```python
# ✅ 风格 A:Annotated 类型别名(推荐,最清晰)
from app.core.datasource import DbPostgresPrimary, DbPostgresReadonly, DbMysqlBusiness, RedisCache

@router.get("/pg")
async def list_pg(db: DbPostgresPrimary): ...      # 写主库
async def read_pg(db: DbPostgresReadonly): ...     # 读副本
async def write_mysql(db: DbMysqlBusiness): ...    # MySQL

@router.get("/redis/ping")
async def ping(r: RedisCache): ...                 # Redis

# ✅ 风格 B:装饰器(@use_datasource,默认别名 db)
from app.core.datasource import use_datasource

@router.post("/mysql")
@use_datasource("mysql_business")
async def create(db: AsyncSession): ...

# ✅ 风格 C:原生 Depends()
from fastapi import Depends
from app.core.datasource import get_db
async def list_all(db: AsyncSession = Depends(get_db("postgres_readonly"))): ...
```

**新增数据源的步骤:**
1. 在 `config/config.yaml` 的 `datasources:` 下加一项,设置 `type` 与 `dsn`
2. 在 `app/core/datasource.py` 末尾加一行 `Annotated` 别名(可选,仅当想用风格 A)
3. 重启应用——lifespan 会自动建好连接

**表结构初始化(示例):**
```bash
curl -X POST http://127.0.0.1:8000/api/v1/items/pg/init
```

---

### 2️⃣ 认证:默认关闭,注解开启

全局 `auth.enabled: false`(在 `config.yaml`)。在此默认下:

| 接口 | 行为 |
|---|---|
| 普通接口 | ✅ 公开访问 |
| 加了 `@require_auth` 的接口 | 🔒 必须带 Bearer Token,否则 401 |

```python
from app.core.auth import require_auth, AuthUser

@router.get("/me")
@require_auth                                  # 👈 一个注解开启认证
async def me(current_user: AuthUser):
    return {"user": current_user}
```

**获取 Token(示例):**
```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/token \
     -H "Content-Type: application/json" \
     -d '{"username":"alice","password":"pwd"}'
```

**访问受保护接口:**
```bash
curl http://127.0.0.1:8000/api/v1/auth/me \
     -H "Authorization: Bearer <your-token>"
```

> 想要全局开启认证,只需把 `config.yaml` 里 `auth.enabled` 设为 `true`。

---

### 3️⃣ LLM(LangChain + 多 provider)

封装在 `app/services/llm.py`,底层用 LangChain 的 `ChatOpenAI` 连接 **NewAPI**(OpenAI 兼容协议),**支持配置多个 provider(多个 NewAPI 地址 + 多个模型),调用时切换**。天然兼容 DeepSeek / Qwen / GLM / Kimi 等国产模型。

设计原则:
- **多 provider** — 一个配置里可同时配 DeepSeek、Qwen、GLM、真 OpenAI 等,每个有独立的 base_url / api_key / model
- 调用时传 `provider` 参数切换;不传则用 `default_provider`
- 只发送国产模型都支持的最小参数集,**不发送 OpenAI 专属参数**(reasoning_effort / service_tier / logprobs)
- 保留我们灵活的 prompt 系统(YAML/Jinja2),LangChain 仅用于模型调用 + LCEL 链式编排

**配置(`config.yaml` 或 `config/local.yaml` 的 `llm:` 段):**
```yaml
llm:
  default_provider: deepseek              # 不传 provider 时用这个
  providers:
    deepseek:                             # 供应商名(自定义)
      base_url: "https://api.deepseek.com/v1"
      api_key: "enc:xxx"                  # 支持 enc: 加密
      model: "deepseek-chat"              # 该 provider 的默认 model
      temperature: 0.7
    qwen:
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
      api_key: "sk-xxx"
      model: "qwen-plus"
    glm:
      base_url: "https://open.bigmodel.cn/api/paas/v4"
      api_key: "sk-xxx"
      model: "glm-4"
```

**四种调用方式:**

```python
from app.services.llm import llm
from langchain_core.messages import HumanMessage

# 1) 最简调用:用默认 provider
text = await llm.invoke([HumanMessage(content="你好")])

# 2) 切换 provider(传 provider 参数)
text = await llm.invoke(
    [HumanMessage(content="你好")],
    provider="qwen",
)

# 3) 按 prompt 文件调用 + 切换 provider
text = await llm.complete_prompt(
    "translate", source="Hello", target_lang="中文",
    provider="qwen",
    overrides={"model": "qwen-max"},      # 临时换该 provider 的具体模型
)
async for chunk in llm.complete_prompt_stream(
    "joke", topic="程序员", provider="glm"
):
    print(chunk, end="", flush=True)

# 4) 链式:某个流程里固定用某 provider
qwen = llm.use("qwen")                    # 返回绑定 qwen 的子服务
await qwen.complete_prompt("summarize", text=article)
await qwen.complete_prompt("translate", source="...", target_lang="中文")

# 5) LCEL 多步骤编排(两步可用同一 provider)
pipe = (
    llm.chain("summarize", provider="qwen", output_key="source")
    | llm.chain("translate", provider="qwen")
)
result = await pipe.ainvoke({"text": article, "target_lang": "English"})
```

**HTTP 接口(`app/api/v1/endpoints/chat.py`):**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/v1/llm/providers` | 列出所有可用 provider |
| POST | `/api/v1/llm/invoke` | 单次调用(支持 provider 参数) |
| GET  | `/api/v1/llm/stream` | SSE 流式(支持 ?provider=) |
| POST | `/api/v1/llm/prompt/{name}` | 按 prompt 文件调用 |
| POST | `/api/v1/llm/prompt/{name}/stream` | 按 prompt 文件流式 |
| POST | `/api/v1/llm/pipeline` | LCEL 多步骤编排演示 |

**新增 provider:** 在 `config/local.yaml` 的 `llm.providers` 下加一项即可,重启后生效,代码零改动。

---

### 4️⃣ 日志(Spring Boot 风格)

封装在 `app/core/logging_config.py`,完全对标 Spring Boot 默认日志格式。

**输出示例(彩色控制台):**
```
2026-07-15 14:23:45.123  INFO 12345 [req-a1b2c3d4] a.s.llm              : Connection pool initialized
2026-07-15 14:23:46.456  WARN 12345 [req-a1b2c3d4] a.a.v.e.items        : Slow query detected (2.3s)
2026-07-15 14:23:47.789 ERROR 12345 [req-b2c3d4e5] a.s.http_client      : Failed to reach upstream
```

字段拆解(对照 Spring Boot):

| 字段 | 说明 | Spring Boot 对应 |
|---|---|---|
| `2026-07-15 14:23:45.123` | 时间到毫秒 | `%(date)` |
| `INFO` / `WARN` / `ERROR` | 级别固定 5 字符宽,彩色 | `%-5level` |
| `12345` | 进程 PID | PID |
| `[req-a1b2c3d4]` | **请求 ID**(每个请求唯一,贯穿链路) | `[线程名]` |
| `a.s.llm` | **logger 名缩写**(`app.services.llm` → `a.s.llm`) | `c.e.d.X` 缩写 |
| ` : ` | 分隔符 | ` : ` |
| 消息 | 日志正文 | message |

**关键特性:**

1. **请求 ID 链路追踪** —— 每个请求由 `RequestIdMiddleware` 注入唯一 ID,写入 `ContextVar`(异步安全),同请求内所有日志自动带上。客户端可透传 `X-Request-Id` 请求头,否则自动生成,响应头回写。

2. **logger 名缩写** —— 类似 Spring 把 `com.example.UserService` 缩成 `c.e.UserService`,本项目把 `app.services.llm` 缩成 `a.s.llm`,日志列对齐美观。

3. **两种输出格式**(配置切换):
   - `console`(默认):彩色 Spring Boot 风格,人眼友好
   - `json`:单行 JSON,带 `@timestamp / level / request_id / logger / message`,供 ELK / Loki / CloudWatch 采集

4. **可选文件输出** —— 配 `file: "logs/app.log"` 同时写一份纯文本(无色)到文件。

5. **按天滚动归档(对应 Logback RollingFileAppender)** —— 日志文件每天午夜自动滚动:当前文件改名归档,创建新文件继续写,超过 `backup_count` 天的最老归档自动删除。归档命名 `app.log.2026-07-14`。

**配置(`config.yaml` 的 `logging:` 段):**
```yaml
logging:
  level: "INFO"
  format: "console"          # console(彩色)| json(单行 JSON)
  color: true
  file: "logs/app.log"       # 主日志文件;null=只输出控制台
  rotation: "daily"          # daily(每天午夜)| hourly | weekly
  backup_count: 30           # 保留 30 天历史日志,超期自动删除
  max_file_size: 0           # MB;0=只按时间滚动,>0 额外按大小切分
```

**归档效果:**
```
logs/
├── app.log                  当前正在写
├── app.log.2026-07-14       昨天的归档
├── app.log.2026-07-13       前天的归档
├── ...
└── app.log.2026-06-15       30 天前(再老一点会被自动删除)
```

**在代码里用:**
```python
from app.core.logging_config import get_logger

log = get_logger("app.services.payment")  # 自动缩写为 a.s.payment
log.info("Order %s paid", order_id)        # 日志会自动带上当前请求 ID
```

**JSON 输出示例(生产用):**
```json
{"@timestamp":"2026-07-15T14:23:45.123Z","level":"INFO","pid":12345,"request_id":"req-a1b2c3d4","user":"-","logger":"app.api.items","message":"Item created"}
```

---

### 5️⃣ 定时任务(APScheduler + `@scheduled` 装饰器)

类似 Spring `@Scheduled`,在 `app/tasks/` 下任意 `.py` 文件里用装饰器注册即可,**应用启动时自动扫描**。

**用法:**
```python
# app/tasks/my_tasks.py
from app.core.scheduler import scheduled

@scheduled(cron="0 0 * * *")          # 每天 0 点(类 Spring cron)
async def daily_report():
    """生成日报"""
    ...

@scheduled(seconds=300)               # 每 5 分钟(固定间隔)
async def sync_data():
    ...

@scheduled(minutes=10)                # 语义糖
def check_health():                   # 同步函数自动丢线程池
    ...
```

**支持的参数:**
| 参数 | 说明 |
|---|---|
| `cron` | cron 表达式,如 `"0 0 * * *"` / `"*/30 * * * *"` |
| `seconds` / `minutes` / `hours` | 固定间隔(任选) |
| `coalesce` | 错过的多次执行合并为一次(默认 true) |
| `max_instances` | 同一任务最大并发实例(默认 1,防重入) |
| `misfire_grace_time` | 任务超期多久内仍可执行(秒) |

**任务管理 endpoint:**
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/tasks` | 列出所有任务及下次执行时间 |
| POST | `/api/v1/tasks/{id}/run` | 手动触发一次 |
| POST | `/api/v1/tasks/{id}/pause` | 暂停 |
| POST | `/api/v1/tasks/{id}/resume` | 恢复 |

**配置(`config.yaml` 的 `scheduler:` 段):**
```yaml
scheduler:
  timezone: "Asia/Shanghai"
  coalesce: true
  max_instances: 1
  misfire_grace_time: 60
```

**新增任务步骤:**
1. 在 `app/tasks/` 下新建 `.py` 文件(如 `order_tasks.py`)
2. 用 `@scheduled(cron=...)` 装饰函数
3. 重启应用——自动扫描注册,无需额外配置

---

### 6️⃣ 数据库迁移(Alembic)

类似 Java 的 Flyway/Liquibase。**数据源从 `config.yaml` 读取**(不在 alembic.ini 里配 URL),复用密码加密。

**常用命令:**
```bash
# 安装同步驱动(alembic 走同步路径,首次需要)
.venv\Scripts\python.exe -m pip install psycopg2-binary pymysql

# 1) 自动生成迁移(检测 model 变化,生成迁移文件)
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "add user table"

# 2) 执行迁移到最新版本
.venv\Scripts\python.exe -m alembic upgrade head

# 3) 回滚一个版本
.venv\Scripts\python.exe -m alembic downgrade -1

# 4) 查看当前版本
.venv\Scripts\python.exe -m alembic current

# 5) 查看历史
.venv\Scripts\python.exe -m alembic history
```

**切换迁移的数据源**(默认 `postgres_primary`):
```bash
# 临时迁移到 MySQL
.venv\Scripts\python.exe -m alembic -x datasource=mysql_business upgrade head
```

**新增表/字段的完整流程:**
1. 在 `app/models/` 定义 ORM 模型(如 `user.py`)
2. **在 `app/models/__init__.py` 里导入**(`from app.models.user import User`)—— 否则 autogenerate 检测不到
3. `alembic revision --autogenerate -m "add user table"`
4. 检查生成的迁移文件(`migrations/versions/` 下)
5. `alembic upgrade head`

> ⚠️ **重要**:第 2 步的导入是必须的。Python 的 ORM 模型需要被显式导入才会注册到 `Base.metadata`,autogenerate 才能检测到。

**目录结构:**
```
migrations/
├── env.py                   # 运行环境(从 settings 读数据源,自动转换 async→sync 驱动)
├── script.py.mako           # 迁移文件模板
└── versions/                # 迁移文件(按时间顺序)
    └── 9d35bb4a1977_create_items_table.py
alembic.ini                  # 配置(URL 留空,由 env.py 注入)
```

---

## 配置参考(`config/config.yaml`)

```yaml
app:
  name: "fastapi-demo"
  env: "dev"
  debug: true
  api_prefix: "/api/v1"

# 密码加密(AES-256-GCM)。留空=不启用加密。
# 生成 key:python -m app.core.crypto genkey
crypto:
  key: ""

# 数据源:默认留空,不配也能启动(跳过 DB 相关功能)。
# 配在 config/local.yaml 里(参考 local.yaml.example)。
datasources: {}

# 数据源示例(放进 local.yaml):支持分段配置 + 密码加密
# datasources:
#   postgres_primary:
#     type: postgresql
#     host: 127.0.0.1
#     port: 5432
#     username: postgres
#     password: "enc:gKR8Y2..."     # enc: = 密文;无前缀 = 明文
#     database: app
#     pool_size: 10
#     max_overflow: 20

llm:
  default_provider: deepseek              # 默认 provider
  providers:                              # 多个供应商,任意 OpenAI 兼容端点
    deepseek:
      base_url: "https://api.deepseek.com/v1"
      api_key: "enc:..."
      model: "deepseek-chat"
    qwen:
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
      api_key: "sk-..."
      model: "qwen-plus"
```

### 🔐 密码加密

数据库密码、API key 等敏感字段支持 AES-256-GCM 加密后填入配置。

**工作流程:**
1. 生成密钥(只需一次):`python -m app.core.crypto genkey` → 输出 base64 字符串
2. 把密钥填入 `config.yaml` 的 `crypto.key`
3. 加密真实密码:`python -m app.core.crypto encrypt "你的明文密码"` → 输出 `enc:xxx`
4. 把 `enc:xxx` 填入配置的 password / api_key 字段
5. 应用启动时自动解密,拼接成最终 DSN

**字段设计:** 任何字符串字段都能用 `enc:` 前缀标识密文,加载时自动解密。无前缀 = 明文原样使用(开发环境方便)。所以同一字段本地可填明文 `postgres`、生产填 `enc:gKR8Y2...`。

**安全性说明:** 密钥写在 config.yaml(符合"不用环境变量"的要求)。这能防"一眼看穿"和部分泄露场景;若密钥与密文同文件被整体窃取,理论上仍可解密。如需更高安全性,可后续把密钥单独管理。

### 📊 数据源分段配置

支持两种配置方式:

```yaml
# 方式 1:分段配置(推荐,密码可加密)
postgres_primary:
  type: postgresql
  host: 127.0.0.1
  port: 5432
  username: postgres
  password: "enc:..."          # 自动 URL encode 后拼进 DSN
  database: app

# 方式 2:整串 DSN(简单,但密码得是明文)
postgres_primary:
  type: postgresql
  dsn: "postgresql+asyncpg://user:pass@host:5432/db"
```

分段配置的优势:密码字段支持 `enc:` 加密、特殊字符自动 URL encode、可读性好。

### ✅ 不配数据库也能启动

`config.yaml` 的 `datasources:` 默认为空 `{}`。应用启动时会跳过所有未配置的数据源(`is_configured()` 检查),不报错。需要用到 DB 的 endpoint 在请求时才会报"数据源未配置"的运行时错误,不影响应用启动。

---

## 测试

```bash
.venv\Scripts\python.exe -m pip install aiosqlite   # 测试用 in-memory SQLite
pytest -v
```

`tests/smoke_test.py` 端到端验证:健康检查、公共接口、`@require_auth` 在全局关闭时仍生效、`@use_datasource` 装饰器注入、Annotated 别名注入、Redis 别名注入、装饰器签名正确性。

测试通过 `dependency_overrides` 替换数据源,无需真实 MySQL/PostgreSQL/Redis。

`tests/test_prompt_http.py` 验证 Prompt 加载/渲染(三种格式 + 变量校验 + 缓存)以及 HTTP 客户端的 GET/POST/PUT/PATCH/DELETE(用 `httpx.MockTransport` 模拟,不联网)。

---

## 🐳 Docker 部署

提供完整的 Docker 化方案:多阶段构建的 `Dockerfile`、`docker-compose.yml` 一键全栈、配置通过卷挂载(改配置无需重打镜像)。

### 方式一:本地一键全栈(推荐用于开发)

```bash
# 1) 准备运行时配置(数据源指向 compose 里的服务名)
cp docker/local.yaml.example config/local.yaml

# 2) 一键起 app + PostgreSQL + MySQL + Redis
docker compose up -d --build

# 3) 查看日志 / 停止
docker compose logs -f app
docker compose down           # 停止(保留数据)
docker compose down -v        # 停止并删除数据卷
```

启动后:
- API:http://localhost:8000/docs
- 容器内 app 通过 docker 网络用服务名连数据源(`postgres:5432` / `mysql:3306` / `redis:6379`)

### 方式二:构建镜像 + 导出 tar(目标机器部署)

这是"在 A 机构建、拷到 B 机运行"的标准流程。

```bash
# 1) 构建镜像
docker build -t fastapi-demo:latest .

# 2) 导出为 tar 文件(约 200~400MB,取决于依赖)
docker save fastapi-demo:latest -o fastapi-demo.tar
# 可选:压缩
gzip fastapi-demo.tar          # -> fastapi-demo.tar.gz

# 3) 拷贝到目标机器(U盘 / scp / 内网传输),然后加载
docker load -i fastapi-demo.tar
# 或解压后加载:gzip -d fastapi-demo.tar.gz && docker load -i fastapi-demo.tar

# 4) 运行(在目标机器准备 config/local.yaml 后)
docker run --rm -d \
  --name fastapi-demo \
  -p 8000:8000 \
  -v $(pwd)/config:/app/config:ro \
  -v $(pwd)/prompts:/app/prompts:ro \
  fastapi-demo:latest
```

> **关键**:`-v $(pwd)/config:/app/config:ro` 把宿主机的 `config/` 目录挂进容器。
> 这样修改 `config/local.yaml`(改数据库密码、改模型等)**不需要重新构建镜像**,重启容器即可:
> `docker restart fastapi-demo`

### 镜像特性

| 特性 | 说明 |
|---|---|
| **多阶段构建** | builder 阶段装依赖+编译 C 扩展,runtime 阶段只拷 venv,镜像更小 |
| **基础镜像** | `python:3.11-slim`(比 alpine 兼容性好,asyncpg/bcrypt 等无需重编译) |
| **非 root 用户** | 以 `appuser`(uid 1001)运行,符合安全基线 |
| **依赖管理** | 用 `uv` 安装(官方 skill 推荐,比 pip 快 10×) |
| **健康检查** | 内置 `HEALTHCHECK`,自动探 `/health` |
| **不带 dev 依赖** | 镜像里没有 pytest/ruff/mypy,更小更安全 |
| **配置外挂** | `config/` 和 `prompts/` 通过卷挂载,镜像与配置解耦 |

### 常用运维命令

```bash
# 查看容器状态
docker ps
docker inspect fastapi-demo --format '{{.State.Health.Status}}'

# 进入容器排查
docker exec -it fastapi-demo /bin/bash

# 查看实时日志
docker logs -f fastapi-demo

# 更新代码后重建(配置改动不需要这步)
docker compose up -d --build app

# 调整 worker 数(改 docker-compose.yml 的 command)
#   command: fastapi run --host 0.0.0.0 --port 8000 --workers 4
```

### 镜像内目录结构(参考)

```
/app/
├── app/              # 应用代码(构建时拷入)
├── config/           # 默认配置(可被 -v 覆盖)
│   ├── config.yaml
│   └── local.yaml.example
├── prompts/          # 提示词(可被 -v 覆盖)
└── /opt/venv/        # 独立虚拟环境(从 builder 拷入)
```

### 环境变量

本项目**不依赖任何业务环境变量**。配置全部走 `config/*.yaml`(见上文加载规则)。

| 变量 | 默认 | 作用 |
|---|---|---|
| `TZ` | UTC | 时区,如 `Asia/Shanghai`(仅 Docker 运行时设) |
| `PYTHONUNBUFFERED=1` | 固定 | 日志实时输出,不缓冲 |

---

## 🌐 HTTP 客户端工具类

封装在 `app/services/http_client.py`,基于 `httpx.AsyncClient`,在 lifespan 启动一次,进程内复用连接池。

### 基本用法

```python
from app.services.http_client import http_client

# GET(支持 params / headers / timeout 等 httpx 全部 kwargs)
resp = await http_client.get("https://api.example.com/users/1", params={"fields": "full"})
data = resp.json()                       # 解析 JSON
text = resp.text                         # 原始文本
if not resp.ok:                          # resp.ok / resp.status_code
    resp.raise_for_status()              # 4xx/5xx 抛 HTTPStatusError

# POST / PUT / PATCH / DELETE 同样的签名
resp = await http_client.post(url, json={"k": 1}, headers={"X-Trace": "abc"})
await http_client.put(url, json=payload)
await http_client.patch(url, json={"field": "new"})
await http_client.delete(url)

# 大文件流式下载
async with http_client.stream("GET", big_url) as r:
    async for chunk in r.aiter_bytes():
        ...
```

### 配置(在 `config.yaml` 的 `http:` 段)

```yaml
http:
  timeout: 30.0                    # 默认超时(秒)
  max_connections: 100             # 连接池上限
  max_keepalive_connections: 20
  verify: true                     # 关闭可绕过自签证书(仅 dev)
  default_headers:                 # 附加到每个请求
    User-Agent: "fastapi-demo/0.1"
    Authorization: "Bearer xxx"    # 例如统一鉴权头
```

> 每次调用传的 `headers` / `timeout` 会覆盖默认值。

### 在 endpoint 里用

参考 `app/api/v1/endpoints/examples.py`,提供 `/examples/http/get` `/examples/http/post` `/examples/http/put` 三个演示接口。

---

## 📜 Prompt 系统

提示词作为**独立文件**存放在 `prompts/` 目录,**文件名即 prompt 名**。

### 支持的三种格式

| 后缀 | 说明 |
|---|---|
| `.yaml` / `.yml` | **结构化**(推荐):可同时定义 `system` / `user` / 变量声明 / LLM 参数 |
| `.txt` | 纯文本(只作 user prompt),可选择性带 YAML front-matter |
| `.j2` / `.jinja2` | Jinja2 模板,可写 `{% if %}` 等控制流 |

### YAML 格式 Schema(`prompts/translate.yaml` 示例)

```yaml
name: translate                 # 可省,默认为文件名
description: 翻译助手
system: |
  你是一名专业翻译。目标语言: {{ target_lang }}
user: |
  请把下面的内容翻译为 {{ target_lang }}:
  {{ source }}
temperature: 0.3                # 该 prompt 调用 LLM 时的默认温度
model: null                     # null = 用 config.yaml 里的 llm.model
max_tokens: 2048
variables:                      # 变量声明:用于校验、文档、默认值
  - name: source
    description: 要翻译的原文
    required: true
  - name: target_lang
    description: 目标语言
    required: true
    default: 中文
```

### 加载与渲染(`app/core/prompt.py`)

```python
from app.core.prompt import load_prompt, render_prompt, list_prompts

# 1) 查看可用 prompt
print(list_prompts())                # ['joke', 'summarize', 'translate']

# 2) 加载(带缓存)
tpl = load_prompt("translate")       # 返回 PromptTemplate
print([v.name for v in tpl.variables])

# 3) 渲染(填入变量)
rendered = render_prompt("translate", source="Hello", target_lang="中文")
print(rendered.system)
print(rendered.user)
print(rendered.to_messages())        # -> [{"role": "system", ...}, {"role": "user", ...}]

# 缺失必填变量会抛 ValueError:
# render_prompt("translate", target_lang="中文")  # 缺 source -> 报错
```

### 直接喂给 LLM(`llm.complete_prompt`)

把"加载 + 渲染 + 调用 LLM"一行搞定(LangChain 底层):

```python
from app.services.llm import llm

# 非流式
text = await llm.complete_prompt(
    "translate",
    source="Hello",
    target_lang="中文",
)

# 流式(返回 async iterator)
async for chunk in llm.complete_prompt_stream("joke", topic="程序员"):
    print(chunk, end="", flush=True)

# 覆盖 prompt 文件里的 model/temperature
text = await llm.complete_prompt(
    "translate",
    source="Hello", target_lang="中文",
    overrides={"temperature": 0.0, "model": "deepseek-chat"},
)

# LCEL 多步骤编排(见上面「3️⃣ LLM」章节)
pipe = llm.chain("summarize", output_key="source") | llm.chain("translate")
```

### 调试 endpoint

| 路径 | 用途 |
|---|---|
| `GET  /api/v1/examples/prompts` | 列出所有 prompt |
| `GET  /api/v1/examples/prompts/{name}` | 查看某个 prompt 的结构 |
| `POST /api/v1/examples/prompts/{name}/render` | 渲染但不调 LLM(预览) |
| `POST /api/v1/examples/llm/translate` | 用 translate.yaml + LLM 翻译 |
| `GET  /api/v1/examples/llm/joke/stream` | 用 joke.txt + SSE 流式(需 Token) |

### 新增 prompt 的步骤

1. 在 `prompts/` 下新建文件,例如 `prompts/extract_keywords.yaml`
2. 编辑内容(声明变量 + 写 system/user)
3. 调用:`render_prompt("extract_keywords", text="...")` 或 `llm.complete_prompt("extract_keywords", text="...")`

无需重启应用——加载器会在第一次访问时读取(之后走缓存;调用时传 `refresh=True` 可强制重读)。

---

## 技术栈

- **FastAPI** ≥ 0.120(官方 skill 推荐用法:`Annotated`、`EventSourceResponse`、`fastapi` CLI)
- **SQLAlchemy 2.x async** + `asyncpg` / `aiomysql`
- **redis[hiredis]** 的 `redis.asyncio`
- **LangChain + langchain-openai**(LLM,ChatOpenAI 连 NewAPI,兼容国产模型)
- **httpx**(出站 HTTP,官方 skill 推荐替代 requests)
- **Jinja2**(Prompt 模板渲染)
- **Pydantic v2** + `pydantic-settings`
- **uv** / **Ruff** / **pytest**(开发工具链)

---

## 已知 TODO(留给你按需扩展)

- [ ] 把 `auth/token` 的密码校验换成查数据库
- [ ] 引入 Alembic 做迁移(可加 `alembic` 依赖与 `migrations/` 目录)
- [ ] 在 `services/` 下补充具体业务服务
- [ ] 增加 Prometheus / OpenTelemetry 可观测
