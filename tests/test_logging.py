"""Tests for the Spring Boot style logging.

Verifies:
- SpringBootLogFormatter produces the expected fields/padding
- abbreviate_name compresses logger names like Spring (c.e.d.X)
- JsonLogFormatter emits one JSON object per line
- request_id ContextVar flows through RequestIdMiddleware
- color toggle works
"""
from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.logging_config import (
    JsonLogFormatter,
    RequestIdMiddleware,
    SpringBootLogFormatter,
    abbreviate_name,
    request_id_ctx,
    setup_logging,
)


# ----------------------------------------------------------- abbreviate_name
def test_abbreviate_long_name():
    assert abbreviate_name("app.services.llm") == "a.s.llm"
    assert abbreviate_name("app.api.v1.endpoints.items") == "a.a.v.e.items"
    assert abbreviate_name("app.core.datasource") == "a.c.datasource"


def test_abbreviate_short_name_unchanged():
    assert abbreviate_name("app") == "app"
    assert abbreviate_name("uvicorn") == "uvicorn"
    assert abbreviate_name("") == ""


def test_abbreviate_two_parts():
    # Only the last segment is kept full
    assert abbreviate_name("foo.bar") == "f.bar"


# ----------------------------------------------------------- formatter
def _make_record(msg: str = "hello", name: str = "app.services.llm") -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_springboot_formatter_contains_all_fields():
    fmt = SpringBootLogFormatter(color=False)
    out = fmt.format(_make_record("User login"))

    # Timestamp with milliseconds
    assert "20" in out[:5]  # year starts with 20
    assert "." in out  # has millis separator
    # Level padded to 5 chars
    assert " INFO " in out
    # PID present (numeric run)
    import re

    assert re.search(r"\b\d+\b", out)
    # Logger abbreviated
    assert "a.s.llm" in out
    # Separator and message
    assert " : User login" in out


def test_springboot_formatter_color_includes_ansi():
    fmt = SpringBootLogFormatter(color=True)
    out = fmt.format(_make_record("colored"))
    # ANSI escape codes present
    assert "\033[" in out
    assert "colored" in out


def test_springboot_formatter_no_color_when_disabled():
    fmt = SpringBootLogFormatter(color=False)
    out = fmt.format(_make_record("plain"))
    assert "\033[" not in out
    assert "plain" in out


def test_springboot_formatter_with_request_id():
    fmt = SpringBootLogFormatter(color=False)
    token = request_id_ctx.set("req-abc123")
    try:
        out = fmt.format(_make_record("with-rid"))
        assert "req-abc123" in out
    finally:
        request_id_ctx.reset(token)


def test_json_formatter_outputs_valid_json():
    fmt = JsonLogFormatter()
    out = fmt.format(_make_record("json-msg", name="app.test"))
    payload = json.loads(out)  # raises if not valid JSON

    assert payload["message"] == "json-msg"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert "request_id" in payload
    assert "@timestamp" in payload


def test_json_formatter_includes_exception():
    fmt = JsonLogFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="app.test", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="failed", args=(), exc_info=exc_info,
    )
    out = fmt.format(record)
    payload = json.loads(out)
    assert "exception" in payload
    assert "ValueError" in payload["exception"]


def test_setup_logging_console_handler():
    """setup_logging should attach a SpringBoot formatter to root."""
    from app.core.config import LoggingConfig

    # Capture stdout via a stream handler for inspection
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    try:
        setup_logging(LoggingConfig(level="DEBUG", format="console", color=False))
        assert root.level == logging.DEBUG
        # At least one handler attached
        assert len(root.handlers) >= 1
    finally:
        root.handlers = old_handlers


def test_setup_logging_json_handler():
    from app.core.config import LoggingConfig

    root = logging.getLogger()
    old_handlers = root.handlers[:]
    try:
        setup_logging(LoggingConfig(format="json"))
        # One of the handlers should use JsonLogFormatter
        formatters = [type(h.formatter).__name__ for h in root.handlers]
        assert "JsonLogFormatter" in formatters
    finally:
        root.handlers = old_handlers


# ----------------------------------------------------------- middleware
def test_request_id_middleware_generates_and_returns():
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ping")
    async def ping():
        return {"rid": request_id_ctx.get()}

    with TestClient(app) as c:
        r = c.get("/ping")
        assert r.status_code == 200
        # Response carries the header
        assert "X-Request-Id" in r.headers
        rid = r.headers["X-Request-Id"]
        assert len(rid) > 0
        # Same value visible inside the handler
        assert r.json()["rid"] == rid


def test_request_id_middleware_passes_client_value():
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ping")
    async def ping():
        return {"rid": request_id_ctx.get()}

    with TestClient(app) as c:
        r = c.get("/ping", headers={"X-Request-Id": "client-supplied-1"})
        assert r.headers["X-Request-Id"] == "client-supplied-1"
        assert r.json()["rid"] == "client-supplied-1"


