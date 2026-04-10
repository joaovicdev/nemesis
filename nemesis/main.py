"""Entry point for the NEMESIS CLI."""

from __future__ import annotations

import sys


def run() -> None:
    """Launch the NEMESIS TUI application."""
    try:
        from nemesis.tui.app import NemesisApp

        app = NemesisApp()
        app.run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    run()
