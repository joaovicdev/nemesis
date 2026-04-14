"""TaskList widget — displays the current attack plan with per-step status."""

from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from nemesis.db.models import AttackPlan, PlanStep, PlanStepStatus

# Re-export PlanStepStatus as TaskStatus for backward compatibility with callers
# that still use the old name (e.g. on_task_update in main.py).
TaskStatus = PlanStepStatus

# Icons per status
_STATUS_ICON: dict[PlanStepStatus, tuple[str, str]] = {
    PlanStepStatus.DONE: ("✓", "#00ff7f"),
    PlanStepStatus.RUNNING: ("⚡", "#ffd700"),
    PlanStepStatus.FAILED: ("✗", "#ff2040"),
    PlanStepStatus.SKIPPED: ("–", "#555570"),
    PlanStepStatus.PENDING: ("○", "#555570"),
}

_BAR_WIDTH = 20


def _build_progress_bar(done: int, total: int) -> Text:
    """Render a Unicode block progress bar with fraction and percentage."""
    pct = done / total if total else 0.0
    filled = round(_BAR_WIDTH * pct)
    bar = "━" * filled + "─" * (_BAR_WIDTH - filled)

    text = Text()
    text.append(f"  {bar}", style="#00d4ff")
    text.append(f"  {done}/{total}", style="#555570")
    text.append(f"  {round(pct * 100)}%", style="#ffd700")
    return text


class TaskList(Widget):
    """Left-panel widget showing the attack plan and step statuses.

    After PLAN 5 this widget accepts the domain AttackPlan from db/models
    and renders all steps upfront with a live progress bar.
    """

    DEFAULT_CSS = """
    TaskList {
        background: #0f0f1a;
        padding: 1 2;
    }
    """

    # Internal timer tracking per step id
    _started_at: dict[str, float]
    _elapsed: dict[str, int]

    plan: reactive[AttackPlan | None] = reactive(None)

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._started_at = {}
        self._elapsed = {}

    def compose(self) -> ComposeResult:
        yield Static("", id="task-content")

    def watch_plan(self, value: AttackPlan | None) -> None:
        self._refresh_content(value)

    def on_mount(self) -> None:
        self._refresh_content(self.plan)
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        if self.plan is None:
            return
        updated = False
        for step in self.plan.steps:
            if step.status == PlanStepStatus.RUNNING and step.id in self._started_at:
                self._elapsed[step.id] = int(time.monotonic() - self._started_at[step.id])
                updated = True
        if updated:
            self._refresh_content(self.plan)

    def _refresh_content(self, plan: AttackPlan | None) -> None:
        widget = self.query_one("#task-content", Static)
        widget.update(self._build_content(plan))

    def _build_content(self, plan: AttackPlan | None) -> Text:  # noqa: PLR0912
        text = Text()
        text.append("◈ ATTACK PLAN\n", style="bold #00d4ff")

        if plan is None:
            text.append("\n  no plan yet\n", style="italic #555570")
            text.append("  start a project to\n", style="#555570")
            text.append("  generate a plan\n", style="#555570")
            return text

        # Truncate goal for display
        goal = plan.goal[:36] + "…" if len(plan.goal) > 36 else plan.goal
        text.append(f"\n  {goal}\n", style="bold #ffd700")
        text.append("\n")

        # Group steps by phase using the agent name as a rough proxy
        prev_agent_group = ""
        for step in plan.steps:
            # Emit a lightweight phase header when the agent family changes
            agent_group = _agent_group(step)
            if agent_group != prev_agent_group:
                text.append(f"  {agent_group.upper()}\n", style="bold #555570")
                prev_agent_group = agent_group

            icon, icon_color = _STATUS_ICON[step.status]
            is_running = step.status == PlanStepStatus.RUNNING

            text.append(f"  {icon} ", style=f"{'bold ' if is_running else ''}{icon_color}")

            # Step label color
            if is_running:
                label_style = "bold #00d4ff"
            elif step.status == PlanStepStatus.DONE:
                label_style = "#555570"
            elif step.status == PlanStepStatus.FAILED:
                label_style = "#ff2040"
            else:
                label_style = "#555570"

            show_timer = step.status in (
                PlanStepStatus.RUNNING,
                PlanStepStatus.DONE,
                PlanStepStatus.FAILED,
            )
            elapsed_sec = self._elapsed.get(step.id, 0)

            if show_timer and elapsed_sec > 0:
                mins = elapsed_sec // 60
                secs = elapsed_sec % 60
                text.append(step.name, style=label_style)
                text.append(f"  [{mins:02d}:{secs:02d}]\n", style="#555570")
            else:
                text.append(f"{step.name}\n", style=label_style)

            # Dependency labels
            if step.depends_on:
                text.append(
                    f"      → {', '.join(step.depends_on)}\n",
                    style="#555570",
                )

            # Inline result note on failure
            if step.result_summary and step.status == PlanStepStatus.FAILED:
                note = (
                    step.result_summary[:50] + "…"
                    if len(step.result_summary) > 50
                    else step.result_summary
                )
                text.append(f"      {note}\n", style="italic #555570")

        # Progress bar
        total = len(plan.steps)
        done = sum(1 for s in plan.steps if s.status == PlanStepStatus.DONE)
        text.append("\n")
        text.append_text(_build_progress_bar(done, total))
        text.append("\n")

        return text

    # ── Public API ──────────────────────────────────────────────────────────

    def set_plan(self, plan: AttackPlan | None) -> None:
        """Set the current plan (accepts the domain AttackPlan from db/models)."""
        self._started_at.clear()
        self._elapsed.clear()
        self.plan = plan

    def update_task_status(self, task_id: str, status: PlanStepStatus, note: str = "") -> None:
        """Update a single step's status and re-render."""
        if self.plan is None:
            return
        for step in self.plan.steps:
            if step.id == task_id:
                if status == PlanStepStatus.RUNNING and task_id not in self._started_at:
                    self._started_at[task_id] = time.monotonic()
                    self._elapsed[task_id] = 0
                elif status in (PlanStepStatus.DONE, PlanStepStatus.FAILED, PlanStepStatus.SKIPPED):
                    if task_id in self._started_at:
                        self._elapsed[task_id] = int(time.monotonic() - self._started_at[task_id])
                step.status = status
                if note and status == PlanStepStatus.FAILED:
                    step.result_summary = note
                break
        self._refresh_content(self.plan)


# ── Helper ──────────────────────────────────────────────────────────────────────


def _agent_group(step: PlanStep) -> str:
    """Derive a display group name from the agent field."""
    agent = step.agent.lower()
    if "recon" in agent:
        return "recon"
    if "scan" in agent:
        return "scan"
    if "enum" in agent:
        return "enum"
    if "vuln" in agent:
        return "vuln"
    if "exploit" in agent:
        return "exploit"
    return agent