def test_request_id_isolated_between_requests():
    """Two sequential requests must get different request_ids."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/rid")
    async def get_rid():
        return {"rid": request_id_ctx.get()}

    with TestClient(app) as c:
        r1 = c.get("/rid").json()["rid"]
        r2 = c.get("/rid").json()["rid"]
    assert r1 != r2


# ----------------------------------------------------------- end-to-end output
def test_end_to_end_log_format(capsys):
    """A real log call should render Spring Boot style on stdout."""
    from app.core.config import LoggingConfig

    setup_logging(LoggingConfig(format="console", color=False, level="INFO"))
    log = logging.getLogger("app.services.demo")
    log.info("hello world")

    captured = capsys.readouterr()
    assert "hello world" in captured.out
    assert "a.s.demo" in captured.out  # abbreviated
    assert "INFO" in captured.out


# ----------------------------------------------------------- rotation / archival
import logging.handlers  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from app.core.config import LoggingConfig  # noqa: E402
from app.core.logging_config import _build_file_handlers, _parse_rotation  # noqa: E402


def test_parse_rotation_daily():
    when, suffix = _parse_rotation("daily")
    assert when == "midnight"
    assert "%Y-%m-%d" in suffix


def test_parse_rotation_hourly():
    when, suffix = _parse_rotation("hourly")
    assert when == "H"
    assert "%H" in suffix


def test_parse_rotation_weekly():
    when, suffix = _parse_rotation("weekly")
    assert when == "W0"


def test_parse_rotation_invalid_raises():
    with pytest.raises(ValueError, match="Unknown logging.rotation"):
        _parse_rotation("yearly")


def test_build_file_handlers_uses_timed_rotating(tmp_path: Path):
    """配 file 后应创建 TimedRotatingFileHandler(按天滚动)。"""
    log_file = tmp_path / "app.log"
    cfg = LoggingConfig(file=str(log_file), rotation="daily", backup_count=15)
    handlers = _build_file_handlers(cfg)

    time_handlers = [
        h for h in handlers if isinstance(h, logging.handlers.TimedRotatingFileHandler)
    ]
    assert len(time_handlers) == 1
    h = time_handlers[0]
    assert h.backupCount == 15
    # TimedRotatingFileHandler normalizes `when` to uppercase internally
    assert h.when.upper() == "MIDNIGHT"
    # 主日志文件应被创建
    assert log_file.exists()


def test_build_file_handlers_max_size_adds_rotating_handler(tmp_path: Path):
    """max_file_size>0 时应额外加一个 RotatingFileHandler(按大小)。"""
    log_file = tmp_path / "app.log"
    cfg = LoggingConfig(file=str(log_file), max_file_size=10, backup_count=5)
    handlers = _build_file_handlers(cfg)

    rotating_handlers = [
        h for h in handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    # 应该有 1 个 TimedRotating + 1 个按大小的 Rotating
    assert any(isinstance(h, logging.handlers.TimedRotatingFileHandler) for h in handlers)
    assert len(rotating_handlers) == 1
    assert rotating_handlers[0].maxBytes == 10 * 1024 * 1024


def test_build_file_handlers_no_size_rotation_by_default(tmp_path: Path):
    """max_file_size=0 时不应有按大小的 handler。"""
    log_file = tmp_path / "app.log"
    cfg = LoggingConfig(file=str(log_file), max_file_size=0)
    handlers = _build_file_handlers(cfg)
    assert len(handlers) == 1
    assert isinstance(handlers[0], logging.handlers.TimedRotatingFileHandler)


def test_setup_logging_attaches_timed_rotating_to_root(tmp_path: Path):
    """setup_logging 应把 TimedRotatingFileHandler 挂到 root logger。"""
    log_file = tmp_path / "app.log"
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    try:
        setup_logging(LoggingConfig(file=str(log_file), backup_count=7))
        timed = [
            h
            for h in root.handlers
            if isinstance(h, logging.handlers.TimedRotatingFileHandler)
        ]
        assert len(timed) == 1
        assert timed[0].backupCount == 7
    finally:
        for h in root.handlers:
            if hasattr(h, "close"):
                h.close()
        root.handlers = old_handlers


def test_log_actually_written_to_file(tmp_path: Path):
    """真实写一条日志,验证文件里有内容 + 格式正确。"""
    log_file = tmp_path / "app.log"
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    try:
        setup_logging(
            LoggingConfig(file=str(log_file), format="console", color=False, level="INFO")
        )
        log = logging.getLogger("app.services.demo")
        log.info("file-output-test")

        for h in root.handlers:
            h.flush()
            if hasattr(h, "close"):
                h.close()

        content = log_file.read_text(encoding="utf-8")
        assert "file-output-test" in content
        assert "a.s.demo" in content  # abbreviated
    finally:
        root.handlers = old_handlers


def test_backup_count_default_is_30():
    """默认配置应保留 30 天(对照 Logback maxHistory 常用值)。"""
    cfg = LoggingConfig()
    assert cfg.backup_count == 30
    assert cfg.rotation == "daily"
