"""ContextPanel widget — shows the active project metadata."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


@dataclass
class ProjectSummary:
    """Lightweight snapshot of project state for the context panel."""

    name: str
    targets: list[str]
    phase: str
    findings_critical: int = 0
    findings_high: int = 0
    findings_medium: int = 0
    findings_low: int = 0
    mode: str = "step"


class ContextPanel(Widget):
    """Displays the active project context in the left sidebar."""

    DEFAULT_CSS = """
    ContextPanel {
        background: #0f0f1a;
        border-bottom: tall #1a1a3a;
        height: auto;
        padding: 1 2;
    }
    """

    project: reactive[ProjectSummary | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Static("", id="context-content")

    def watch_project(self, value: ProjectSummary | None) -> None:
        self._refresh_content(value)

    def on_mount(self) -> None:
        self._refresh_content(self.project)

    def _refresh_content(self, project: ProjectSummary | None) -> None:
        widget = self.query_one("#context-content", Static)
        widget.update(self._render(project))

    def _render(self, project: ProjectSummary | None) -> Text:
        text = Text()

        # Section title
        text.append("◈ PROJECT\n", style="bold #00d4ff")

        if project is None:
            text.append("\n  no active project\n", style="italic #555570")
            text.append("  ctrl+n  ", style="#1a1a3a")
            text.append("new project\n", style="#555570")
            return text

        # Project name
        text.append(f"\n  {project.name}\n", style="bold #c8c8d8")

        # Targets
        for target in project.targets[:3]:
            text.append(f"  {target}\n", style="#555570")
        if len(project.targets) > 3:
            text.append(f"  +{len(project.targets) - 3} more\n", style="#555570")

        # Phase
        text.append("\n  PHASE  ", style="#555570")
        text.append(f"{project.phase}\n", style="bold #ffd700")

        # Mode
        text.append("  MODE   ", style="#555570")
        mode_color = {"auto": "#ff2040", "step": "#00d4ff", "manual": "#555570"}.get(
            project.mode, "#555570"
        )
        text.append(f"{project.mode.upper()}\n", style=f"bold {mode_color}")

        # Findings summary
        total = (
            project.findings_critical
            + project.findings_high
            + project.findings_medium
            + project.findings_low
        )
        if total > 0:
            text.append("\n  FINDINGS\n", style="#555570")
            if project.findings_critical:
                text.append(
                    f"  ● {project.findings_critical} CRITICAL\n", style="bold #ff2040"
                )
            if project.findings_high:
                text.append(f"  ● {project.findings_high} HIGH\n", style="#ff6040")
            if project.findings_medium:
                text.append(f"  ● {project.findings_medium} MEDIUM\n", style="#ffd700")
            if project.findings_low:
                text.append(f"  ● {project.findings_low} LOW\n", style="#00ff7f")
        else:
            text.append("\n  no findings yet\n", style="#555570")

        return text

    def set_project(self, project: ProjectSummary | None) -> None:
        self.project = project
