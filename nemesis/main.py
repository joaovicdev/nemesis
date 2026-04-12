"""Entry point for the NEMESIS CLI."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def run() -> None:
    """Launch the NEMESIS TUI application."""
    _configure_logging()
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


if __name__ == "__main__":
    run()
