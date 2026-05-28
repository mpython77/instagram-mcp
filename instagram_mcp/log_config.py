"""Structured logging configuration with ContextVars, JSON formatter, and redaction filter.

Provides:
- Three module-level ContextVar instances for correlation tracking.
- ``configure_logging()`` to install the chosen formatter (JSON or plain text)
  on the root logger, respecting env vars for format and level.
- ``ContextFilter`` that copies ContextVar values onto every LogRecord.
- ``RedactingFilter`` that strips sensitive Cookie/sessionid patterns.

Environment variables:
- ``INSTAGRAM_MCP_LOG_FORMAT``: ``"json"`` or ``"text"`` (default ``"text"``).
- ``INSTAGRAM_MCP_LOG_LEVEL``: standard Python log level name (default ``"INFO"``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "current_correlation_id",
    "current_tool_name",
    "current_account_alias",
    "new_correlation_id",
    "configure_logging",
    "ContextFilter",
    "RedactingFilter",
]

# ---------------------------------------------------------------------------
# ContextVars — set by Tool_Wrapper on each tool invocation
# ---------------------------------------------------------------------------

current_correlation_id: ContextVar[str | None] = ContextVar(
    "current_correlation_id", default=None
)
current_tool_name: ContextVar[str | None] = ContextVar(
    "current_tool_name", default=None
)
current_account_alias: ContextVar[str | None] = ContextVar(
    "current_account_alias", default=None
)


# ---------------------------------------------------------------------------
# Correlation ID helper
# ---------------------------------------------------------------------------


def new_correlation_id() -> str:
    """Generate a new correlation ID (uuid4 hex, no dashes)."""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

_COOKIE_RE = re.compile(r"Cookie:\s*[^\r\n]+", re.IGNORECASE)
_SESSIONID_RE = re.compile(r"sessionid=[^;\s]+", re.IGNORECASE)


class ContextFilter(logging.Filter):
    """Copies ContextVar values onto every LogRecord.

    Attributes added: ``correlation_id``, ``tool``, ``account_alias``.
    When a ContextVar is None the attribute is set to None (JSON null).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = current_correlation_id.get()  # type: ignore[attr-defined]
        record.tool = current_tool_name.get()  # type: ignore[attr-defined]
        record.account_alias = current_account_alias.get()  # type: ignore[attr-defined]
        return True


class RedactingFilter(logging.Filter):
    """Strips Cookie headers and sessionid patterns from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg and isinstance(record.msg, str):
            record.msg = _COOKIE_RE.sub("Cookie: <redacted>", record.msg)
            record.msg = _SESSIONID_RE.sub("sessionid=<redacted>", record.msg)
        # Also redact the formatted message if args are present
        if record.args:
            # Format the message first, then redact
            try:
                formatted = record.msg % record.args
                formatted = _COOKIE_RE.sub("Cookie: <redacted>", formatted)
                formatted = _SESSIONID_RE.sub("sessionid=<redacted>", formatted)
                record.msg = formatted
                record.args = None
            except (TypeError, ValueError):
                pass
        return True


# ---------------------------------------------------------------------------
# JSON Formatter (hand-rolled fallback when python-json-logger unavailable)
# ---------------------------------------------------------------------------


class _FallbackJSONFormatter(logging.Formatter):
    """Minimal JSON formatter used when python-json-logger is not installed."""

    def format(self, record: logging.LogRecord) -> str:
        # Build the base message
        message = record.getMessage()
        # Redact sensitive patterns in the final message
        message = _COOKIE_RE.sub("Cookie: <redacted>", message)
        message = _SESSIONID_RE.sub("sessionid=<redacted>", message)

        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "correlation_id": getattr(record, "correlation_id", None),
            "tool": getattr(record, "tool", None),
            "account_alias": getattr(record, "account_alias", None),
        }

        # Include extra fields passed via extra={...}
        # Standard LogRecord attributes to exclude from extras
        _STANDARD_ATTRS = {
            "name", "msg", "args", "created", "relativeCreated",
            "thread", "threadName", "msecs", "filename", "funcName",
            "levelno", "lineno", "module", "exc_info", "exc_text",
            "stack_info", "pathname", "processName", "process",
            "levelname", "message", "correlation_id", "tool",
            "account_alias", "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                log_entry[key] = value

        return json.dumps(log_entry, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Install structured logging on the root logger.

    Parameters
    ----------
    level : str | None
        Override log level. If None, reads ``INSTAGRAM_MCP_LOG_LEVEL`` env var
        (default ``"INFO"``).
    fmt : str | None
        Override format. If None, reads ``INSTAGRAM_MCP_LOG_FORMAT`` env var
        (default ``"text"``). Valid: ``"json"``, ``"text"``.
    """
    logger = logging.getLogger()

    # --- Resolve log level ---
    raw_level = level or os.environ.get("INSTAGRAM_MCP_LOG_LEVEL", "INFO")
    resolved_level = raw_level.upper()
    if resolved_level not in _VALID_LEVELS:
        # Will log warning after handler is installed
        _bad_level = resolved_level
        resolved_level = "INFO"
    else:
        _bad_level = None

    logger.setLevel(getattr(logging, resolved_level))

    # --- Resolve format ---
    raw_fmt = fmt or os.environ.get("INSTAGRAM_MCP_LOG_FORMAT", "text")
    use_json = raw_fmt.lower() == "json"

    # --- Remove existing handlers to avoid duplicates on re-call ---
    logger.handlers.clear()

    # --- Create handler ---
    handler = logging.StreamHandler()

    # --- Install filters on the handler (not just root logger) ---
    # Filters on the handler ensure they run for ALL records that propagate
    # from child loggers, since logger-level filters are NOT inherited.
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactingFilter())

    # Also install on root logger for direct usage
    logger.filters = [
        f for f in logger.filters
        if not isinstance(f, (ContextFilter, RedactingFilter))
    ]
    logger.addFilter(ContextFilter())
    logger.addFilter(RedactingFilter())

    # --- Choose formatter ---
    _json_logger_available = False
    if use_json:
        try:
            from pythonjsonlogger.json import JsonFormatter  # type: ignore[import-untyped]

            formatter = JsonFormatter(
                fmt="%(timestamp)s %(level)s %(name)s %(message)s",
                rename_fields={
                    "timestamp": "timestamp",
                    "levelname": "level",
                    "name": "logger",
                },
                static_fields={},
            )
            # Ensure our reserved keys are included
            _json_logger_available = True
        except ImportError:
            _json_logger_available = False

        if _json_logger_available:
            handler.setFormatter(formatter)
        else:
            # Fall back to our hand-rolled JSON formatter
            handler.setFormatter(_FallbackJSONFormatter())
            # We'll log the warning after handler is attached
    else:
        # Plain text formatter
        text_format = (
            "%(asctime)s [%(levelname)s] %(name)s "
            "[%(correlation_id)s] [%(tool)s] [%(account_alias)s] %(message)s"
        )
        handler.setFormatter(logging.Formatter(text_format))

    logger.addHandler(handler)

    # --- Post-setup warnings ---
    if _bad_level is not None:
        logger.warning(
            "Unrecognised INSTAGRAM_MCP_LOG_LEVEL=%r; falling back to INFO",
            _bad_level,
        )

    if use_json and not _json_logger_available:
        logger.warning(
            "python-json-logger is not installed; JSON log output uses "
            "built-in fallback formatter. Install python-json-logger for "
            "full feature support."
        )
