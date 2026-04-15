"""MainScreen — primary layout: header, left panel, chat, agent output, cards."""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static

from nemesis.agents.orchestrator import Orchestrator, OrchestratorResponse
from nemesis.core.project import ProjectContext
from nemesis.db.models import (
    AttackPlan,
    ChatEntry,
    Finding,
    FindingSeverity,
    FindingStatus,
    PlanStep,
    PlanStepStatus,
    Project,
    Session,
)
from nemesis.tui.widgets.agent_output import AgentOutputPanel
from nemesis.tui.widgets.chat_panel import ChatPanel
from nemesis.tui.widgets.context_panel import ContextPanel, ProjectSummary
from nemesis.tui.widgets.finding_card import FindingCard
from nemesis.tui.widgets.status_bar import StatusBar
from nemesis.tui.widgets.step_confirm import StepConfirmWidget
from nemesis.tui.widgets.task_list import TaskList

logger = logging.getLogger(__name__)

_HEADER_LOGO = (
    "  [bold #00d4ff]NEMESIS[/]"
    "  [#1a1a3a]│[/]"
    "  [#555570]THE ADVERSARY[/]"
    "  [#1a1a3a]│[/]"
    "  [#555570]AI-Assisted Pentest Co-pilot[/]"
)


class MainScreen(Screen[None]):
    """The primary interface screen after boot."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+n", "new_project", "New Project"),
        Binding("ctrl+l", "load_project", "Load Project"),
        Binding("ctrl+r", "report", "Report", show=False),
        Binding("f2", "toggle_panel", "Toggle Panel", show=False),
    ]

    DEFAULT_CSS = """
    MainScreen {
        background: #0a0a0a;
        layout: vertical;
    }

    #main-header {
        background: #0a0a0a;
        border-bottom: tall #1a1a3a;
        height: 3;
        padding: 0 2;
        align: left middle;
        color: #00d4ff;
    }

    #main-body {
        layout: horizontal;
        height: 1fr;
    }

    #left-panel {
        background: #0f0f1a;
        border-right: tall #1a1a3a;
        width: 30%;
        min-width: 28;
        max-width: 40;
        layout: vertical;
        overflow-y: auto;
        scrollbar-color: #1a1a3a #0f0f1a;
        scrollbar-size: 1 1;
    }

    #right-panel {
        background: #0a0a0a;
        width: 1fr;
        layout: vertical;
    }

    #chat-panel {
        height: 1fr;
    }

    #cards-area {
        background: #0a0a0a;
        height: auto;
        max-height: 40;
        overflow-y: auto;
        padding: 0 1;
        scrollbar-color: #1a1a3a #0a0a0a;
        scrollbar-size: 1 1;
    }

    #header-bindings {
        color: #1a1a3a;
        width: auto;
    }
    """

    def __init__(
        self,
        project: Project | None = None,
        session: Session | None = None,
    ) -> None:
        super().__init__()
        self._project_ctx: ProjectContext | None = None
        self._orchestrator: Orchestrator | None = None
        self._pending_confirmation: str | None = None
        self._orchestrator_busy: bool = False
        self._startup_project = project
        self._startup_session = session
        # Step tracking for status bar
        self._steps_done: int = 0
        self._steps_total: int = 0
        # Active StepConfirmWidget id when one is showing
        self._active_confirm_widget_id: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="main-header"):
            yield Static(_HEADER_LOGO, markup=True)
            yield Static(
                "  [#1a1a3a]ctrl+n[/] [#555570]new[/]"
                "  [#1a1a3a]ctrl+l[/] [#555570]load[/]"
                "  [#1a1a3a]ctrl+r[/] [#555570]report[/]"
                "  [#1a1a3a]ctrl+c[/] [#555570]quit[/]",
                id="header-bindings",
                markup=True,
            )

        with Horizontal(id="main-body"):
            with Vertical(id="left-panel"):
                yield ContextPanel(id="context-panel")
                yield TaskList(id="task-list")

            with Vertical(id="right-panel"):
                yield ChatPanel(id="chat-panel")
                yield AgentOutputPanel(id="agent-output")
                yield Vertical(id="cards-area")

        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        logger.debug(
            "Screen mounted",
            extra={"event": "tui.screen_mounted", "screen": "MainScreen"},
        )
        status = self.query_one("#status-bar", StatusBar)
        status.update_model("ollama / llama3.1:8b")
        status.update_project("no project")
        status.update_phase("—")
        status.update_mode("step")
        if self._startup_project and self._startup_session:
            self.run_worker(
                self._activate_project(self._startup_project, self._startup_session),
                exclusive=True,
                name="activate-project",
            )
        else:
            self.call_after_refresh(self._show_idle_hint)

    def _show_idle_hint(self) -> None:
        try:
            chat = self.query_one("#chat-panel", ChatPanel)
            chat.append_system(
                "No project loaded. Press [bold]ctrl+n[/] to create a new engagement"
                " or [bold]ctrl+l[/] to load an existing one."
            )
        except Exception:
            pass

    # ── Chat routing ───────────────────────────────────────────────────────

    def on_chat_panel_user_message(self, event: ChatPanel.UserMessage) -> None:
        logger.debug(
            "User message submitted",
            extra={
                "event": "tui.user_message_submitted",
                "message_length": len(event.text),
            },
        )
        self._persist_chat_entry("user", event.text)
        self._handle_user_message(event.text)

    def _reply_system(self, chat: ChatPanel, content: str) -> None:
        chat.append_system(content)
        self._persist_chat_entry("system", content)

    def _reply_nemesis(self, chat: ChatPanel, content: str) -> None:
        chat.append_nemesis(content)
        self._persist_chat_entry("nemesis", content)

    def _handle_user_message(self, text: str) -> None:
        chat = self.query_one("#chat-panel", ChatPanel)
        lower = text.lower().strip()

        if lower in ("new project", "new", "n"):
            self.action_new_project()
            return

        if lower in ("load project", "load", "l"):
            self.action_load_project()
            return

        # Legacy text-based step-mode gate (kept for typed "y"/"n" in chat)
        if self._pending_confirmation is not None:
            if lower in ("y", "yes", ""):
                if self._orchestrator_busy:
                    self._reply_system(chat, "NEMESIS is busy — please wait a moment.")
                    return
                action_id = self._pending_confirmation
                self._pending_confirmation = None
                logger.log(  # type: ignore[attr-defined]
                    25,
                    "Confirmation modal answered",
                    extra={
                        "event": "tui.confirmation_modal_answered",
                        "action_id": action_id,
                        "accepted": True,
                    },
                )
                self.run_worker(
                    self._run_confirmed(action_id),
                    exclusive=False,
                    name="orchestrator-call",
                )
                return
            if lower in ("n", "no", "cancel"):
                action_id = self._pending_confirmation
                self._pending_confirmation = None
                logger.log(  # type: ignore[attr-defined]
                    25,
                    "Confirmation modal answered",
                    extra={
                        "event": "tui.confirmation_modal_answered",
                        "action_id": action_id,
                        "accepted": False,
                    },
                )
                if self._orchestrator:
                    self._orchestrator.cancel_pending()
                self._reply_system(chat, "Cancelled.")
                return

        if self._orchestrator is not None:
            if self._orchestrator_busy:
                self._reply_system(chat, "NEMESIS is busy — please wait a moment.")
                return
            self.run_worker(
                self._route_to_orchestrator(text),
                exclusive=False,
                name="orchestrator-call",
            )
            return

        self._reply_system(
            chat,
            "No active project. Use [bold #00d4ff]ctrl+n[/] to start one.",
        )

    async def _route_to_orchestrator(self, text: str) -> None:
        if self._orchestrator is None:
            return
        self._orchestrator_busy = True
        chat = self.query_one("#chat-panel", ChatPanel)
        chat.set_thinking(True)
        try:
            response = await self._orchestrator.handle_message(text)
        except Exception:
            logger.exception("[MainScreen] Orchestrator error on message: %s", text[:80])
            self._reply_system(chat, "An error occurred. Check logs.")
            return
        else:
            self._on_orchestrator_response(response)
        finally:
            self._orchestrator_busy = False
            chat.set_thinking(False)

    async def _run_confirmed(self, action_id: str) -> None:
        if self._orchestrator is None:
            return
        self._orchestrator_busy = True
        chat = self.query_one("#chat-panel", ChatPanel)
        self._reply_system(chat, "Confirmed. Running...")
        chat.set_thinking(True)
        try:
            response = await self._orchestrator.confirm_and_execute(action_id)
        except Exception:
            logger.exception("[MainScreen] confirm_and_execute failed for %s", action_id)
            self._reply_system(chat, "Execution failed. Check logs.")
            return
        else:
            self._on_orchestrator_response(response)
        finally:
            self._orchestrator_busy = False
            chat.set_thinking(False)

    def _on_orchestrator_response(self, response: OrchestratorResponse) -> None:
        """Display an Orchestrator response in the chat and handle confirmation state."""
        chat = self.query_one("#chat-panel", ChatPanel)
        self._reply_nemesis(chat, response.text)

        # Render StepConfirmWidget instead of plain-text "y/n" prompt
        if response.requires_confirmation and response.confirmation_action_id:
            self._pending_confirmation = response.confirmation_action_id
            logger.debug(
                "Confirmation required",
                extra={
                    "event": "tui.confirmation_required",
                    "action_id": response.confirmation_action_id,
                },
            )
            # If the action references a pending_step on the orchestrator, render card
            if self._orchestrator is not None and self._orchestrator._pending_step is not None:
                self._show_step_confirm_widget(
                    self._orchestrator._pending_step,
                    response.confirmation_action_id,
                )

        # Render FindingCards for any new unverified findings
        if response.findings:
            unverified = [f for f in response.findings if f.status == FindingStatus.UNVERIFIED]
            for finding in unverified:
                self._show_finding_card(finding)
            self._refresh_context_panel()
            self._update_findings_count()

        # Update status bar phase if it changed
        if self._project_ctx:
            status = self.query_one("#status-bar", StatusBar)
            status.update_phase(self._project_ctx.current_phase.value.upper())

    # ── StepConfirmWidget integration ──────────────────────────────────────

    def _show_step_confirm_widget(self, step: PlanStep, action_id: str) -> None:
        """Mount a StepConfirmWidget in the cards area."""
        cards = self.query_one("#cards-area", Vertical)
        # Remove any existing confirm widget first
        for existing in cards.query(StepConfirmWidget):
            existing.remove()

        done_ids: list[str] = []
        if self._orchestrator and self._orchestrator._active_plan:
            from nemesis.db.models import PlanStepStatus as PSS

            done_ids = [s.id for s in self._orchestrator._active_plan.steps if s.status == PSS.DONE]

        widget = StepConfirmWidget(step, confirmed_deps=done_ids)
        self._active_confirm_widget_id = action_id
        cards.mount(widget)

    def on_step_confirm_widget_run_step(self, event: StepConfirmWidget.RunStep) -> None:
        """User confirmed the step via the confirm card."""
        if self._orchestrator_busy or self._orchestrator is None:
            return
        action_id = self._pending_confirmation
        if action_id is None:
            return
        self._pending_confirmation = None
        self._active_confirm_widget_id = None
        logger.log(  # type: ignore[attr-defined]
            25,
            "Step confirmed via StepConfirmWidget",
            extra={
                "event": "tui.step_confirmed",
                "action_id": action_id,
                "step_id": event.step_id,
                "accepted": True,
            },
        )
        self.run_worker(
            self._run_confirmed(action_id),
            exclusive=False,
            name="orchestrator-call",
        )

    def on_step_confirm_widget_skip_step(self, event: StepConfirmWidget.SkipStep) -> None:
        """User skipped the step via the confirm card."""
        self._pending_confirmation = None
        self._active_confirm_widget_id = None
        chat = self.query_one("#chat-panel", ChatPanel)
        self._reply_system(chat, f"Step [{event.step_id}] skipped.")
        # Mark the step as skipped in task list
        task_list = self.query_one("#task-list", TaskList)
        task_list.update_task_status(event.step_id, PlanStepStatus.SKIPPED)
        if self._orchestrator:
            # Continue the plan loop with the next step
            self.run_worker(
                self._continue_plan_after_skip(event.step_id),
                exclusive=False,
                name="orchestrator-call",
            )

    async def _continue_plan_after_skip(self, skipped_step_id: str) -> None:
        """Mark step as skipped on the orchestrator and advance the plan loop."""
        if self._orchestrator is None:
            return
        # Update the step status in the active plan
        if self._orchestrator._active_plan:
            for step in self._orchestrator._active_plan.steps:
                if step.id == skipped_step_id:
                    step.status = PlanStepStatus.SKIPPED
                    break
        # Clear pending step and continue
        self._orchestrator._pending_step = None
        if self._orchestrator._loop_plan:
            self._orchestrator_busy = True
            chat = self.query_one("#chat-panel", ChatPanel)
            chat.set_thinking(True)
            try:
                response = await self._orchestrator._pick_next_step_confirmation(
                    self._orchestrator._loop_plan
                )
            except Exception:
                logger.exception("[MainScreen] Plan continuation after skip failed.")
            else:
                self._on_orchestrator_response(response)
            finally:
                self._orchestrator_busy = False
                chat.set_thinking(False)

    def on_step_confirm_widget_abort_plan(self, _event: StepConfirmWidget.AbortPlan) -> None:
        """User aborted the plan via the confirm card."""
        self._pending_confirmation = None
        self._active_confirm_widget_id = None
        if self._orchestrator:
            self._orchestrator.cancel_pending()
        chat = self.query_one("#chat-panel", ChatPanel)
        self._reply_system(chat, "Plan aborted.")
        logger.log(  # type: ignore[attr-defined]
            25,
            "Plan aborted by user",
            extra={"event": "tui.plan_aborted"},
        )

    def on_step_confirm_widget_args_edited(self, event: StepConfirmWidget.ArgsEdited) -> None:
        """User edited a step arg — update the plan."""
        chat = self.query_one("#chat-panel", ChatPanel)
        self._reply_system(chat, f"Target for [{event.step_id}] updated to: {event.new_target}")

    # ── FindingCard integration ─────────────────────────────────────────────

    def _show_finding_card(self, finding: Finding) -> None:
        cards = self.query_one("#cards-area", Vertical)
        cards.mount(FindingCard(finding))

    def on_finding_card_validate_finding(self, event: FindingCard.ValidateFinding) -> None:
        self.run_worker(
            self._persist_finding_status(event.finding_id, FindingStatus.VALIDATED),
            exclusive=False,
            name="finding-persist",
        )
        self._update_findings_count()
        chat = self.query_one("#chat-panel", ChatPanel)
        self._reply_system(chat, f"Finding validated: {event.finding_id[:8]}")

    def on_finding_card_dismiss_finding(self, event: FindingCard.DismissFinding) -> None:
        self.run_worker(
            self._persist_finding_status(event.finding_id, FindingStatus.DISMISSED),
            exclusive=False,
            name="finding-persist",
        )
        self._update_findings_count()

    def on_finding_card_show_finding_detail(self, event: FindingCard.ShowFindingDetail) -> None:
        from nemesis.tui.screens.finding_detail import FindingDetailScreen

        def _on_detail_result(result: str | None) -> None:
            if result in ("validated", "dismissed"):
                new_status = (
                    FindingStatus.VALIDATED if result == "validated" else FindingStatus.DISMISSED
                )
                event.finding.status = new_status
                self.run_worker(
                    self._persist_finding_status(event.finding.id, new_status),
                    exclusive=False,
                    name="finding-persist",
                )
                self._update_findings_count()

        self.app.push_screen(FindingDetailScreen(event.finding), _on_detail_result)

    async def _persist_finding_status(self, finding_id: str, status: FindingStatus) -> None:
        if self._project_ctx is None:
            return
        # Update in context
        for finding in self._project_ctx.findings:
            if finding.id == finding_id:
                finding.status = status
                break
        # Persist to DB
        try:
            db = self.app.db  # type: ignore[attr-defined]
            await db.update_finding_status(finding_id, status)
        except Exception:
            logger.exception("Failed to persist finding status for %s.", finding_id)
        self._refresh_context_panel()

    # ── Plan approval flow ─────────────────────────────────────────────────

    def _on_plan_ready(self, plan: AttackPlan) -> None:
        """Called by Orchestrator after PlannerAgent finishes — before execution."""
        from nemesis.tui.screens.plan_approval import PlanApprovalScreen

        # Set the plan in the task list immediately so the user sees it
        task_list = self.query_one("#task-list", TaskList)
        task_list.set_plan(plan)

        self._steps_total = len(plan.steps)
        self._steps_done = 0
        status = self.query_one("#status-bar", StatusBar)
        status.update_step(0, self._steps_total)

        def _on_approved(approved_plan: AttackPlan | None) -> None:
            chat = self.query_one("#chat-panel", ChatPanel)
            if approved_plan is None:
                self._reply_system(chat, "Plan cancelled.")
                if self._orchestrator:
                    self._orchestrator.cancel_pending()
                return
            self._reply_system(chat, "Plan approved. Starting execution…")
            # Tell the orchestrator which plan to use (in case it was edited)
            if self._orchestrator is not None:
                self._orchestrator._active_plan = approved_plan
                self._orchestrator._loop_plan = approved_plan

            # Defer the worker until after the current refresh cycle so that
            # do_pop() + screen.remove() fully complete before we mount any
            # new widgets (StepConfirmWidget / FindingCard). Starting the
            # worker synchronously here would race with the modal's DOM
            # cleanup and produce a 'NoneType has no render_strips' crash.
            def _start_plan() -> None:
                self.run_worker(
                    self._execute_approved_plan(approved_plan),
                    exclusive=False,
                    name="plan-loop",
                )

            self.call_after_refresh(_start_plan)

        # Defer push_screen until after the refresh triggered by set_plan() and
        # update_step() above. This ensures TaskList and StatusBar have settled
        # in the compositor before the modal screen layers on top.
        self.call_after_refresh(
            lambda: self.app.push_screen(PlanApprovalScreen(plan), _on_approved)
        )

    async def _execute_approved_plan(self, plan: AttackPlan) -> None:
        """Run the approved plan loop and emit the final response."""
        if self._orchestrator is None:
            return
        self._orchestrator_busy = True
        chat = self.query_one("#chat-panel", ChatPanel)
        chat.set_thinking(True)
        try:
            response = await self._orchestrator.run_plan_loop(plan)
        except Exception:
            logger.exception("[MainScreen] run_plan_loop raised unexpectedly.")
            self._reply_system(chat, "Plan execution encountered an error. Check logs.")
            return
        else:
            self._on_orchestrator_response(response)
        finally:
            self._orchestrator_busy = False
            chat.set_thinking(False)

    # ── Task update callback (from Orchestrator) ───────────────────────────

    def _on_task_update(self, task_id: str, status: str, note: str) -> None:
        """Invoked by the Orchestrator when a step changes state."""
        task_list = self.query_one("#task-list", TaskList)
        try:
            step_status = PlanStepStatus(status)
        except ValueError:
            step_status = PlanStepStatus.PENDING

        task_list.update_task_status(task_id, step_status, note)

        # Update status bar step counter
        if task_list.plan:
            done = sum(1 for s in task_list.plan.steps if s.status == PlanStepStatus.DONE)
            total = len(task_list.plan.steps)
            label = note if step_status == PlanStepStatus.RUNNING else ""
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.update_step(done, total, label)

    def _on_agent_output(self, task_id: str, line: str) -> None:
        """Route streaming executor output to the AgentOutputPanel."""
        if not line.strip():
            return
        agent_out = self.query_one("#agent-output", AgentOutputPanel)
        # Start the step if this is the first line
        if agent_out._current_step_id != task_id:
            tool = ""
            if self._orchestrator and self._orchestrator._active_plan:
                for step in self._orchestrator._active_plan.steps:
                    if step.id == task_id and step.required_tools:
                        tool = step.required_tools[0]
                        break
            agent_out.start_step(task_id, tool)
        agent_out.push_line(line)

    def _on_task_complete(self, task_id: str) -> None:
        """Signal AgentOutputPanel that a step is done."""
        agent_out = self.query_one("#agent-output", AgentOutputPanel)
        if agent_out._current_step_id == task_id:
            agent_out.end_step()

    # ── New project ────────────────────────────────────────────────────────

    def action_new_project(self) -> None:
        from nemesis.tui.screens.new_project import NewProjectScreen

        self.app.push_screen(NewProjectScreen(), self._on_project_created)

    def _on_project_created(self, project_data: dict | None) -> None:
        if project_data is None:
            return
        self.run_worker(
            self._persist_and_activate_new(project_data),
            exclusive=True,
            name="activate-project",
        )

    async def _persist_and_activate_new(self, project_data: dict) -> None:
        chat = self.query_one("#chat-panel", ChatPanel)
        try:
            db = self.app.db  # type: ignore[attr-defined]
            project = Project(
                name=project_data["name"],
                targets=project_data["targets"],
                out_of_scope=project_data.get("out_of_scope", []),
                context=project_data.get("context", ""),
            )
            session = Session(project_id=project.id)
            await db.create_project(project)
            await db.create_session(session)
        except Exception:
            logger.exception(
                "Failed to persist new project",
                extra={"event": "tui.project_create_failed"},
            )
            chat.append_system("Could not save project to database.")
            return

        logger.info(
            "New project created",
            extra={
                "event": "tui.project_created",
                "project_id": project.id,
                "target_count": len(project.targets),
            },
        )
        await self._activate_project(project, session)

    # ── Load project ───────────────────────────────────────────────────────

    def action_load_project(self) -> None:
        from nemesis.tui.screens.load_project import LoadProjectScreen

        self.app.push_screen(LoadProjectScreen(), self._on_project_loaded)

    def _on_project_loaded(self, project: Project | None) -> None:
        if project is None:
            return
        self.run_worker(
            self._load_session_and_activate(project),
            exclusive=True,
            name="activate-project",
        )

    async def _load_session_and_activate(self, project: Project) -> None:
        chat = self.query_one("#chat-panel", ChatPanel)
        try:
            db = self.app.db  # type: ignore[attr-defined]
            session = await db.get_latest_session(project.id)
            if session is None:
                session = Session(project_id=project.id)
                await db.create_session(session)
        except Exception:
            logger.exception(
                "Failed to load session for project",
                extra={"event": "tui.project_load_failed", "project_id": project.id},
            )
            chat.append_system("Could not load project session from database.")
            return

        logger.info(
            "Project loaded",
            extra={"event": "tui.project_loaded", "project_id": project.id},
        )
        await self._activate_project(project, session)

    # ── Common activation ──────────────────────────────────────────────────

    async def _activate_project(self, project: Project, session: Session) -> None:
        """Load state from DB, update all UI components, build ProjectContext and Orchestrator."""
        db = self.app.db  # type: ignore[attr-defined]
        llm_client = self.app.llm_client  # type: ignore[attr-defined]
        chat = self.query_one("#chat-panel", ChatPanel)

        try:
            findings = await db.list_findings(project.id)
        except Exception:
            logger.exception("Failed to load findings for project %s.", project.id)
            findings = []

        self._project_ctx = ProjectContext(project=project, session=session)
        for finding in findings:
            self._project_ctx.add_finding(finding)

        if self._orchestrator is not None:
            await self._orchestrator.shutdown()

        self._orchestrator = Orchestrator(
            context=self._project_ctx,
            db=db,
            llm_client=llm_client,
            on_response=self._on_orchestrator_response,
            on_task_update=self._on_task_update,
            on_agent_output=self._on_agent_output,
            on_plan_ready=self._on_plan_ready,
        )
        await self._orchestrator.start()

        self._refresh_context_panel()
        self._update_findings_count()

        status = self.query_one("#status-bar", StatusBar)
        status.update_project(project.name)
        status.update_phase(session.phase.value.upper())
        status.update_mode(project.mode.value)

        try:
            history = await db.get_chat_history(session.id)
        except Exception:
            logger.exception("Failed to load chat history for session %s.", session.id)
            history = []

        if history:
            for entry in history:
                if entry.role == "user":
                    chat.append_user(entry.content)
                elif entry.role == "nemesis":
                    chat.append_nemesis(entry.content)
                else:
                    chat.append_system(entry.content)
        else:
            targets_str = ", ".join(project.targets)
            oos_str = ", ".join(project.out_of_scope) if project.out_of_scope else "none"
            chat.append_system(
                f"Project '[bold]{project.name}[/]' active.\n"
                f"  Targets:  {targets_str}\n"
                f"  Excl.:    {oos_str}"
            )
            self.run_worker(
                self._trigger_initial_recon(),
                exclusive=False,
                name="initial-recon",
            )

    async def _trigger_initial_recon(self) -> None:
        """Invoke PlannerAgent via Orchestrator; result will trigger on_plan_ready → PlanApprovalScreen."""
        if self._orchestrator is None:
            return
        await asyncio.sleep(0)
        self._orchestrator_busy = True
        chat = self.query_one("#chat-panel", ChatPanel)
        chat.append_system("Generating attack plan…")
        chat.set_thinking(True)
        try:
            response = await self._orchestrator.on_project_activated()
        except Exception:
            logger.exception("[MainScreen] on_project_activated() raised unexpectedly.")
            chat.append_system(
                "Could not reach the AI model. "
                "Make sure Ollama is running and try: [bold]run nmap[/]"
            )
            return
        else:
            self._on_orchestrator_response(response)
        finally:
            self._orchestrator_busy = False
            chat.set_thinking(False)

    # ── Context panel refresh ──────────────────────────────────────────────

    def _refresh_context_panel(self) -> None:
        if self._project_ctx is None:
            return
        project = self._project_ctx.project
        session = self._project_ctx.session
        findings = self._project_ctx.findings

        active = [f for f in findings if f.status.value != "dismissed"]
        counts = {s: 0 for s in FindingSeverity}
        for f in active:
            counts[f.severity] += 1

        summary = ProjectSummary(
            name=project.name,
            targets=project.targets,
            out_of_scope=project.out_of_scope,
            phase=session.phase.value.upper(),
            mode=project.mode.value,
            findings_critical=counts[FindingSeverity.CRITICAL],
            findings_high=counts[FindingSeverity.HIGH],
            findings_medium=counts[FindingSeverity.MEDIUM],
            findings_low=counts[FindingSeverity.LOW],
        )
        self.query_one("#context-panel", ContextPanel).set_project(summary)

    def _update_findings_count(self) -> None:
        if self._project_ctx is None:
            return
        active = [
            f for f in self._project_ctx.findings if f.status not in (FindingStatus.DISMISSED,)
        ]
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.update_findings_count(len(active))

    # ── Persistence helpers ────────────────────────────────────────────────

    async def _persist_mode(self) -> None:
        if self._project_ctx is None:
            return
        try:
            db = self.app.db  # type: ignore[attr-defined]
            await db.update_project(self._project_ctx.project)
        except Exception:
            logger.exception(
                "Failed to persist mode change for project %s.",
                self._project_ctx.project.id,
            )

    def _persist_chat_entry(self, role: str, content: str) -> None:
        if self._project_ctx is None:
            return
        entry = ChatEntry(
            project_id=self._project_ctx.project.id,
            session_id=self._project_ctx.session.id,
            role=role,
            content=content,
        )
        asyncio.create_task(self._write_chat_entry(entry))

    async def _write_chat_entry(self, entry: ChatEntry) -> None:
        try:
            db = self.app.db  # type: ignore[attr-defined]
            await db.append_chat(entry)
        except Exception:
            logger.exception("Failed to persist chat entry (role=%s).", entry.role)

    # ── Other actions ──────────────────────────────────────────────────────

    def action_report(self) -> None:
        chat = self.query_one("#chat-panel", ChatPanel)
        chat.append_system("Report generation not yet implemented.")

    def action_toggle_panel(self) -> None:
        pass
