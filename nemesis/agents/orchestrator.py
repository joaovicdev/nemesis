"""Orchestrator agent — the brain of NEMESIS.

The Orchestrator:
  - Maintains the ProjectContext across the full session
  - Receives natural language input from the user (via TUI)
  - Plans attack phases and proposes next steps
  - Spawns Executor agents and routes their output through the Analyst
  - Produces answers, summaries, and confirmations for the TUI

This is the only agent the TUI communicates with directly.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Callable

from nemesis.agents.analyst import AnalystAgent
from nemesis.agents.executor import (
    ExecutorResult,
    ScopeViolationError,
    ToolNotFoundError,
    get_executor,
)
from nemesis.core.project import ProjectContext
from nemesis.db.database import Database
from nemesis.db.models import ChatEntry, ControlMode, Finding, FindingStatus, TaskRecord


logger = logging.getLogger(__name__)


@dataclass
class OrchestratorResponse:
    """A response emitted by the Orchestrator to the TUI."""

    text: str
    findings: list[Finding] | None = None
    requires_confirmation: bool = False
    confirmation_action_id: str | None = None


class Orchestrator:
    """
    Central coordinating agent.

    Lifecycle:
        orc = Orchestrator(context, db, llm_client)
        await orc.start()
        response = await orc.handle_message("run initial recon")
        await orc.shutdown()
    """

    def __init__(
        self,
        context: ProjectContext,
        db: Database,
        llm_client: object,
        on_response: Callable[[OrchestratorResponse], None] | None = None,
        on_task_update: Callable[[str, str, str], None] | None = None,
    ) -> None:
        """
        Args:
            context: Active project context.
            db: Async database instance.
            llm_client: LiteLLM client (wired in next milestone).
            on_response: Callback invoked with each Orchestrator response (for TUI streaming).
            on_task_update: Callback(task_id, status, note) for TUI task list updates.
        """
        self._context = context
        self._db = db
        self._llm = llm_client
        self._on_response = on_response
        self._on_task_update = on_task_update
        self._analyst = AnalystAgent(context, llm_client)
        self._running_executors: dict[str, asyncio.Task[ExecutorResult]] = {}

    async def start(self) -> None:
        """Initialize the Orchestrator for a session."""
        logger.info(
            "[Orchestrator] Session started for project '%s'",
            self._context.project.name,
        )

    async def shutdown(self) -> None:
        """Cancel all running executors and clean up."""
        for task_id, task in list(self._running_executors.items()):
            if not task.done():
                task.cancel()
                logger.info("[Orchestrator] Cancelled executor %s", task_id)
        self._running_executors.clear()

    # ── Main entry point ───────────────────────────────────────────────────

    async def handle_message(self, text: str) -> OrchestratorResponse:
        """
        Process a user message and return a response.

        Routes to:
          - Built-in commands (mode, status, findings, plan, etc.)
          - Tool execution requests ("run nmap on X")
          - Free-form questions (LLM)
        """
        # Persist user message
        await self._db.append_chat(
            ChatEntry(
                project_id=self._context.project.id,
                session_id=self._context.session.id,
                role="user",
                content=text,
            )
        )

        lower = text.lower().strip()

        # ── Built-in command routing ───────────────────────────────────────
        if lower.startswith("mode "):
            return await self._handle_mode_change(lower.split(" ", 1)[1].strip())

        if lower in ("status", "show status"):
            return self._handle_status()

        if lower in ("findings", "show findings", "list findings"):
            return self._handle_findings()

        if lower in ("plan", "show plan", "attack plan"):
            return self._handle_plan()

        if lower.startswith("run "):
            return await self._handle_run_request(text[4:].strip())

        # ── LLM conversation (placeholder) ─────────────────────────────────
        response = await self._llm_response(text)
        await self._persist_response(response.text)
        return response

    # ── Mode change ────────────────────────────────────────────────────────

    async def _handle_mode_change(self, mode_str: str) -> OrchestratorResponse:
        try:
            mode = ControlMode(mode_str)
        except ValueError:
            return OrchestratorResponse(text=f"Unknown mode '{mode_str}'. Use: auto, step, manual.")
        self._context.set_mode(mode)
        await self._db.update_project(self._context.project)
        return OrchestratorResponse(text=f"Control mode set to {mode.value.upper()}.")

    # ── Status ─────────────────────────────────────────────────────────────

    def _handle_status(self) -> OrchestratorResponse:
        ctx = self._context
        validated = ctx.get_validated_findings()
        critical = ctx.get_critical_findings()
        lines = [
            f"Project: {ctx.project.name}",
            f"Targets: {', '.join(ctx.project.targets)}",
            f"Phase: {ctx.session.phase.value}",
            f"Mode: {ctx.mode.value}",
            f"Validated findings: {len(validated)} ({len(critical)} critical)",
        ]
        return OrchestratorResponse(text="\n".join(lines))

    # ── Findings ───────────────────────────────────────────────────────────

    def _handle_findings(self) -> OrchestratorResponse:
        validated = self._context.get_validated_findings()
        if not validated:
            return OrchestratorResponse(text="No validated findings yet.")
        lines = [f"Validated findings ({len(validated)}):"]
        for f in validated:
            lines.append(f"  [{f.severity.value.upper()}] {f.title} — {f.target}:{f.port}")
        return OrchestratorResponse(text="\n".join(lines), findings=validated)

    # ── Plan ───────────────────────────────────────────────────────────────

    def _handle_plan(self) -> OrchestratorResponse:
        # Placeholder — plan is generated by LLM in next milestone
        return OrchestratorResponse(
            text="Attack plan generation will be available once the LLM is connected."
        )

    # ── Tool execution ─────────────────────────────────────────────────────

    async def _handle_run_request(self, request: str) -> OrchestratorResponse:
        """
        Parse a 'run <tool> [on <target>]' request and execute it.

        PLACEHOLDER — full natural language parsing via LLM comes in next milestone.
        """
        parts = request.lower().split()
        tool = parts[0] if parts else ""
        target = self._context.project.targets[0] if self._context.project.targets else ""

        if "on" in parts:
            idx = parts.index("on")
            if idx + 1 < len(parts):
                target = parts[idx + 1]

        if not tool:
            return OrchestratorResponse(text="Please specify a tool to run.")

        # Scope check
        if not self._context.is_in_scope(target):
            return OrchestratorResponse(
                text=f"⚠ Target '{target}' is outside the project scope. "
                     "Add it to the project targets first."
            )

        return await self._execute_tool(tool, target)

    async def _execute_tool(
        self, tool: str, target: str, extra_args: list[str] | None = None
    ) -> OrchestratorResponse:
        """Spawn an executor, run it, route output through Analyst."""
        task_id = str(uuid.uuid4())[:8]

        # Create task record
        task_record = TaskRecord(
            project_id=self._context.project.id,
            session_id=self._context.session.id,
            label=f"{tool} on {target}",
            tool=tool,
            status="running",
        )
        await self._db.create_task(task_record)
        self._notify_task(task_record.id, "running", "")

        try:
            executor = get_executor(tool, task_id, target, extra_args)
        except ValueError as exc:
            await self._db.update_task_status(task_record.id, "failed", str(exc))
            self._notify_task(task_record.id, "failed", str(exc))
            return OrchestratorResponse(text=f"Unknown tool: {tool}")

        try:
            result = await executor.run()
        except ToolNotFoundError as exc:
            msg = str(exc)
            await self._db.update_task_status(task_record.id, "failed", msg)
            self._notify_task(task_record.id, "failed", msg)
            return OrchestratorResponse(text=f"Tool error: {msg}")
        except ScopeViolationError as exc:
            msg = str(exc)
            await self._db.update_task_status(task_record.id, "failed", msg)
            self._notify_task(task_record.id, "failed", msg)
            return OrchestratorResponse(text=f"Scope violation: {msg}")

        # Route through Analyst
        findings = await self._analyst.process(result)

        # Persist findings
        for finding in findings:
            await self._db.create_finding(finding)
            self._context.add_finding(finding)

        await self._db.update_task_status(task_record.id, "done")
        self._notify_task(task_record.id, "done", "")

        # Build response
        if not findings:
            response_text = (
                f"{tool} completed on {target}. No findings extracted. "
                "(LLM analysis not yet connected — raw output is saved.)"
            )
        else:
            lines = [f"{tool} complete. {len(findings)} finding(s):"]
            for f in findings:
                lines.append(f"  [{f.severity.value.upper()}] {f.title}")
            response_text = "\n".join(lines)

        return OrchestratorResponse(text=response_text, findings=findings or None)

    # ── LLM response (placeholder) ─────────────────────────────────────────

    async def _llm_response(self, text: str) -> OrchestratorResponse:
        """
        Send a message to the LLM with the full project context injected.

        PLACEHOLDER — LiteLLM integration will be added in the next milestone.
        """
        logger.debug("[Orchestrator] LLM call placeholder for: %s", text[:80])
        return OrchestratorResponse(
            text=(
                "The AI conversation engine is not yet connected. "
                "LLM integration (Ollama via LiteLLM) will be added in the next milestone.\n\n"
                "You can already use built-in commands:\n"
                "  status · findings · plan · run <tool> · mode <auto|step|manual>"
            )
        )

    # ── Helpers ────────────────────────────────────────────────────────────

    def _notify_task(self, task_id: str, status: str, note: str) -> None:
        if self._on_task_update:
            self._on_task_update(task_id, status, note)

    async def _persist_response(self, text: str) -> None:
        await self._db.append_chat(
            ChatEntry(
                project_id=self._context.project.id,
                session_id=self._context.session.id,
                role="nemesis",
                content=text,
            )
        )
