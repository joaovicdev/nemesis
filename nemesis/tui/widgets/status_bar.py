"""StatusBar widget — fixed footer with model, project, phase bar, and step info."""

from __future__ import annotations

import platform

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

_PHASES = ("RECON", "ENUM", "EXPLOIT", "POST", "REPORT")

_PHASE_MAP: dict[str, str] = {
    "recon": "RECON",
    "enumeration": "ENUM",
    "exploitation": "EXPLOIT",
    "post_exploitation": "POST",
    "reporting": "REPORT",
}


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
    """Three-line footer bar showing persistent session context + phase progress."""

    DEFAULT_CSS = """
    StatusBar {
        background: #0f0f1a;
        border-top: tall #1a1a3a;
        height: 4;
        padding: 0 2;
        dock: bottom;
        layout: vertical;
    }
    """

    model_name: reactive[str] = reactive("no model")
    project_name: reactive[str] = reactive("no project")
    phase: reactive[str] = reactive("—")
    mode: reactive[str] = reactive("step")
    step_progress: reactive[tuple[int, int]] = reactive((0, 0))
    current_step_label: reactive[str] = reactive("")
    findings_count: reactive[int] = reactive(0)

    def compose(self) -> None:
        yield Static("", id="bar-line1")
        yield Static("", id="bar-line2")
        yield Static("", id="bar-line3")

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

    def watch_step_progress(self, _: tuple[int, int]) -> None:
        self._refresh()

    def watch_current_step_label(self, _: str) -> None:
        self._refresh()

    def watch_findings_count(self, _: int) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self.query_one("#bar-line1", Static).update(self._render_line1())
        self.query_one("#bar-line2", Static).update(self._render_line2())
        self.query_one("#bar-line3", Static).update(self._render_line3())

    # ── Line 1: NEMESIS · project · model ──────────────────────────────────

    def _render_line1(self) -> Text:
        sep = Text("  ·  ", style="#1a1a3a")
        text = Text()
        text.append("NEMESIS", style="bold #00d4ff")
        text.append_text(sep)
        text.append(self.project_name, style="#c8c8d8")
        text.append_text(sep)
        text.append(self.model_name, style="#555570")
        text.append_text(sep)
        text.append("OS ", style="#333355")
        text.append(_detect_os(), style="#333355")
        return text

    # ── Line 2: phase progress bar ──────────────────────────────────────────

    def _render_line2(self) -> Text:
        current_phase_key = _PHASE_MAP.get(self.phase.lower(), self.phase.upper())
        text = Text()

        current, total = self.step_progress
        if total > 0:
            pct = current / total
            bar_len = 14
            filled = round(bar_len * pct)
            bar = "━" * filled
            if filled < bar_len:
                bar += "╸"
            bar += " " * max(0, bar_len - filled - 1)
        else:
            bar = "─" * 14

        # Current phase with bar
        for _i, phase_label in enumerate(_PHASES):
            is_current = phase_label == current_phase_key
            if is_current:
                text.append(f" {phase_label} ", style="bold #ffd700")
                text.append(bar, style="#00d4ff")
                text.append("  ", style="#555570")
            else:
                text.append(f" {phase_label} ", style="#333355")

        return text

    # ── Line 3: step counter · findings · current step label ───────────────

    def _render_line3(self) -> Text:
        sep = Text("  ·  ", style="#1a1a3a")
        text = Text()

        current, total = self.step_progress
        if total > 0:
            text.append("step ", style="#555570")
            text.append(f"{current}/{total}", style="#c8c8d8")
        else:
            text.append("no plan", style="#333355")

        text.append_text(sep)
        text.append(f"{self.findings_count}", style="#ffd700")
        text.append(" findings", style="#555570")

        if self.current_step_label:
            text.append_text(sep)
            label = (
                self.current_step_label[:30] + "…"
                if len(self.current_step_label) > 30
                else self.current_step_label
            )
            text.append(label, style="#00d4ff")
            text.append(" ⚡", style="#ffd700")

        mode_color = {"auto": "#ff2040", "step": "#00d4ff", "manual": "#555570"}.get(
            self.mode, "#555570"
        )
        text.append("  MODE ", style="#333355")
        text.append(self.mode.upper(), style=f"bold {mode_color}")

        return text

    # ── Public update methods ───────────────────────────────────────────────

    def update_model(self, name: str) -> None:
        self.model_name = name

    def update_project(self, name: str) -> None:
        self.project_name = name

    def update_phase(self, phase: str) -> None:
        self.phase = phase

    def update_mode(self, mode: str) -> None:
        self.mode = mode

    def update_step(self, current: int, total: int, label: str = "") -> None:
        """Update the step progress counter and optionally the current step label."""
        self.step_progress = (current, total)
        if label:
            self.current_step_label = label

    def update_findings_count(self, count: int) -> None:
        self.findings_count = count
