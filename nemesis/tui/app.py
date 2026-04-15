"""NEMESIS Textual application — root app definition."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from textual.app import App
from textual.binding import Binding

from nemesis.agents.llm_client import LLMClient
from nemesis.db.database import Database
from nemesis.tui.screens.splash import SplashScreen

if TYPE_CHECKING:
    from nemesis.tui.screens.main import MainScreen


logger = logging.getLogger(__name__)

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

    def __init__(self) -> None:
        super().__init__()
        self.db: Database = Database()
        self.llm_client: LLMClient = LLMClient()

    async def on_mount(self) -> None:
        logger.info(
            "NEMESIS application started",
            extra={"event": "tui.app_started"},
        )
        await self.db.connect()
        await self.push_screen(SplashScreen())

    async def on_unmount(self) -> None:
        logger.info(
            "NEMESIS application stopping",
            extra={"event": "tui.app_stopped"},
        )
        await self.db.close()
        await asyncio.sleep(0.25)  # give aiohttp time to drain open sessions before loop closes

    def action_new_project(self) -> None:
        from nemesis.tui.screens.new_project import NewProjectScreen

        main = self._get_main_screen()
        if main is not None:
            self.push_screen(NewProjectScreen(), main._on_project_created)
        else:
            self.push_screen(NewProjectScreen())

    def action_load_project(self) -> None:
        from nemesis.tui.screens.load_project import LoadProjectScreen

        main = self._get_main_screen()
        if main is not None:
            self.push_screen(LoadProjectScreen(), main._on_project_loaded)
        else:
            self.push_screen(LoadProjectScreen())

    def action_help(self) -> None:
        pass

    def _get_main_screen(self) -> MainScreen | None:
        from nemesis.tui.screens.main import MainScreen

        for screen in self.screen_stack:
            if isinstance(screen, MainScreen):
                return screen
        return None
