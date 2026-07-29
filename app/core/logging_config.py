"""Standardized logging — Spring Boot style.

Spring Boot default log format:
    2024-01-15 10:23:45.123  INFO 12345 [http-nio-8080-exec-1] c.e.d.UserService   : User login

本项目实现:
- 时间到毫秒 + 级别固定 5 字符宽 + PID + 请求/线程 + logger 名缩写 + 消息
- 控制台彩色输出(开发)
- JSON 格式输出(生产,便于 ELK/Loki 采集)
- 请求 ID 中间件:每个请求注入唯一 trace,贯穿整个请求链路(异步安全)

组件:
- SpringBootLogFormatter   控制台/文件彩色格式化
- JsonLogFormatter         JSON 一行一条
- request_id_ctx           ContextVar,异步安全持有当前请求 ID
- RequestIdMiddleware      FastAPI 中间件,生成/透传 request_id
- abbreviate_name          把 app.services.llm -> a.s.llm(类 Spring c.e.d.X)
- setup_logging            按 config 初始化 root logger
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import LoggingConfig, settings

# ---------------------------------------------------------- request id MDC
# ContextVar 是 async 安全的:每个请求有独立的 request_id,互不干扰。
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
# 可扩展的用户上下文(认证后填充)
user_ctx: ContextVar[str] = ContextVar("user", default="-")


# ---------------------------------------------------------- logger name abbrev
def abbreviate_name(name: str) -> str:
    """把 logger 名按 Spring 风格缩写。

    app.services.llm        -> a.s.llm
    app.api.v1.items        -> a.a.v.items     (最后一段保留全名)
    app                     -> app
    """
    if not name:
        return name
    parts = name.split(".")
    if len(parts) <= 1:
        return name
    # 前 n-1 段各取首字母,最后一段保留全名
    head = ".".join(p[0] for p in parts[:-1])
    return f"{head}.{parts[-1]}"


# ---------------------------------------------------------- color codes
class _Color:
    RESET = "\033[0m"
    # 级别配色(参考 Spring Boot 默认)
    DEBUG = "\033[37m"     # 灰白
    INFO = "\033[32m"      # 绿
    WARN = "\033[33m"      # 黄
    ERROR = "\033[31m"     # 红
    CRITICAL = "\033[1;31m"  # 加粗红
    CYAN = "\033[36m"      # logger 名 / request_id 用
    MAGENTA = "\033[35m"   # PID / 线程

    LEVEL_MAP = {
        "DEBUG": DEBUG,
        "INFO": INFO,
        "WARN": WARN,
        "WARNING": WARN,
        "ERROR": ERROR,
        "CRITICAL": CRITICAL,
    }


# ---------------------------------------------------------- Spring Boot Formatter
class SpringBootLogFormatter(logging.Formatter):
    """Spring Boot 风格控制台格式化器。

    输出示例(彩色):
      2026-07-15 14:23:45.123  INFO 12345 [req-a1b2c3] a.s.llm         : LLM ready
    """

    def __init__(self, *, color: bool = True) -> None:
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        # 时间到毫秒
        created = self.formatTime(record, datefmt="%Y-%m-%d %H:%M:%S")
        msecs = f"{record.msecs:03.0f}"
        timestamp = f"{created}.{msecs}"

        level = record.levelname
        pid = record.process
        # 请求/线程标识:优先 request_id(ASGI 单线程下更有意义),否则 threadName
        rid = request_id_ctx.get()
        thread = rid if rid != "-" else record.threadName

        logger_name = abbreviate_name(record.name)
        msg = record.getMessage()

        # 异常信息(像 Spring Boot 那样换行追加)
        if record.exc_info:
            import traceback

            msg = msg + "\n" + "".join(traceback.format_exception(*record.exc_info))

        if self.color:
            color = _Color.LEVEL_MAP.get(level, _Color.CYAN)
            return (
                f"{_Color.CYAN}{timestamp}{_Color.RESET}  "
                f"{color}{level:<5}{_Color.RESET} "
                f"{_Color.MAGENTA}{pid:<5}{_Color.RESET} "
                f"[{_Color.MAGENTA}{thread}{_Color.RESET}] "
                f"{_Color.CYAN}{logger_name:<20}{_Color.RESET} "
                f": {msg}"
            )
        # 无色版(写文件用)
        return f"{timestamp}  {level:<5} {pid:<5} [{thread}] {logger_name:<20} : {msg}"


# ---------------------------------------------------------- JSON Formatter (生产)
class JsonLogFormatter(logging.Formatter):
    """JSON 一行一条,便于 ELK / Loki / CloudWatch 采集。

    字段:timestamp, level, pid, request_id, logger, message, [exception]
    """

    # 跟 ELK 习惯一致的字段名
    def format(self, record: logging.LogRecord) -> str:
        created = self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S")
        msecs = f"{record.msecs:03.0f}"
        payload = {
            "@timestamp": f"{created}.{msecs}Z",
            "level": record.levelname,
            "pid": record.process,
            "request_id": request_id_ctx.get(),
            "user": user_ctx.get(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------- Request ID Middleware
class RequestIdMiddleware(BaseHTTPMiddleware):
    """为每个请求生成/透传唯一 request_id,写入 ContextVar + 响应头。

    - 客户端可透传:请求头 `X-Request-Id` 带值就用,否则生成
    - 响应头回写 `X-Request-Id`,便于客户端关联
    - ContextVar 让同请求内任何日志自动带上该 ID
    """

    HEADER = "X-Request-Id"

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get(self.HEADER) or uuid.uuid4().hex[:12]
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
            response.headers[self.HEADER] = rid
            return response
        finally:
            request_id_ctx.reset(token)


# ---------------------------------------------------------- setup_logging
def setup_logging(cfg: LoggingConfig | None = None) -> None:
    """按配置初始化 root logger。可在 lifespan 或测试中调用。

    配置项见 LoggingConfig:
    - format: console | json       控制台输出格式
    - color:  true/false           控制台是否彩色(console 模式)
    - level:  DEBUG/INFO/WARNING/ERROR
    - file:   可选,主日志文件路径(同时输出到控制台和文件)
    - rotation:  daily/hourly/midnight(文件按时间滚动归档)
    - backup_count: 保留多少个历史归档(超期自动删除)
    - max_file_size: 单文件大小上限(MB),0=不按大小滚动
    """
    cfg = cfg or settings.logging

    root = logging.getLogger()
    # 清掉旧 handler(避免 basicConfig 残留 + 重复输出)
    for h in list(root.handlers):
        root.removeHandler(h)

    root.setLevel(cfg.level.upper())

    # 控制台 handler
    console = logging.StreamHandler(sys.stdout)
    if cfg.format == "json":
        console.setFormatter(JsonLogFormatter())
    else:
        console.setFormatter(SpringBootLogFormatter(color=cfg.color))
    root.addHandler(console)

    # 文件 handler(可选)—— 支持按时间 + 按大小滚动归档
    if cfg.file:
        for fh in _build_file_handlers(cfg):
            root.addHandler(fh)

    # 降低第三方库噪音
    for noisy in ("uvicorn.access", "httpx", "openai", "urllib3", "httpcore"):
        logging.getLogger(noisy).setLevel("WARNING")


def _build_file_handlers(cfg: LoggingConfig) -> list[logging.Handler]:
    """构造文件 handler 列表(支持按时间滚动 + 可选按大小滚动)。

    归档命名规则(参考 Logback):
      app.log                  当前正在写的文件
      app.log.2026-07-15       昨天(已滚动归档)
      app.log.2026-07-14       前天
      ...
      超过 backup_count 个的最老文件会被自动删除
    """
    os.makedirs(os.path.dirname(cfg.file) or ".", exist_ok=True)  # type: ignore[arg-type]

    handlers: list[logging.Handler] = []
    fmt = SpringBootLogFormatter(color=False)  # 文件始终无色纯文本

    # ---- 按时间滚动(对应 Logback TimeBasedRollingPolicy)----
    when, suffix = _parse_rotation(cfg.rotation)
    time_handler = logging.handlers.TimedRotatingFileHandler(
        cfg.file,
        when=when,
        interval=1,
        backupCount=cfg.backup_count,
        encoding="utf-8",
        utc=False,
    )
    time_handler.suffix = suffix
    time_handler.setFormatter(fmt)
    time_handler.setLevel(cfg.level.upper())
    handlers.append(time_handler)

    # ---- 可选:按大小滚动(对应 Logback SizeBasedTriggeringPolicy)----
    # 同时配置时间+大小时,大小滚动作为兜底:单文件太大也切一份。
    if cfg.max_file_size > 0:
        size_handler = logging.handlers.RotatingFileHandler(
            cfg.file + ".size",
            maxBytes=cfg.max_file_size * 1024 * 1024,
            backupCount=cfg.backup_count,
            encoding="utf-8",
        )
        size_handler.setFormatter(fmt)
        size_handler.setLevel(cfg.level.upper())
        handlers.append(size_handler)

    return handlers


def _parse_rotation(rotation: str) -> tuple[str, str]:
    """把配置里的 rotation 字符串映射成 TimedRotatingFileHandler 的 when/suffix。

    返回 (when, suffix):
      when   传给 TimedRotatingFileHandler
      suffix 归档文件名后缀(决定文件名里日期格式)
    """
    mapping = {
        "daily": ("midnight", "%Y-%m-%d"),       # 每天午夜滚动 -> app.log.2026-07-15
        "midnight": ("midnight", "%Y-%m-%d"),    # 同上(别名)
        "hourly": ("H", "%Y-%m-%d_%H"),          # 每小时 -> app.log.2026-07-15_14
        "weekly": ("W0", "%Y-%m-%d"),            # 每周一滚动
    }
    if rotation not in mapping:
        raise ValueError(
            f"Unknown logging.rotation: '{rotation}'. "
            f"Must be one of: {list(mapping)}"
        )
    return mapping[rotation]


def get_logger(name: str) -> logging.Logger:
    """统一获取 logger 的入口(便于将来加额外逻辑)。"""
    return logging.getLogger(name)


__all__ = [
    "RequestIdMiddleware",
    "SpringBootLogFormatter",
    "JsonLogFormatter",
    "abbreviate_name",
    "request_id_ctx",
    "user_ctx",
    "setup_logging",
    "get_logger",
]
