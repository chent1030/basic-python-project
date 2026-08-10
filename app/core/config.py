"""Configuration loader.

- Reads YAML only (no .env, no env vars).
- 始终加载 config/config.yaml;若 config/local.yaml 存在则深合并覆盖。
- 加载阶段自动:
    1) 用 crypto.key 解密所有 `enc:` 前缀字段(密码、API key)
    2) 把分段 host/port/username/password/database 拼接成 dsn
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


# ---------- nested config models ---------------------------------------------
class AppConfig(BaseModel):
    name: str = "fastapi-demo"
    env: str = "dev"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    api_prefix: str = "/api/v1"


class AuthConfig(BaseModel):
    enabled: bool = False  # global default: OFF
    algorithm: str = "HS256"
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7


class DatasourceConfig(BaseModel):
    """One datasource entry. `type` decides which engine is built.

    两种配置方式(二选一):
    1. 分段配置(推荐,密码可加密):
         type: postgresql
         host: 127.0.0.1
         port: 5432
         username: postgres
         password: "enc:gKR8Y2..."    # enc: 前缀=密文;无前缀=明文
         database: app
       加载时自动拼接成 DSN,密码字段自动解密。

    2. 整串 DSN(老方式,简单):
         type: postgresql
         dsn: "postgresql+asyncpg://user:pass@host:5432/db"
       注意:整串 DSN 不做字段级解密,密码得是明文。

    池/超时参数对照 Spring Boot HikariCP:
      pool_size + max_overflow ≈ maximumPoolSize (实际上限)
      pool_timeout        ≈ connectionTimeout (借连接等待时长)
      pool_recycle        ≈ maxLifetime (连接最大存活/回收)
      pool_pre_ping       ≈ connectionTestQuery (借出前探活)
      connect_timeout     驱动层 TCP 连接超时
      statement_timeout   PostgreSQL 语句级超时(毫秒);MySQL 不支持
    """

    type: str  # postgresql | mysql | redis
    dsn: str = ""                     # 整串;留空则用下面的分段字段拼

    # ---- 分段连接信息(优先级低于 dsn;两者都没填=不创建该数据源)----
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""                # 支持 enc: 前缀密文,加载时自动解密
    database: str = ""                # SQL 库名 / Redis db 号

    # ---- 连接池大小(HikariCP: minimumIdle + maximumPoolSize)----
    pool_size: int = 10
    max_overflow: int = 20

    # ---- 超时(HikariCP: connectionTimeout / maxLifetime)----
    pool_timeout: float = 30.0
    pool_recycle: int = 3600
    pool_pre_ping: bool = True

    # ---- 驱动层超时 ----
    connect_timeout: int = 10
    statement_timeout: int | None = None  # 仅 PostgreSQL 生效(毫秒)

    # ---- 调试 ----
    echo: bool = False

    # ---- Redis 专用 ----
    max_connections: int = 20
    decode_responses: bool = True
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0
    health_check_interval: int = 30

    def is_configured(self) -> bool:
        """该数据源是否真的需要创建。空配置(没填 dsn 也没填 host)返回 False。"""
        return bool(self.dsn) or bool(self.host)

    def build_dsn(self) -> str:
        """根据分段字段拼接最终 DSN;若已有 dsn 则直接返回。

        分段优先级:如果 dsn 字段已填(整串),直接用 dsn;否则用 host/port/...
        拼出来的 DSN 供 SQLAlchemy / Redis 客户端使用。
        """
        if self.dsn:
            return self.dsn
        return _build_dsn_from_parts(self)

    @model_validator(mode="after")
    def _normalize_dsn(self) -> DatasourceConfig:
        """如果 dsn 字段为空但 host 已填,把分段拼接的结果回写到 dsn。

        这样 datasource.py 永远只读 self.dsn,不用关心来源。
        """
        if not self.dsn and self.host:
            self.dsn = _build_dsn_from_parts(self)
        return self


def _build_dsn_from_parts(cfg: DatasourceConfig) -> str:
    """根据 type + host/port/user/password/database 拼接 DSN。"""
    t = cfg.type
    auth = ""
    if cfg.username:
        # 密码可能含特殊字符,做 URL encode;但 password 此时已是明文(已解密)
        from urllib.parse import quote_plus

        if cfg.password:
            auth = f"{quote_plus(cfg.username)}:{quote_plus(cfg.password)}@"
        else:
            auth = f"{quote_plus(cfg.username)}@"

    host_port = cfg.host
    if cfg.port:
        host_port = f"{cfg.host}:{cfg.port}"

    if t == "postgresql":
        return f"postgresql+asyncpg://{auth}{host_port}/{cfg.database}"
    if t == "mysql":
        return f"mysql+aiomysql://{auth}{host_port}/{cfg.database}?charset=utf8mb4"
    if t == "redis":
        db = cfg.database or "0"
        return f"redis://{auth}{host_port}/{db}"
    raise ValueError(f"无法为未知数据源类型拼 DSN: {t}")


class CryptoConfig(BaseModel):
    """密码加解密配置。

    key: base64 编码的 32 字节 AES-256 密钥。
         用 `python -m app.core.crypto genkey` 生成。
         config.yaml 里所有 `enc:` 前缀字段都会用这个 key 解密。
    """

    key: str = ""


class LLMProviderConfig(BaseModel):
    """单个 LLM 供应商配置(一个 NewAPI 实例 + 默认 model)。

    支持任意 OpenAI 兼容端点:DeepSeek / Qwen / GLM / Kimi / 真 OpenAI 等。
    password/api_key 支持 `enc:` 加密(跟数据源密码同一套机制)。

    只发送国产模型都支持的最小参数集。NewAPI 会拒绝 OpenAI 专属参数
    (reasoning_effort / service_tier / logprobs 等),不要加。
    """

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""                 # 支持 enc: 加密
    model: str = "gpt-4o-mini"        # 该 provider 的默认 model
    temperature: float = 0.7
    timeout: int = 60
    max_tokens: int = 0               # 0 = 不发送该参数(国产模型更安全)
    max_retries: int = 2


class LLMConfig(BaseModel):
    """LLM 配置 —— 支持多个 provider(多个 NewAPI 地址),调用时切换。

    config.yaml 示例:
        llm:
          default_provider: deepseek
          providers:
            deepseek:
              base_url: "https://api.deepseek.com/v1"
              api_key: "enc:xxx"
              model: "deepseek-chat"
            qwen:
              base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
              api_key: "sk-xxx"
              model: "qwen-plus"

    调用时不传 provider 就用 default_provider。
    """

    default_provider: str = "default"
    providers: dict[str, LLMProviderConfig] = Field(default_factory=dict)


class HttpConfig(BaseModel):
    """Shared defaults for the outbound HTTP client (`HttpClient`).

    Per-request kwargs always override these. `default_headers` are merged
    onto every outgoing request; useful for auth tokens / tracing ids.
    """

    timeout: float = 30.0
    max_connections: int = 100
    max_keepalive_connections: int = 20
    default_headers: dict[str, str] = Field(default_factory=dict)
    verify: bool = True


class PromptsConfig(BaseModel):
    """Where prompt files live + caching behaviour."""

    dir: str = "prompts"           # relative to project root
    default_format: str = "yaml"   # yaml | txt | j2 | jinja2
    cache: bool = True             # cache rendered/loaded prompts in-memory


class LoggingConfig(BaseModel):
    """日志配置 — Spring Boot 风格 + 按天滚动归档。

    - format:        console(彩色)| json(单行 JSON,供 ELK/Loki)
    - color:         console 模式是否彩色
    - file:          主日志文件路径(同时输出到控制台和文件)
    - rotation:      滚动周期(对应 Logback RollingFileAppender)
                     daily(每天,默认)| hourly | midnight
    - backup_count:  保留多少个历史归档文件(超期自动删除),默认 30
    - max_file_size: 单文件大小上限(MB);超过则按大小滚动。
                     设为 0 表示不按大小滚动(仅按时间)。
    """

    level: str = "INFO"
    format: str = "console"               # console | json
    color: bool = True                    # 仅 console 模式生效
    file: str | None = None               # 例如 "logs/app.log"
    rotation: str = "daily"               # daily | hourly | midnight
    backup_count: int = 30                # 保留归档文件数
    max_file_size: int = 0                # MB;0 = 不按大小滚动


class SchedulerConfig(BaseModel):
    """定时任务配置。

    - timezone:          时区(cron 表达式按此时区解释)
    - coalesce:          全局默认:错过的多次执行合并为一次
    - max_instances:     全局默认:同一任务最大并发实例(防重入)
    - misfire_grace_time:全局默认:任务超期多久内仍可执行(秒)
    """

    timezone: str = "Asia/Shanghai"
    coalesce: bool = True
    max_instances: int = 1
    misfire_grace_time: int = 60


class AlembicConfig(BaseModel):
    """数据库迁移配置。

    - default_datasource: alembic upgrade/autogenerate 默认操作哪个数据源
    """

    default_datasource: str = "postgres_primary"


# ---------------------------------------------------------------------
# AI Agent 框架 —— 全局配置(单 agent 配置在 app/ai/agents/<name>/config.yml)
# ---------------------------------------------------------------------
class PersistentMemoryConfig(BaseModel):
    """持久记忆(跨会话事实/偏好)—— 独立向量库,与业务库隔离。

    与会话记忆(session,走业务库)分开:持久记忆是长期事实,需要向量召回,
    存在独立的向量库数据源里,避免污染业务表。
    未启用(enabled=false)时,persistent_memory 中间件退化为 no-op。
    """

    enabled: bool = False
    datasource: str = ""              # 向量库数据源名(在 datasources 里配,通常是独立 PG+pgvector)
    table: str = "agent_memories"
    embedding_provider: str = ""      # 用哪个 LLM provider 出 embedding(复用 llm.providers)
    embedding_model: str = ""         # embedding 模型名
    top_k: int = 5                    # 召回条数


class ExternalMemoryConfig(BaseModel):
    """外部记忆(系统外知识源)—— 第一期只支持外部 API 召回。

    中间件在 agent 调用前 POST 该 url(query 放 body),取回的知识片段注入上下文。
    pgvector 知识库 / 业务 DB 查询等其它外部记忆形式留接口后续扩展。
    未启用(enabled=false)时,external_memory 中间件退化为 no-op。
    """

    enabled: bool = False
    url: str = ""
    method: str = "POST"              # HTTP 方法
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = 30.0


class AgentsConfig(BaseModel):
    """AI Agent 框架全局配置。

    agent 列表是去中心化的:每个 agent 一个独立目录 app/ai/agents/<name>/,
    内含 config.yml 声明该 agent 的拓扑/后端/中间件等。
    本配置只放全局开关 + 共享存储位置 + 中间件后端配置。

    - agents_dir:   agent 配置目录(registry 扫描入口)
    - tools_dir:    全局共享工具目录(每工具一文件,自动发现)
    - session_datasource: 会话记忆存哪个数据源(业务库)
    - runs_table:   运行记录表(每次 trigger/chat 都写,树状 parent_run_id)
    """

    enabled: bool = True
    agents_dir: str = "app/ai/agents"
    tools_dir: str = "app/ai/tools"
    session_datasource: str = "postgres_primary"
    session_table: str = "agent_sessions"
    runs_table: str = "agent_runs"
    persistent_memory: PersistentMemoryConfig = Field(default_factory=PersistentMemoryConfig)
    external_memory: ExternalMemoryConfig = Field(default_factory=ExternalMemoryConfig)


# ---------------------------------------------------------------------
# MinerU OCR 服务配置
# ---------------------------------------------------------------------
class MineruConfig(BaseModel):
    """MinerU OCR HTTP 服务配置。

    MinerU 部署为独立 HTTP 服务,doc_review service 把文件 POST 过去拿 OCR 结果。
    不配(url 空)时,OCR 工具会报错提示未配置。
    """

    url: str = ""                  # MinerU OCR API 地址,如 http://mineru:8000/ocr
    timeout: float = 120.0         # OCR 耗时较长,默认 120s
    api_key: str = ""              # 若 MinerU 需认证


# ---------------------------------------------------------------------
# 文档智能审核配置
# ---------------------------------------------------------------------
class DocReviewConfig(BaseModel):
    """文档审核功能配置。

    doc_review service 编排:OCR → 并行检查(规则+AI) → 汇总报告。
    外部系统传入 entity(业务数据,比对基准)+ url(文档地址)。
    """

    enabled: bool = True
    callback_timeout: float = 30.0    # 审核完成后回调外部系统的超时
    check_timeout: float = 180.0      # 单项检查(agent 调用)的超时


class Settings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    crypto: CryptoConfig = Field(default_factory=CryptoConfig)
    datasources: dict[str, DatasourceConfig] = Field(default_factory=dict)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    alembic: AlembicConfig = Field(default_factory=AlembicConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    mineru: MineruConfig = Field(default_factory=MineruConfig)
    doc_review: DocReviewConfig = Field(default_factory=DocReviewConfig)


# ---------- YAML deep merge ---------------------------------------------------
def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings() -> Settings:
    """加载 config/config.yaml;若 config/local.yaml 存在则深合并覆盖。

    切换环境的方式:在 config/ 下放(或不放)local.yaml。无需任何环境变量。

    加载顺序:
        config.yaml + local.yaml(深合并)
        → 用 crypto.key 解密所有 enc: 前缀字段
        → Settings.model_validate(触发 DatasourceConfig 的 DSN 拼接)
    """
    base = _load_yaml(CONFIG_DIR / "config.yaml")

    # 纯文件驱动:local.yaml 存在就覆盖,不存在就只用 config.yaml。
    local_path = CONFIG_DIR / "local.yaml"
    if local_path.exists():
        base = _deep_merge(base, _load_yaml(local_path))

    # 用 crypto.key 解密所有 enc: 字段(密码、API key 等)
    crypto_key = (base.get("crypto") or {}).get("key") or ""
    if crypto_key:
        base = _decrypt_tree(base, crypto_key)

    return Settings.model_validate(base)


def _decrypt_tree(node: Any, key: str) -> Any:
    """递归遍历配置树,把所有 `enc:` 前缀字符串解密成明文。"""
    from app.core.crypto import maybe_decrypt

    if isinstance(node, dict):
        return {k: _decrypt_tree(v, key) for k, v in node.items()}
    if isinstance(node, list):
        return [_decrypt_tree(v, key) for v in node]
    return maybe_decrypt(node, key)


# Eager singleton — imported across the app.
settings = load_settings()
