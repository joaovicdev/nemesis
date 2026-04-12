"""MainScreen — primary layout: header, left panel, chat, status bar."""

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
from nemesis.db.models import ChatEntry, FindingSeverity, Project, Session
from nemesis.tui.widgets.chat_panel import ChatPanel
from nemesis.tui.widgets.context_panel import ContextPanel, ProjectSummary
from nemesis.tui.widgets.status_bar import StatusBar
from nemesis.tui.widgets.task_list import AttackPlan, AttackTask, TaskList, TaskStatus

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
            # Fresh start from splash — open the project wizard after first render
            self.set_timer(0.05, self._open_new_project_wizard)

    def _open_new_project_wizard(self) -> None:
        """Auto-open the new-project modal on first launch (no startup project given)."""
        from nemesis.tui.screens.new_project import NewProjectScreen

        self.app.push_screen(NewProjectScreen(), self._on_project_created)

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

        # ── Shortcut commands handled without Orchestrator ─────────────────
        if lower in ("new project", "new", "n"):
            self.action_new_project()
            return

        if lower in ("load project", "load", "l"):
            self.action_load_project()
            return

        # ── Step-mode confirmation gate ────────────────────────────────────
        if self._pending_confirmation is not None:
            if lower in ("y", "yes", ""):
                if self._orchestrator_busy:
                    self._reply_system(chat, "NEMESIS is busy — please wait a moment.")
                    return
                action_id = self._pending_confirmation
                self._pending_confirmation = None
                logger.log(  # type: ignore[attr-defined]
                    25,  # AUDIT level
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
                    25,  # AUDIT level
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

        # ── Route through Orchestrator if available ────────────────────────
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

        # ── Fallback: no project loaded yet ───────────────────────────────
        self._reply_system(
            chat,
            "No active project. Use [bold #00d4ff]ctrl+n[/] to start one.",
        )

    async def _route_to_orchestrator(self, text: str) -> None:
        """Send a message through the Orchestrator and display the response."""
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
        """Execute a step-mode confirmed action."""
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

        if response.requires_confirmation and response.confirmation_action_id:
            self._pending_confirmation = response.confirmation_action_id
            logger.debug(
                "Confirmation modal opened",
                extra={
                    "event": "tui.confirmation_modal_opened",
                    "action_id": response.confirmation_action_id,
                },
            )

        # Update context panel if findings were produced
        if response.findings and self._project_ctx:
            self._refresh_context_panel()

        # Update status bar phase if it changed
        if self._project_ctx:
            status = self.query_one("#status-bar", StatusBar)
            status.update_phase(self._project_ctx.current_phase.value.upper())

    # ── Task update callback (from Orchestrator) ───────────────────────────

    def _on_task_update(self, task_id: str, status: str, note: str) -> None:
        """Invoked by the Orchestrator when a task changes state."""
        task_list = self.query_one("#task-list", TaskList)
        try:
            task_status = TaskStatus(status)
        except ValueError:
            task_status = TaskStatus.PENDING

        if task_list.plan is not None:
            task_list.update_task_status(
                task_id, task_status, note if task_status == TaskStatus.FAILED else ""
            )
            return

        # First task for this session — create the plan on the fly.
        # The label is passed via note when status is "running".
        label = note if task_status == TaskStatus.RUNNING and note else task_id
        plan = AttackPlan(
            phase=self._project_ctx.current_phase.value.upper() if self._project_ctx else "RECON",
            tasks=[AttackTask(id=task_id, label=label, tool="", status=task_status)],
        )
        task_list.set_plan(plan)

    def _on_agent_output(self, task_id: str, line: str) -> None:
        """Invoked by the Orchestrator for each streamed stdout line from an executor."""
        if not line.strip():
            return
        chat = self.query_one("#chat-panel", ChatPanel)
        chat.append_agent_line(f"> {line}")

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
            extra={
                "event": "tui.project_loaded",
                "project_id": project.id,
            },
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

        # Shutdown any previous orchestrator before creating a new one
        if self._orchestrator is not None:
            await self._orchestrator.shutdown()

        self._orchestrator = Orchestrator(
            context=self._project_ctx,
            db=db,
            llm_client=llm_client,
            on_response=self._on_orchestrator_response,
            on_task_update=self._on_task_update,
            on_agent_output=self._on_agent_output,
        )
        await self._orchestrator.start()

        self._refresh_context_panel()

        status = self.query_one("#status-bar", StatusBar)
        status.update_project(project.name)
        status.update_phase(session.phase.value.upper())
        status.update_mode(project.mode.value)

        # Restore chat history (skip for new sessions)
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
            # Fresh session — show welcome, then auto-trigger recon suggestion
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
        """Ask the Orchestrator to plan and propose the first recon step."""
        if self._orchestrator is None:
            return
        # Yield to the event loop so the UI renders fully before the LLM call starts.
        await asyncio.sleep(0)
        self._orchestrator_busy = True
        chat = self.query_one("#chat-panel", ChatPanel)
        chat.append_system("Analyzing project context...")
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
