"""NEMESIS Textual application — root app definition."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding

from nemesis.tui.screens.splash import SplashScreen


CSS_PATH = Path(__file__).parent / "theme.tcss"


class NemesisApp(App[None]):
    """Root Textual application for NEMESIS."""

    TITLE = "NEMESIS"
    SUB_TITLE = "THE ADVERSARY"
    CSS_PATH = str(CSS_PATH)

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
        Binding("ctrl+n", "new_project", "New Project", show=False),
        Binding("ctrl+l", "load_project", "Load Project", show=False),
        Binding("f1", "help", "Help", show=False),
    ]

    def on_mount(self) -> None:
        self.push_screen(SplashScreen())

    def action_new_project(self) -> None:
        from nemesis.tui.screens.new_project import NewProjectScreen

        self.push_screen(NewProjectScreen())

    def action_load_project(self) -> None:
        # Placeholder — will open a project picker
        pass

    def action_help(self) -> None:
        # Placeholder — will show help overlay
        pass
