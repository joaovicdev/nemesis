"""StatusBar widget — fixed footer with model, project, OS, and phase info."""

from __future__ import annotations

import platform

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


def _detect_os() -> str:
    system = platform.system().lower()
    if system == "linux":
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("ID="):
                        distro = line.split("=")[1].strip().strip('"').lower()
                        return distro
        except OSError:
            return "linux"
    elif system == "darwin":
        return "macos"
    return system


class StatusBar(Widget):
    """Single-line footer bar showing persistent session context."""

    DEFAULT_CSS = """
    StatusBar {
        background: #0f0f1a;
        border-top: tall #1a1a3a;
        height: 1;
        padding: 0 2;
        dock: bottom;
        layout: horizontal;
        align: left middle;
    }
    """

    model_name: reactive[str] = reactive("no model")
    project_name: reactive[str] = reactive("no project")
    phase: reactive[str] = reactive("—")
    mode: reactive[str] = reactive("step")

    def compose(self) -> None:
        yield Static("", id="bar-content")

    def on_mount(self) -> None:
        self._refresh()

    def watch_model_name(self, _: str) -> None:
        self._refresh()

    def watch_project_name(self, _: str) -> None:
        self._refresh()

    def watch_phase(self, _: str) -> None:
        self._refresh()

    def watch_mode(self, _: str) -> None:
        self._refresh()

    def _refresh(self) -> None:
        widget = self.query_one("#bar-content", Static)
        widget.update(self._render())

    def _render(self) -> Text:
        sep = Text("  │  ", style="#1a1a3a")
        text = Text()

        text.append("MODEL ", style="#555570")
        text.append(self.model_name, style="#00d4ff")

        text.append_text(sep)

        text.append("PROJECT ", style="#555570")
        text.append(self.project_name, style="#c8c8d8")

        text.append_text(sep)

        text.append("PHASE ", style="#555570")
        text.append(self.phase, style="#ffd700")

        text.append_text(sep)

        text.append("MODE ", style="#555570")
        mode_color = {"auto": "#ff2040", "step": "#00d4ff", "manual": "#555570"}.get(
            self.mode, "#555570"
        )
        text.append(self.mode.upper(), style=f"bold {mode_color}")

        text.append_text(sep)

        text.append("OS ", style="#555570")
        text.append(_detect_os(), style="#555570")

        return text

    def update_model(self, name: str) -> None:
        self.model_name = name

    def update_project(self, name: str) -> None:
        self.project_name = name

    def update_phase(self, phase: str) -> None:
        self.phase = phase

    def update_mode(self, mode: str) -> None:
        self.mode = mode
