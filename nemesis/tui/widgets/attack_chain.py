"""AttackChainWidget — suggested follow-up actions after new findings."""

from __future__ import annotations

import logging
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from nemesis.db.models import AttackChainSuggestion

logger = logging.getLogger(__name__)


class AttackChainWidget(Widget):
    """Card listing LLM-suggested chain actions; pick with 1–9 or dismiss with D."""

    class SuggestionSelected(Message):
        """User chose a numbered suggestion."""

        def __init__(self, suggestion: AttackChainSuggestion) -> None:
            super().__init__()
            self.suggestion = suggestion

    class Dismissed(Message):
        """User dismissed the suggestion card."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("1", "pick_1", "1", show=False),
        Binding("2", "pick_2", "2", show=False),
        Binding("3", "pick_3", "3", show=False),
        Binding("4", "pick_4", "4", show=False),
        Binding("5", "pick_5", "5", show=False),
        Binding("6", "pick_6", "6", show=False),
        Binding("7", "pick_7", "7", show=False),
        Binding("8", "pick_8", "8", show=False),
        Binding("9", "pick_9", "9", show=False),
        Binding("d", "dismiss", "Dismiss", show=False),
    ]

    can_focus: bool = True

    DEFAULT_CSS = """
    AttackChainWidget {
        background: #0f0f1a;
        border: tall #1a1a3a;
        padding: 1 2;
        margin: 1 0;
        height: auto;
    }

    AttackChainWidget:focus-within {
        border: tall #00d4ff;
    }

    AttackChainWidget #chain-content {
        height: auto;
    }
    """

    def __init__(self, suggestions: list[AttackChainSuggestion]) -> None:
        super().__init__()
        self._suggestions = suggestions

    def compose(self) -> ComposeResult:
        yield Static("", id="chain-content")

    def on_mount(self) -> None:
        self._update_content()
        self.focus()

    def _update_content(self) -> None:
        text = Text()
        text.append("⛓ SUGGESTED NEXT STEPS  ", style="bold #ffd700")
        text.append("(press 1–9 to run, [D] dismiss)\n", style="#555570")

        for i, s in enumerate(self._suggestions[:9], start=1):
            text.append("\n", style="")
            text.append(f"  [{i}] ", style="bold #00d4ff")
            text.append(s.action, style="bold #c8c8d8")
            if s.destructive:
                text.append("  [destructive]", style="bold #ff2040")
            text.append("\n", style="")
            text.append("      ", style="")
            text.append(f"`{s.tool}`", style="#00ff7f")
            text.append(" on ", style="#555570")
            text.append(s.target, style="#c8c8d8")
            if s.port:
                text.append(f"  ·  port {s.port}", style="#555570")
            text.append("\n", style="")
            if s.rationale:
                r = s.rationale[:100] + ("…" if len(s.rationale) > 100 else "")
                text.append(f"      _{r}_\n", style="italic #555570")

        self.query_one("#chain-content", Static).update(text)

    def _select_index(self, idx: int) -> None:
        if 0 <= idx < len(self._suggestions):
            sug = self._suggestions[idx]
            logger.debug(
                "Attack chain suggestion selected",
                extra={"event": "tui.chain_selected", "tool": sug.tool},
            )
            self.post_message(self.SuggestionSelected(sug))
            self.remove()

    def action_pick_1(self) -> None:
        self._select_index(0)

    def action_pick_2(self) -> None:
        self._select_index(1)

    def action_pick_3(self) -> None:
        self._select_index(2)

    def action_pick_4(self) -> None:
        self._select_index(3)

    def action_pick_5(self) -> None:
        self._select_index(4)

    def action_pick_6(self) -> None:
        self._select_index(5)

    def action_pick_7(self) -> None:
        self._select_index(6)

    def action_pick_8(self) -> None:
        self._select_index(7)

    def action_pick_9(self) -> None:
        self._select_index(8)

    def action_dismiss(self) -> None:
        logger.debug("Attack chain widget dismissed", extra={"event": "tui.chain_dismissed"})
        self.post_message(self.Dismissed())
        self.remove()
