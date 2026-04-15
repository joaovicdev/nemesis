"""StepConfirmWidget — inline step-mode confirmation card."""

from __future__ import annotations

import logging
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static

from nemesis.db.models import PlanStep

logger = logging.getLogger(__name__)


class StepConfirmWidget(Widget):
    """Inline card rendered in the cards area when STEP mode pauses for confirmation.

    Posts typed messages that MainScreen handles to drive the plan loop:
    - RunStep(step_id)
    - SkipStep(step_id)
    - AbortPlan()
    """

    # ── Messages ────────────────────────────────────────────────────────────

    class RunStep(Message):
        """Posted when the user confirms the step should run."""

        def __init__(self, step_id: str) -> None:
            super().__init__()
            self.step_id = step_id

    class SkipStep(Message):
        """Posted when the user skips this step."""

        def __init__(self, step_id: str) -> None:
            super().__init__()
            self.step_id = step_id

    class AbortPlan(Message):
        """Posted when the user aborts the entire plan."""

    class ArgsEdited(Message):
        """Posted when the user edits the step args target."""

        def __init__(self, step_id: str, new_target: str) -> None:
            super().__init__()
            self.step_id = step_id
            self.new_target = new_target

    # ── Bindings ────────────────────────────────────────────────────────────

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("y", "run_step", "Run"),
        Binding("s", "skip_step", "Skip"),
        Binding("e", "edit_args", "Edit args"),
        Binding("x", "abort_plan", "Abort"),
    ]

    can_focus: bool = True

    _editing: reactive[bool] = reactive(False)

    DEFAULT_CSS = """
    StepConfirmWidget {
        background: #0f0f1a;
        border: tall #1a1a3a;
        padding: 1 2;
        margin: 1 0;
        height: auto;
    }

    StepConfirmWidget #confirm-content {
        height: auto;
    }

    StepConfirmWidget #confirm-edit-input {
        background: #080818;
        border: solid #1a1a3a;
        color: #c8c8d8;
        margin: 1 0 0 0;
        display: none;
    }

    StepConfirmWidget:focus-within {
        border: tall #00d4ff;
    }
    """

    def __init__(self, step: PlanStep, confirmed_deps: list[str] | None = None) -> None:
        super().__init__()
        self._step = step
        self._confirmed_deps = confirmed_deps or []

    def compose(self) -> ComposeResult:
        yield Static("", id="confirm-content")
        yield Input(placeholder="new target — press enter to confirm", id="confirm-edit-input")

    def on_mount(self) -> None:
        self._update_content()
        self.focus()

    def _update_content(self) -> None:
        step = self._step
        first_tool = step.required_tools[0] if step.required_tools else step.agent
        target = step.args.get("target", "?")
        extra_args = step.args.get("extra_args", [])
        args_str = " ".join(str(a) for a in extra_args) if extra_args else "—"
        reason = step.description

        text = Text()
        text.append("⚡ NEXT STEP  ", style="bold #ffd700")
        text.append(f"[{step.id}]", style="#555570")
        text.append("\n")
        text.append(f"  {step.name}", style="bold #c8c8d8")
        text.append(f" — {step.agent}", style="#555570")
        text.append("\n\n")

        def field(label: str, value: str, vstyle: str = "#c8c8d8") -> None:
            text.append(f"  {label:<10}", style="#555570")
            text.append(value, style=vstyle)
            text.append("\n")

        field("Tool:", first_tool, "#00d4ff")
        field("Target:", target)
        field("Args:", args_str)

        if reason:
            wrapped = reason[:80] + ("…" if len(reason) > 80 else "")
            text.append(f"  {'Reason:':<10}", style="#555570")
            text.append(wrapped, style="italic #555570")
            text.append("\n")

        # Dependency satisfaction
        if step.depends_on:
            text.append("\n  Deps satisfied: ", style="#555570")
            for dep_id in step.depends_on:
                ok = dep_id in self._confirmed_deps
                text.append(dep_id, style="#c8c8d8")
                text.append(" ✓  " if ok else " ✗  ", style="#00ff7f" if ok else "#ff2040")
            text.append("\n")

        text.append("\n")
        text.append("[Y] ", style="bold #00ff7f")
        text.append("Run", style="#c8c8d8")
        text.append("    ", style="#555570")
        text.append("[S] ", style="bold #ffd700")
        text.append("Skip", style="#c8c8d8")
        text.append("    ", style="#555570")
        text.append("[E] ", style="bold #00d4ff")
        text.append("Edit args", style="#c8c8d8")
        text.append("    ", style="#555570")
        text.append("[X] ", style="bold #ff2040")
        text.append("Abort", style="#c8c8d8")

        self.query_one("#confirm-content", Static).update(text)

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_run_step(self) -> None:
        if self._editing:
            return
        logger.debug(
            "Step confirmed via StepConfirmWidget",
            extra={"event": "tui.step_confirmed", "step_id": self._step.id},
        )
        self.post_message(self.RunStep(self._step.id))
        self.remove()

    def action_skip_step(self) -> None:
        if self._editing:
            return
        logger.debug(
            "Step skipped via StepConfirmWidget",
            extra={"event": "tui.step_skipped", "step_id": self._step.id},
        )
        self.post_message(self.SkipStep(self._step.id))
        self.remove()

    def action_abort_plan(self) -> None:
        if self._editing:
            self._cancel_edit()
            return
        logger.debug(
            "Plan aborted via StepConfirmWidget",
            extra={"event": "tui.plan_aborted"},
        )
        self.post_message(self.AbortPlan())
        self.remove()

    def action_edit_args(self) -> None:
        if self._editing:
            return
        target = self._step.args.get("target", "")
        edit_input = self.query_one("#confirm-edit-input", Input)
        edit_input.styles.display = "block"
        edit_input.value = target
        edit_input.focus()
        self._editing = True

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not self._editing:
            return
        new_target = event.value.strip()
        if new_target:
            self._step.args["target"] = new_target
            self.post_message(self.ArgsEdited(self._step.id, new_target))
        self._cancel_edit()

    def _cancel_edit(self) -> None:
        edit_input = self.query_one("#confirm-edit-input", Input)
        edit_input.styles.display = "none"
        self._editing = False
        self._update_content()
        self.focus()
