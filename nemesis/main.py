"""Entry point for the NEMESIS CLI."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def run() -> None:
    """Launch the NEMESIS TUI application."""
    _configure_logging()
    _log_llm_config()
    try:
        from nemesis.tui.app import NemesisApp

        app = NemesisApp()
        app.run()
    except KeyboardInterrupt:
        sys.exit(0)


def _configure_logging() -> None:
    """Initialise structured logging before anything else starts."""
    from nemesis.core.logging_config import setup_logging

    log_dir_env = os.environ.get("NEMESIS_LOG_DIR", "").strip()
    log_dir = Path(log_dir_env) if log_dir_env else None
    debug = os.environ.get("NEMESIS_DEBUG", "").strip() in ("1", "true", "yes")
    setup_logging(log_dir=log_dir, debug=debug)


def _log_llm_config() -> None:
    """Log which LLM model will be used so the user knows at startup."""
    from nemesis.agents.llm_client import load_llm_config_from_env

    cfg = load_llm_config_from_env()
    logger.info(
        "LLM config loaded",
        extra={
            "event": "app.llm_config_loaded",
            "model": cfg.model,
            "base_url": cfg.base_url,
            "timeout": cfg.timeout,
        },
    )


if __name__ == "__main__":
    run()
