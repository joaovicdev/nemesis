"""FindingCard — interactive inline finding validation card."""

from __future__ import annotations

import logging
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from nemesis.db.models import Finding, FindingSeverity, FindingStatus

logger = logging.getLogger(__name__)

_SEVERITY_COLORS: dict[FindingSeverity, str] = {
    FindingSeverity.CRITICAL: "#ff2040",
    FindingSeverity.HIGH: "#ff6600",
    FindingSeverity.MEDIUM: "#ffd700",
    FindingSeverity.LOW: "#00d4ff",
    FindingSeverity.INFO: "#555570",
}


class FindingCard(Widget):
    """Inline interactive card representing a single UNVERIFIED finding.

    Rendered in the cards area after each plan step completes. Posts:
    - ValidateFinding(finding_id)
    - DismissFinding(finding_id)
    - ShowFindingDetail(finding)
    """

    # ── Messages ────────────────────────────────────────────────────────────

    class ValidateFinding(Message):
        """Posted when the user validates this finding."""

        def __init__(self, finding_id: str) -> None:
            super().__init__()
            self.finding_id = finding_id

    class DismissFinding(Message):
        """Posted when the user dismisses this finding."""

        def __init__(self, finding_id: str) -> None:
            super().__init__()
            self.finding_id = finding_id

    class ShowFindingDetail(Message):
        """Posted when the user requests the full detail view."""

        def __init__(self, finding: Finding) -> None:
            super().__init__()
            self.finding = finding

    # ── Widget config ────────────────────────────────────────────────────────

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("v", "validate", "Validate"),
        Binding("d", "dismiss", "Dismiss"),
        Binding("right", "detail", "Details"),
        Binding("enter", "detail", "Details", show=False),
    ]

    can_focus: bool = True

    DEFAULT_CSS = """
    FindingCard {
        background: #0f0f1a;
        border: tall #1a1a3a;
        padding: 1 2;
        margin: 0 0 1 0;
        height: auto;
    }

    FindingCard:focus {
        border: tall #00d4ff;
    }

    FindingCard.severity-critical {
        border: tall #ff2040;
    }

    FindingCard.severity-high {
        border: tall #ff6600;
    }

    FindingCard.severity-medium {
        border: tall #ffd700;
    }

    FindingCard #card-content {
        height: auto;
    }
    """

    def __init__(self, finding: Finding) -> None:
        super().__init__()
        self._finding = finding

    def compose(self) -> ComposeResult:
        yield Static("", id="card-content")

    def on_mount(self) -> None:
        sev = self._finding.severity.value
        if sev in ("critical", "high", "medium"):
            self.add_class(f"severity-{sev}")
        self._update_content()

    def _update_content(self) -> None:
        f = self._finding
        sev_color = _SEVERITY_COLORS.get(f.severity, "#555570")
        confidence_pct = round(f.confidence * 100)
        service_line = f.service if f.service else ""

        text = Text()
        text.append(f"[{f.severity.value.upper()}] ", style=f"bold {sev_color}")
        text.append(f.title, style="bold #c8c8d8")
        text.append("\n")
        text.append("  Target: ", style="#555570")
        target_port = f"{f.target}:{f.port}" if f.port else f.target
        text.append(target_port or "—", style="#c8c8d8")
        text.append("  ·  Confidence: ", style="#555570")
        conf_color = (
            "#00ff7f"
            if confidence_pct >= 80
            else ("#ffd700" if confidence_pct >= 50 else "#ff2040")
        )
        text.append(f"{confidence_pct}%", style=conf_color)
        if service_line:
            text.append("  ·  Service: ", style="#555570")
            text.append(service_line, style="#c8c8d8")
        text.append("\n")

        if f.description:
            desc = f.description[:120] + ("…" if len(f.description) > 120 else "")
            text.append(f"\n  {desc}\n", style="#555570")

        text.append("\n")

        if f.status == FindingStatus.UNVERIFIED:
            text.append("[V] ", style="bold #00ff7f")
            text.append("Validate", style="#c8c8d8")
            text.append("    ", style="#555570")
            text.append("[D] ", style="bold #ff2040")
            text.append("Dismiss", style="#c8c8d8")
            text.append("    ", style="#555570")
        text.append("[→] ", style="bold #00d4ff")
        text.append("Details", style="#c8c8d8")

        self.query_one("#card-content", Static).update(text)

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_validate(self) -> None:
        if self._finding.status != FindingStatus.UNVERIFIED:
            return
        logger.debug(
            "Finding validated via card",
            extra={"event": "tui.finding_validated", "finding_id": self._finding.id},
        )
        self._finding.status = FindingStatus.VALIDATED
        self.post_message(self.ValidateFinding(self._finding.id))
        self._update_content()
        self.remove()

    def action_dismiss(self) -> None:
        if self._finding.status != FindingStatus.UNVERIFIED:
            return
        logger.debug(
            "Finding dismissed via card",
            extra={"event": "tui.finding_dismissed", "finding_id": self._finding.id},
        )
        self._finding.status = FindingStatus.DISMISSED
        self.post_message(self.DismissFinding(self._finding.id))
        self._update_content()
        self.remove()

    def action_detail(self) -> None:
        self.post_message(self.ShowFindingDetail(self._finding))
