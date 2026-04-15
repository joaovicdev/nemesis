"""ReportScreen — displays report generation result."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ReportScreen(ModalScreen[None]):
    """Modal that shows the report was saved and where."""

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "dismiss", "Close")]

    DEFAULT_CSS = """
    ReportScreen {
        align: center middle;
    }
    #report-dialog {
        background: #0f0f1a;
        border: tall #00d4ff;
        width: 70;
        height: auto;
        padding: 2 4;
    }
    #report-title {
        text-style: bold;
        color: #00d4ff;
        margin-bottom: 1;
    }
    #report-paths {
        margin: 1 0;
        color: #aaaaaa;
    }
    #close-btn {
        margin-top: 1;
        width: 100%;
    }
    """

    def __init__(self, md_path: Path, html_path: Path) -> None:
        super().__init__()
        self._md_path = md_path
        self._html_path = html_path

    def compose(self) -> ComposeResult:
        with Vertical(id="report-dialog"):
            yield Label("Report Generated", id="report-title")
            yield Label(
                f"Markdown: {self._md_path}\nHTML:     {self._html_path}",
                id="report-paths",
            )
            yield Button("Close (Esc)", id="close-btn", variant="primary")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss()
