"""FindingDetailScreen — full-screen overlay for a single finding."""

from __future__ import annotations

import logging
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
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

_STATUS_COLORS: dict[FindingStatus, str] = {
    FindingStatus.RAW: "#555570",
    FindingStatus.UNVERIFIED: "#ffd700",
    FindingStatus.VALIDATED: "#00ff7f",
    FindingStatus.DISMISSED: "#555570",
    FindingStatus.REPORTED: "#00d4ff",
}


class FindingDetailScreen(ModalScreen[str | None]):
    """Full-screen overlay showing all fields of a single finding.

    Dismisses with "validated", "dismissed", or None (back).
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("v", "validate_finding", "Validate"),
        Binding("d", "dismiss_finding", "Dismiss"),
        Binding("escape", "go_back", "Back"),
        Binding("q", "go_back", "Back", show=False),
    ]

    DEFAULT_CSS = """
    FindingDetailScreen {
        align: center middle;
    }

    #detail-dialog {
        background: #0f0f1a;
        border: tall #1a1a3a;
        width: 76;
        height: auto;
        max-height: 46;
        padding: 0;
        overflow-y: auto;
    }

    #detail-header {
        background: #0a0a0a;
        border-bottom: tall #1a1a3a;
        padding: 1 2;
        height: auto;
    }

    #detail-body {
        padding: 1 2;
        height: auto;
    }

    #detail-evidence {
        background: #050510;
        border: solid #1a1a3a;
        padding: 1 2;
        margin: 0 2 1 2;
        height: auto;
        max-height: 8;
        overflow-y: auto;
    }

    #detail-footer {
        background: #0a0a0a;
        border-top: tall #1a1a3a;
        padding: 1 2;
        height: auto;
    }
    """

    def __init__(self, finding: Finding) -> None:
        super().__init__()
        self._finding = finding

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-dialog"):
            yield Static("", id="detail-header")
            yield Static("", id="detail-body")
            yield Static("", id="detail-evidence")
            yield Static("", id="detail-footer")

    def on_mount(self) -> None:
        self._render_all()
        self.query_one("#detail-dialog", Vertical).focus()

    def _render_all(self) -> None:
        self._render_header()
        self._render_body()
        self._render_evidence()
        self._render_footer()

    def _render_header(self) -> None:
        f = self._finding
        sev_color = _SEVERITY_COLORS.get(f.severity, "#555570")
        status_color = _STATUS_COLORS.get(f.status, "#555570")

        text = Text()
        text.append("◈ FINDING DETAIL\n", style="bold #00d4ff")
        text.append(f"  [{f.severity.value.upper()}]", style=f"bold {sev_color}")
        text.append(f"  {f.title}", style="bold #ffffff")
        text.append(f"  {f.status.value.upper()}", style=f"  {status_color}")
        self.query_one("#detail-header", Static).update(text)

    def _render_body(self) -> None:
        f = self._finding
        sev_color = _SEVERITY_COLORS.get(f.severity, "#555570")
        confidence_pct = round(f.confidence * 100)

        def row(label: str, value: str, value_style: str = "#c8c8d8") -> Text:
            t = Text()
            t.append(f"  {label:<14}", style="#555570")
            t.append(value, style=value_style)
            t.append("\n")
            return t

        text = Text()
        text.append_text(row("Target:", f.target or "—"))
        text.append_text(row("Port:", f.port or "—"))
        text.append_text(row("Service:", f.service or "—"))
        text.append_text(row("Tool source:", f.tool_source or "—"))
        text.append_text(
            row(
                "Confidence:",
                f"{confidence_pct}%",
                sev_color if confidence_pct >= 80 else "#c8c8d8",
            )
        )
        cves = ", ".join(f.cve_ids) if f.cve_ids else "none"
        text.append_text(row("CVEs:", cves))

        text.append("\n  Description:\n", style="#555570")
        text.append(f"  {f.description}\n", style="#c8c8d8")

        has_attack_steps = bool(getattr(f, "attack_path_steps", []))
        has_impact = bool(getattr(f, "impact_assessment", "").strip())
        has_guidance = bool(getattr(f, "remediation_guidance", "").strip())
        if has_attack_steps or has_impact or has_guidance:
            text.append("\n  Attacker vector:\n", style="#555570")

            if has_attack_steps:
                text.append("  Attack path:\n", style="#555570")
                for idx, step in enumerate(f.attack_path_steps, start=1):  # type: ignore[attr-defined]
                    text.append(f"    {idx}. {step}\n", style="#c8c8d8")
            if has_impact:
                text.append("\n  Impact:\n", style="#555570")
                text.append(f"  {f.impact_assessment}\n", style="#c8c8d8")  # type: ignore[attr-defined]
            if has_guidance:
                text.append("\n  Remediation (detailed):\n", style="#555570")
                text.append(f"  {f.remediation_guidance}\n", style="#c8c8d8")  # type: ignore[attr-defined]
            else:
                if f.remediation:
                    text.append("\n  Remediation (detailed):\n", style="#555570")
                    text.append("  See remediation summary above.\n", style="#c8c8d8")

        if f.remediation:
            text.append("\n  Remediation:\n", style="#555570")
            text.append(f"  {f.remediation}\n", style="#c8c8d8")

        self.query_one("#detail-body", Static).update(text)

    def _render_evidence(self) -> None:
        f = self._finding
        text = Text()
        text.append("  Raw evidence (truncated):\n", style="#555570")
        evidence = f.raw_evidence[:600] if f.raw_evidence else "— no raw evidence stored —"
        text.append(evidence, style="#444460")
        self.query_one("#detail-evidence", Static).update(text)

    def _render_footer(self) -> None:
        text = Text()
        if self._finding.status == FindingStatus.UNVERIFIED:
            text.append("[V] ", style="bold #00ff7f")
            text.append("Validate", style="#c8c8d8")
            text.append("    ", style="#555570")
            text.append("[D] ", style="bold #ff2040")
            text.append("Dismiss", style="#c8c8d8")
            text.append("    ", style="#555570")
        text.append("[ESC] ", style="bold #555570")
        text.append("Back", style="#c8c8d8")
        self.query_one("#detail-footer", Static).update(text)

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_validate_finding(self) -> None:
        if self._finding.status != FindingStatus.UNVERIFIED:
            return
        self.dismiss("validated")

    def action_dismiss_finding(self) -> None:
        if self._finding.status != FindingStatus.UNVERIFIED:
            return
        self.dismiss("dismissed")

    def action_go_back(self) -> None:
        self.dismiss(None)
