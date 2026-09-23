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
    dsn: str = ""  # 整串;留空则用下面的分段字段拼

    # ---- 分段连接信息(优先级低于 dsn;两者都没填=不创建该数据源)----
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""  # 支持 enc: 前缀密文,加载时自动解密
    database: str = ""  # SQL 库名 / Redis db 号

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
    api_key: str = ""  # 支持 enc: 加密
    model: str = "gpt-4o-mini"  # 该 provider 的默认 model
    temperature: float = 0.7
    timeout: int = 60
    max_tokens: int = 0  # 0 = 不发送该参数(国产模型更安全)
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

    dir: str = "prompts"  # relative to project root
    default_format: str = "yaml"  # yaml | txt | j2 | jinja2
    cache: bool = True  # cache rendered/loaded prompts in-memory


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
    format: str = "console"  # console | json
    color: bool = True  # 仅 console 模式生效
    file: str | None = None  # 例如 "logs/app.log"
    rotation: str = "daily"  # daily | hourly | midnight
    backup_count: int = 30  # 保留归档文件数
    max_file_size: int = 0  # MB;0 = 不按大小滚动


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
# Harness 框架配置(session 记忆存哪个数据源)
# ---------------------------------------------------------------------
class HarnessConfig(BaseModel):
    """Harness 框架配置。

    session_datasource: 会话记忆中间件存历史消息的数据源。
    """

    session_datasource: str = "postgres_primary"


# ---------------------------------------------------------------------
# MinerU OCR 服务配置
# ---------------------------------------------------------------------
class MineruConfig(BaseModel):
    """MinerU OCR HTTP 服务配置。

    MinerU 部署为独立 HTTP 服务,doc_review service 把文件 POST 过去拿 OCR 结果。
    不配(url 空)时,OCR 工具会报错提示未配置。
    """

    url: str = ""  # MinerU OCR API 地址,如 http://mineru:8000/ocr
    timeout: float = 120.0  # OCR 耗时较长,默认 120s
    api_key: str = ""  # 若 MinerU 需认证


# ---------------------------------------------------------------------
# 文档智能审核配置
# ---------------------------------------------------------------------
class DocReviewConfig(BaseModel):
    """文档审核功能配置。

    文档加载 → OCR → 事实提取 → 确定性规则检查 → 汇总报告。
    外部系统传入 entity(业务数据,比对基准)+ url(文档地址)。
    """

    enabled: bool = True
    check_timeout: float = 180.0  # 单项检查(LLM 调用)的超时
    max_file_size_mb: int = 30  # 单个待审核文件大小上限


# ---------------------------------------------------------------------
# CPS Agent 初审配置(Java 回调/任务 deadline)
# ---------------------------------------------------------------------
class JavaCallbackConfig(BaseModel):
    """C-02 回调 Java 侧地址。

    - base_url:env CPS_JAVA_CALLBACK_BASE_URL 优先(代码层,见
      app/projects/initial_review/infrastructure/config.py),yaml 为默认值;
    - path:契约固定路径 /api/callbacks/initial-review/result;
    - 技术重试 ≤2 次,30s/60s 退避(设计 §3.1)。
    """

    base_url: str = "http://127.0.0.1:8080"
    path: str = "/api/callbacks/initial-review/result"
    timeout_seconds: float = 10.0
    max_retries: int = 2
    retry_backoff_seconds: list[float] = Field(default_factory=lambda: [30.0, 60.0])


class SimilarityThresholdConfig(BaseModel):
    """A6 雷同性阈值(D-05:W3 前用业务样本校准;默认值为人工抽检估计)。

    - same_*:同单 short_term_measure ↔ long_term_measure(PRD §29.1 强制);
    - history_*:vs 同 issue 历史版本(波次 2 任务书扩展,PRD 未确认——见设计文档);
    - score ≥ fail → FAIL;≥ warn → WARN;否则 PASS。
    """

    same_fail: float = 0.90
    same_warn: float = 0.75
    history_fail: float = 0.85
    history_warn: float = 0.70


class ModelCheckConfig(BaseModel):
    """A7/A8 模型检查配置(qwen 系,OpenAI 兼容模式)。

    - enabled=False 或 provider 未配置 → 检查 SKIPPED(不伪造结论);
      波次 7 起 enabled 支持 None=「未配置」:initial_review 自身未配时按
      默认 True 处理(维持既有契约);room_checks 侧则继续向下回退到自己的
      开关(见 RoomChecksConfig,「全部未配 → 关」);
    - field_budget_seconds:单次模型调用(含一次重试)的预算,90s 可配;
      3 字段 + 1 次视觉 ≈ 4×90s < deadline 480s,Java 接管阈值 10min 不变;
    - max_images_per_side:送入视觉模型的每侧照片上限(防 prompt 膨胀)。
    """

    enabled: bool | None = None  # None=未配置(initial_review 侧默认 True)
    provider: str = "qwen"
    text_model: str = "qwen-plus"
    vision_model: str = "Qwen2.5-VL-7B-Instruct"
    temperature: float = 0.1
    field_budget_seconds: float = 90.0
    max_images_per_side: int = 4


