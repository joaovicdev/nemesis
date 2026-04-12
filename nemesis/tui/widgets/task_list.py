"""TaskList widget — displays the current attack plan with per-step status."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class AttackTask:
    """Single step in the attack plan."""

    id: str
    label: str
    tool: str
    status: TaskStatus = TaskStatus.PENDING
    note: str = ""
    elapsed_seconds: int = 0
    started_at: float | None = None


@dataclass
class AttackPlan:
    """Ordered list of tasks for the current phase."""

    phase: str
    tasks: list[AttackTask] = field(default_factory=list)


# Icons per status
_STATUS_ICON: dict[TaskStatus, tuple[str, str]] = {
    TaskStatus.DONE:    ("✓", "#00ff7f"),
    TaskStatus.RUNNING: ("⚡", "#ffd700"),
    TaskStatus.FAILED:  ("✗", "#ff2040"),
    TaskStatus.SKIPPED: ("–", "#555570"),
    TaskStatus.PENDING: ("○", "#555570"),
}


class TaskList(Widget):
    """Left-panel widget showing the attack plan and task statuses."""

    DEFAULT_CSS = """
    TaskList {
        background: #0f0f1a;
        padding: 1 2;
    }
    """

    plan: reactive[AttackPlan | None] = reactive(None)

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
        for task in self.plan.tasks:
            if task.status == TaskStatus.RUNNING and task.started_at is not None:
                task.elapsed_seconds = int(time.monotonic() - task.started_at)
                updated = True
        if updated:
            self._refresh_content(self.plan)

    def _refresh_content(self, plan: AttackPlan | None) -> None:
        widget = self.query_one("#task-content", Static)
        widget.update(self._build_content(plan))

    def _build_content(self, plan: AttackPlan | None) -> Text:
        text = Text()
        text.append("◈ ATTACK PLAN\n", style="bold #00d4ff")

        if plan is None:
            text.append("\n  no plan yet\n", style="italic #555570")
            text.append("  start a project to\n", style="#555570")
            text.append("  generate a plan\n", style="#555570")
            return text

        text.append(f"\n  {plan.phase}\n", style="bold #ffd700")

        for task in plan.tasks:
            icon, color = _STATUS_ICON[task.status]
            style = "bold" if task.status == TaskStatus.RUNNING else ""

            text.append(f"  {icon} ", style=f"{style} {color}")
            if task.status == TaskStatus.RUNNING:
                label_style = "#00d4ff"
            elif task.status == TaskStatus.DONE:
                label_style = "#555570"
            elif task.status == TaskStatus.FAILED:
                label_style = "#ff2040"
            else:
                label_style = "#555570"

            show_timer = task.status in (TaskStatus.RUNNING, TaskStatus.DONE, TaskStatus.FAILED)
            if show_timer and task.elapsed_seconds > 0:
                mins = task.elapsed_seconds // 60
                secs = task.elapsed_seconds % 60
                timer_str = f"  [{mins:02d}:{secs:02d}]"
                text.append(f"{task.label}", style=label_style)
                text.append(f"{timer_str}\n", style="#555570")
            else:
                text.append(f"{task.label}\n", style=label_style)

            if task.note and task.status in (TaskStatus.RUNNING, TaskStatus.FAILED):
                text.append(f"    {task.note}\n", style="italic #555570")

        return text

    def set_plan(self, plan: AttackPlan | None) -> None:
        self.plan = plan

    def update_task_status(self, task_id: str, status: TaskStatus, note: str = "") -> None:
        if self.plan is None:
            return
        for task in self.plan.tasks:
            if task.id == task_id:
                if status == TaskStatus.RUNNING and task.started_at is None:
                    task.started_at = time.monotonic()
                    task.elapsed_seconds = 0
                elif status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.SKIPPED):
                    if task.started_at is not None:
                        task.elapsed_seconds = int(time.monotonic() - task.started_at)
                task.status = status
                task.note = note
                break
        self._refresh_content(self.plan)
