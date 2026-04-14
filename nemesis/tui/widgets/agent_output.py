"""AgentOutputPanel — collapsible live executor streaming output panel."""

from __future__ import annotations

import time
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import RichLog, Static


class AgentOutputPanel(Widget):
    """Collapsible panel that streams raw executor output per running step.

    Sits below the chat panel. Collapses automatically when no step is running.
    Use start_step() / push_line() / end_step() to drive it.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+o", "toggle_panel", "Toggle output", show=False),
    ]

    DEFAULT_CSS = """
    AgentOutputPanel {
        background: #080818;
        border-top: tall #1a1a3a;
        height: auto;
        max-height: 14;
        display: none;
    }

    AgentOutputPanel.active {
        display: block;
    }

    AgentOutputPanel #output-header {
        background: #0a0a0a;
        height: 1;
        padding: 0 2;
    }

    AgentOutputPanel #output-log {
        background: #080818;
        padding: 0 2;
        height: auto;
        max-height: 12;
        border: none;
        scrollbar-color: #1a1a3a #080818;
        scrollbar-size: 1 1;
    }

    AgentOutputPanel.collapsed #output-log {
        display: none;
    }
    """

    _collapsed: reactive[bool] = reactive(False)

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._current_step_id: str = ""
        self._current_tool: str = ""
        self._step_started_at: float = 0.0
        self._line_count: int = 0
        self._timer_task: object | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="output-header")
        yield RichLog(id="output-log", wrap=False, markup=False, highlight=False)

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick_timer)

    # ── Public API ──────────────────────────────────────────────────────────

    def start_step(self, step_id: str, tool: str) -> None:
        """Begin streaming output for a new step."""
        self._current_step_id = step_id
        self._current_tool = tool
        self._step_started_at = time.monotonic()
        self._line_count = 0
        self._collapsed = False

        log = self.query_one("#output-log", RichLog)
        log.clear()

        self.add_class("active")
        self.remove_class("collapsed")
        self._render_header()

    def push_line(self, line: str) -> None:
        """Append a raw output line to the streaming log."""
        if not line.strip():
            return
        self._line_count += 1
        log = self.query_one("#output-log", RichLog)
        text = Text()
        text.append(f"  {line}", style="#00d4ff")
        log.write(text)

    def end_step(self) -> None:
        """Mark the current step as complete; collapse the panel."""
        if not self._current_step_id:
            return
        elapsed = self._elapsed_str()
        log = self.query_one("#output-log", RichLog)
        text = Text()
        text.append(f"  [{self._line_count} line(s) · {elapsed}]", style="#555570")
        log.write(text)

        self._collapsed = True
        self.add_class("collapsed")
        self._render_header()

    def clear(self) -> None:
        """Hide the panel and reset state."""
        self._current_step_id = ""
        self._current_tool = ""
        self._line_count = 0
        self.remove_class("active")
        self.remove_class("collapsed")

    # ── Collapse toggle ──────────────────────────────────────────────────────

    def action_toggle_panel(self) -> None:
        if not self._current_step_id:
            return
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.add_class("collapsed")
        else:
            self.remove_class("collapsed")
        self._render_header()

    # ── Internal helpers ────────────────────────────────────────────────────

    def _tick_timer(self) -> None:
        if self._current_step_id and not self._collapsed:
            self._render_header()

    def _elapsed_str(self) -> str:
        if not self._step_started_at:
            return "00:00"
        elapsed = int(time.monotonic() - self._step_started_at)
        mins = elapsed // 60
        secs = elapsed % 60
        return f"{mins:02d}:{secs:02d}"

    def _render_header(self) -> None:
        text = Text()
        arrow = "▶" if self._collapsed else "▼"
        text.append(f"{arrow} AGENT OUTPUT", style="bold #555570")

        if self._current_step_id:
            elapsed = self._elapsed_str()
            text.append("  ", style="#555570")
            text.append(f"[{self._current_step_id}", style="#555570")
            if self._current_tool:
                text.append(f" · {self._current_tool}", style="#555570")
            text.append(f" · {elapsed}]", style="#555570")

            if not self._collapsed:
                text.append("  ", style="#555570")
                text.append(f"{self._line_count} line(s)", style="#444460")

        text.append("  ctrl+o=toggle", style="#1a1a3a")
        self.query_one("#output-header", Static).update(text)