class RustFSConfig(BaseModel):
    """object_key 附件的 RustFS(S3 兼容)读取配置。

    默认值对齐 Java 侧 application.yml 的 cps.storage.*(admin/Ct0520.0402
    为本地开发默认,与 Java 仓一致;生产用 env 覆盖):
    env CPS_STORAGE_ENDPOINT / CPS_STORAGE_ACCESS_KEY / CPS_STORAGE_SECRET_KEY
    / CPS_STORAGE_BUCKET 优先于 yaml(见 infrastructure/config.py)。
    """

    endpoint: str = "http://127.0.0.1:9000"
    access_key: str = "admin"
    secret_key: str = "Ct0520.0402"
    bucket: str = "cps-attachments"


class InitialReviewConfig(BaseModel):
    """初审任务执行配置。

    - deadline_seconds:整任务 wall-clock 上限,480s=8 分钟(<10min 留 Java 接管余量,设计 §3.1)。
    """

    deadline_seconds: float = 480.0
    similarity: SimilarityThresholdConfig = Field(default_factory=SimilarityThresholdConfig)
    model_check: ModelCheckConfig = Field(default_factory=ModelCheckConfig)
    rustfs: RustFSConfig = Field(default_factory=RustFSConfig)


class RoomChecksVisionConfig(BaseModel):
    """C-04 辅房点检视觉判定独立开关(波次 7,清单⑥)。

    - enabled=None(未配置)→ 不独立决策,回退旧开关
      ``initial_review.model_check.enabled``(老配置仍生效,同开同关时代部署不受影响);
    - 显式 true/false → 仅控制 C-04 视觉判定,不影响 C-01 初审;
    - env ``CPS_ROOM_CHECKS_VISION_ENABLED`` 优先于 yaml(部署层覆盖)。
    """

    enabled: bool | None = None  # None=未配置→回退旧开关


class RoomChecksConfig(BaseModel):
    """Settings.cps_agent.room_checks 节(C-04 辅房点检)。

    模型参数(provider/vision_model/预算等)沿用 initial_review.model_check,
    本节点只承载 C-04 专属配置——当前仅视觉判定独立开关。
    """

    vision: RoomChecksVisionConfig = Field(default_factory=RoomChecksVisionConfig)


class AsrSwitchConfig(BaseModel):
    """C-06 语音转写独立开关(波次 7,清单⑥)。

    - enabled=None(未配置)→ 回退旧开关 ``speech.enabled``(老配置仍生效);
    - 显式 true/false → 仅控制 C-06 ASR,与 C-01/C-04 互不影响;
    - env ``CPS_SPEECH_ASR_ENABLED`` 优先于 yaml。
    """

    enabled: bool | None = None  # None=未配置→回退 speech.enabled


class SpeechConfig(BaseModel):
    """F1 语音转写配置(契约 C-06 speech-to-text,D-03 三字段)。

    - ASR 走 OpenAI 兼容 chat/completions 的 ``input_audio`` 同步转写
      (DashScope qwen3-asr-flash,音频 ≤10MB/≤5min,与表单语音备注场景匹配);
    - provider 引用 ``llm.providers`` 的 key 取 base_url/api_key;base_url/api_key
      可在本节点覆盖(ASR 专用网关);未配置 → SKIPPED 不伪造;
    - language 空=自动检测(最小参数集,兼容 NewAPI 网关);非空传 asr_options.language;
    - enabled 为旧开关(波次 7 前唯一入口):None=未配置;新开关 ``asr.enabled``
      未配置时回退到本值;两者都未配 → 关闭(SKIPPED,不伪造)。
    """

    enabled: bool | None = None  # 旧开关;None=未配置→回退链见 asr.enabled
    asr: AsrSwitchConfig = Field(default_factory=AsrSwitchConfig)
    provider: str = "qwen"  # 对应 llm.providers 的 key(local.yaml 配置)
    model: str = "qwen3-asr-flash"  # 备选 qwen-audio-3.0-asr-flash(同为同步 OpenAI 兼容)
    base_url: str = ""  # 可选覆盖;空 → llm.providers[provider].base_url
    api_key: str = ""  # 可选覆盖;空 → llm.providers[provider].api_key
    language: str = ""  # 空=自动检测;如 "zh"
    timeout_seconds: float = 60.0  # 单次转写预算(ASR 同步调用,含网关往返)
    max_audio_bytes: int = 10 * 1024 * 1024  # 模型上限 10MB(解码后原始字节)


class CpsAgentConfig(BaseModel):
    """Settings.cps_agent 节。"""

    initial_review: InitialReviewConfig = Field(default_factory=InitialReviewConfig)
    java_callback: JavaCallbackConfig = Field(default_factory=JavaCallbackConfig)
    room_checks: RoomChecksConfig = Field(default_factory=RoomChecksConfig)
    speech: SpeechConfig = Field(default_factory=SpeechConfig)


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
    mineru: MineruConfig = Field(default_factory=MineruConfig)
    doc_review: DocReviewConfig = Field(default_factory=DocReviewConfig)
    harness: HarnessConfig = Field(default_factory=HarnessConfig)
    cps_agent: CpsAgentConfig = Field(default_factory=CpsAgentConfig)


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
