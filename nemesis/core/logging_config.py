"""Central logging configuration for NEMESIS.

Single source of truth for:
- Structured JSON formatter with correlation ID injection
- Custom AUDIT level (25) for security-sensitive events
- Rotating file handlers: nemesis.log, nemesis.debug.log, nemesis.audit.log
- ContextVar-based session_id propagation across async tasks
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── Custom AUDIT level ──────────────────────────────────────────────────────────

AUDIT_LEVEL = 25
logging.addLevelName(AUDIT_LEVEL, "AUDIT")


def audit(self: logging.Logger, message: str, *args: Any, **kwargs: Any) -> None:
    """Emit an AUDIT-level log record."""
    if self.isEnabledFor(AUDIT_LEVEL):
        self._log(AUDIT_LEVEL, message, args, **kwargs)


logging.Logger.audit = audit  # type: ignore[attr-defined]

# ── Correlation context ─────────────────────────────────────────────────────────

_session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)


def set_session_id(session_id: str) -> None:
    """Bind session_id to the current async task's context."""
    _session_id_var.set(session_id)


def clear_session_id() -> None:
    """Remove the session_id from the current async task's context."""
    _session_id_var.set(None)


def get_session_id() -> str | None:
    """Return the session_id bound to the current async task, or None."""
    return _session_id_var.get()


# ── Structured JSON formatter ───────────────────────────────────────────────────


class StructuredFormatter(logging.Formatter):
    """
    Formats every log record as a single-line JSON object.

    Fixed keys: ts, level, logger, event (message).
    Injected when available: session_id (from ContextVar).
    Extra keys from the `extra` dict are merged into the top-level object.

    Security: never log raw target/IP/credentials — callers are responsible
    for passing only safe, derived metadata (counts, IDs, elapsed_ms, etc.).
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = (
            datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            + "Z"
        )

        data: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }

        sid = _session_id_var.get()
        if sid is not None:
            data["session_id"] = sid

        # Merge any structured fields passed via extra={...}
        _SKIP = {
            "args",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",
        }
        for key, val in record.__dict__.items():
            if key not in _SKIP and not key.startswith("_"):
                data[key] = val

        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)

        return json.dumps(data, default=str)


# ── Audit-only filter ───────────────────────────────────────────────────────────


class _AuditFilter(logging.Filter):
    """Allow only AUDIT-level records through."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == AUDIT_LEVEL


# ── Setup ───────────────────────────────────────────────────────────────────────

_LOG_DIR_DEFAULT = Path.home() / ".nemesis" / "logs"

_NEMESIS_LOGGER = "nemesis"

# Sizes and backup counts
_MAIN_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_MAIN_BACKUP_COUNT = 5
_DEBUG_MAX_BYTES = 20 * 1024 * 1024  # 20 MB
_DEBUG_BACKUP_COUNT = 3
_AUDIT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_AUDIT_BACKUP_COUNT = 10  # keep more audit history


def setup_logging(
    log_dir: Path | None = None,
    debug: bool = False,
) -> None:
    """
    Configure the nemesis.* logger hierarchy.

    Creates rotating JSON log files under log_dir (default: ~/.nemesis/logs/):
      - nemesis.log       — INFO+ from all subsystems
      - nemesis.debug.log — DEBUG+ (only when debug=True or NEMESIS_DEBUG=1)
      - nemesis.audit.log — AUDIT-level events only (confirmations, gate decisions)

    Propagation to the root logger is disabled so Textual's own logging
    does not receive NEMESIS records.

    Args:
        log_dir: Directory to write log files into.
        debug: Enable DEBUG-level logging to nemesis.debug.log.
    """
    effective_debug = debug or os.environ.get("NEMESIS_DEBUG", "").strip() in ("1", "true", "yes")

    resolved_dir = log_dir or _LOG_DIR_DEFAULT
    resolved_dir.mkdir(parents=True, exist_ok=True)

    formatter = StructuredFormatter()

    # ── Main handler (INFO+) ────────────────────────────────────────────────
    main_handler = logging.handlers.RotatingFileHandler(
        resolved_dir / "nemesis.log",
        maxBytes=_MAIN_MAX_BYTES,
        backupCount=_MAIN_BACKUP_COUNT,
        encoding="utf-8",
    )
    main_handler.setLevel(logging.INFO)
    main_handler.setFormatter(formatter)

    # ── Audit handler (AUDIT-level only) ────────────────────────────────────
    audit_handler = logging.handlers.RotatingFileHandler(
        resolved_dir / "nemesis.audit.log",
        maxBytes=_AUDIT_MAX_BYTES,
        backupCount=_AUDIT_BACKUP_COUNT,
        encoding="utf-8",
    )
    audit_handler.setLevel(AUDIT_LEVEL)
    audit_handler.addFilter(_AuditFilter())
    audit_handler.setFormatter(formatter)

    handlers: list[logging.Handler] = [main_handler, audit_handler]

    # ── Debug handler (DEBUG+ optional) ─────────────────────────────────────
    if effective_debug:
        debug_handler = logging.handlers.RotatingFileHandler(
            resolved_dir / "nemesis.debug.log",
            maxBytes=_DEBUG_MAX_BYTES,
            backupCount=_DEBUG_BACKUP_COUNT,
            encoding="utf-8",
        )
        debug_handler.setLevel(logging.DEBUG)
        debug_handler.setFormatter(formatter)
        handlers.append(debug_handler)

    # ── Configure the nemesis root logger ───────────────────────────────────
    nemesis_logger = logging.getLogger(_NEMESIS_LOGGER)
    nemesis_logger.setLevel(logging.DEBUG if effective_debug else logging.INFO)
    nemesis_logger.propagate = False  # do not leak into Textual's root logger

    # Clear any handlers added by prior calls (e.g. during testing)
    nemesis_logger.handlers.clear()
    for handler in handlers:
        nemesis_logger.addHandler(handler)

    nemesis_logger.info(
        "Logging initialised",
        extra={
            "event": "logging.setup",
            "log_dir": str(resolved_dir),
            "debug_enabled": effective_debug,
        },
    )
