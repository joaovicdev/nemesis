"""NEMESIS splash / boot screen with ASCII logo and animated boot sequence."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Label, Static


NEMESIS_LOGO = """\
███╗   ██╗███████╗███╗   ███╗███████╗███████╗██╗███████╗
████╗  ██║██╔════╝████╗ ████║██╔════╝██╔════╝██║██╔════╝
██╔██╗ ██║█████╗  ██╔████╔██║█████╗  ███████╗██║███████╗
██║╚██╗██║██╔══╝  ██║╚██╔╝██║██╔══╝  ╚════██║██║╚════██║
██║ ╚████║███████╗██║ ╚═╝ ██║███████╗███████║██║███████║
╚═╝  ╚═══╝╚══════╝╚═╝     ╚═╝╚══════╝╚══════╝╚═╝╚══════╝"""

TAGLINE = "T H E   A D V E R S A R Y"
SUBTITLE = "AI-Assisted Penetration Testing Co-pilot"

BOOT_SEQUENCE: list[str] = [
    "initializing core systems...",
    "loading project context engine...",
    "connecting to local AI model...",
    "verifying tool registry...",
    "ready.",
]


class SplashScreen(Screen[None]):
    """Full-screen boot animation with NEMESIS logo and boot sequence."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "proceed", "Continue", show=False),
        Binding("space", "proceed", "Continue", show=False),
        Binding("escape", "proceed", "Skip", show=False),
    ]

    DEFAULT_CSS = """
    SplashScreen {
        align: center middle;
        background: #0a0a0a;
    }

    #logo {
        text-align: center;
        color: #00d4ff;
        text-style: bold;
        width: auto;
    }

    #tagline {
        text-align: center;
        color: #007a9e;
        width: auto;
        margin-top: 1;
    }

    #subtitle {
        text-align: center;
        color: #555570;
        width: auto;
    }

    #boot-status {
        text-align: center;
        color: #555570;
        width: auto;
        margin-top: 2;
        height: 1;
    }

    #proceed-hint {
        text-align: center;
        color: #1a1a3a;
        width: auto;
        margin-top: 1;
    }

    #version {
        text-align: center;
        color: #1a1a3a;
        width: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(NEMESIS_LOGO, id="logo")
        yield Static(TAGLINE, id="tagline")
        yield Static(SUBTITLE, id="subtitle")
        yield Static("", id="boot-status")
        yield Static("[ press enter to continue ]", id="proceed-hint")
        yield Static("v0.1.0", id="version")

    def on_mount(self) -> None:
        self.run_worker(self._boot_sequence(), exclusive=True)

    async def _boot_sequence(self) -> None:
        status_widget = self.query_one("#boot-status", Static)
        hint_widget = self.query_one("#proceed-hint", Static)

        hint_widget.update("")

        for line in BOOT_SEQUENCE:
            status_widget.update(f"[#555570]>[/] [#007a9e]{line}[/]")
            await asyncio.sleep(0.35)

        await asyncio.sleep(0.3)
        status_widget.update("[#00ff7f]>[/] [#00ff7f]systems online.[/]")
        await asyncio.sleep(0.5)
        hint_widget.update("[#1a1a3a][ press enter to continue ][/]")

        # Auto-proceed after 2 seconds of showing the hint
        await asyncio.sleep(2.0)
        self._go_to_main()

    def action_proceed(self) -> None:
        self._go_to_main()

    def _go_to_main(self) -> None:
        from nemesis.tui.screens.main import MainScreen

        self.app.switch_screen(MainScreen())

    def _build_logo_text(self) -> Text:
        """Build a Rich Text logo with cyan gradient effect."""
        text = Text()
        lines = NEMESIS_LOGO.split("\n")
        shades = ["#005a7a", "#007a9e", "#009ac8", "#00bae0", "#00d4ff", "#00d4ff"]
        for i, line in enumerate(lines):
            shade = shades[min(i, len(shades) - 1)]
            text.append(line + "\n", style=f"bold {shade}")
        return text
