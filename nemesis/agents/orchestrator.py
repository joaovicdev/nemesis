"""Orchestrator agent — the brain of NEMESIS.

The Orchestrator:
  - Maintains the ProjectContext across the full session
  - Receives natural language input from the user (via TUI)
  - Plans attack phases and proposes next steps via LLM
  - Spawns Executor agents and routes their output through the Analyst
  - Produces answers, summaries, and confirmations for the TUI

This is the only agent the TUI communicates with directly.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from nemesis.agents.analyst import AnalystAgent
from nemesis.agents.executor import (
    ExecutorResult,
    ScopeViolationError,
    ToolNotFoundError,
    get_executor,
)
from nemesis.agents.llm_client import LLMClient, LLMError
from nemesis.core.logging_config import set_session_id
from nemesis.core.project import ProjectContext
from nemesis.db.database import Database
from nemesis.db.models import (
    ChatEntry,
    ControlMode,
    Finding,
    FindingStatus,
    SessionPhase,
    TaskRecord,
)

logger = logging.getLogger(__name__)

_RECON_PLANNING_SYSTEM = (
    "You are NEMESIS, an AI penetration testing co-pilot. "
    "You help security professionals plan and execute authorized engagements. "
    "Always respond with valid JSON only — no markdown fences, no explanation outside the JSON."
)

_RECON_PLANNING_PROMPT = """\
New engagement just initialized:

{context_summary}

Choose the single best initial reconnaissance tool for these targets.
Consider the context (company type, objectives, constraints) to decide between:
  - nmap   → network/port scan (best for unknown IP ranges or mixed targets)
  - whois  → domain registration info (best for domain-only targets, OSINT start)
  - dig    → DNS enumeration (best when DNS recon is the priority)
  - gobuster → web directory brute-force (best when a web app URL is the target)
  - nikto  → web vulnerability scan (best for known web servers)

Reply with valid JSON only:
{{
  "tool": "nmap",
  "extra_args": ["-sV", "-sC", "-T4"],
  "reasoning": "short explanation (1-2 sentences)",
  "suggested_command": "nmap -sV -sC -T4 10.0.0.1",
  "user_message": "friendly message shown to the pentester (1-2 sentences)"
}}
"""

_CONVERSATION_SYSTEM = (
    "You are NEMESIS, an AI penetration testing co-pilot. "
    "You assist authorized security professionals during engagements. "
    "Be concise, technical, and actionable. "
    "Never suggest actions outside the defined project scope."
)


@dataclass
class OrchestratorResponse:
    """A response emitted by the Orchestrator to the TUI."""

    text: str
    findings: list[Finding] | None = None
    requires_confirmation: bool = False
    confirmation_action_id: str | None = None


@dataclass
class _PendingRecon:
    """Stores a recon plan waiting for step-mode user confirmation."""

    tool: str
    target: str
    extra_args: list[str] = field(default_factory=list)


class Orchestrator:
    """
    Central coordinating agent.

    Lifecycle:
        orc = Orchestrator(context, db, llm_client)
        await orc.start()
        response = await orc.on_project_activated()   # auto-trigger recon
        response = await orc.handle_message("run initial recon")
        await orc.shutdown()
    """

    def __init__(
        self,
        context: ProjectContext,
        db: Database,
        llm_client: LLMClient,
        on_response: Callable[[OrchestratorResponse], None] | None = None,
        on_task_update: Callable[[str, str, str], None] | None = None,
        on_agent_output: Callable[[str, str], None] | None = None,
    ) -> None:
        self._context = context
        self._db = db
        self._llm = llm_client
        self._on_response = on_response
        self._on_task_update = on_task_update
        self._on_agent_output = on_agent_output
        self._analyst = AnalystAgent(context, llm_client)
        self._running_executors: dict[str, asyncio.Task[ExecutorResult]] = {}
        self._pending_recon: _PendingRecon | None = None

    async def start(self) -> None:
        """Initialize the Orchestrator for a session."""
        set_session_id(self._context.session.id)
        self._context.log_activated()
        logger.info(
            "Orchestrator session started",
            extra={
                "event": "orchestrator.session_started",
                "project_id": self._context.project.id,
                "session_id": self._context.session.id,
                "mode": self._context.mode.value,
            },
        )

    async def shutdown(self) -> None:
        """Cancel all running executors and clean up."""
        cancelled = 0
        for task_id, task in list(self._running_executors.items()):
            if not task.done():
                task.cancel()
                cancelled += 1
                logger.info(
                    "Executor cancelled",
                    extra={
                        "event": "orchestrator.executor_cancelled",
                        "task_id": task_id,
                    },
                )
        self._running_executors.clear()
        logger.info(
            "Orchestrator shutdown",
            extra={
                "event": "orchestrator.session_ended",
                "executors_cancelled": cancelled,
            },
        )

    # ── Auto-trigger: called once after project activation ─────────────────

    async def on_project_activated(self) -> OrchestratorResponse:
        """
        Called once immediately after a project is loaded or created.

        Asks the LLM to choose the best initial recon tool based on the project
        context, then either runs it immediately (auto mode) or presents it to
        the user for confirmation (step mode).
        """
        context_summary = self._context.build_llm_context_summary()
        prompt = _RECON_PLANNING_PROMPT.format(context_summary=context_summary)

        try:
            plan = await self._llm.chat_json(
                [
                    {"role": "system", "content": _RECON_PLANNING_SYSTEM},
                    {"role": "user", "content": prompt},
                ]
            )

            tool = str(plan.get("tool", "nmap")).lower()
            extra_args: list[str] = [str(a) for a in plan.get("extra_args", [])]
            reasoning = str(plan.get("reasoning", ""))
            suggested_cmd = str(plan.get("suggested_command", ""))
            user_message = str(plan.get("user_message", f"I'll start with a {tool} scan."))

        except LLMError as exc:
            logger.warning(
                "LLM recon planning failed — falling back to nmap defaults",
                extra={
                    "event": "orchestrator.error",
                    "error_type": type(exc).__name__,
                    "context": "recon_planning",
                },
            )
            tool, extra_args, reasoning = "nmap", ["-sV", "-sC", "-T4"], ""
            targets = self._context.project.targets
            first_target = targets[0] if targets else "unknown"
            suggested_cmd = f"nmap -sV -sC -T4 {first_target}"
            user_message = (
                f"I couldn't reach the AI model, so I'll default to a standard "
                f"nmap service scan on {first_target}."
            )

        targets = self._context.project.targets
        if not targets:
            return OrchestratorResponse(text="No targets configured. Add a target first.")
        first_target = targets[0]

        mode = self._context.mode

        if mode == ControlMode.AUTO:
            header = f"{user_message}\n\nRunning: `{suggested_cmd}`"
            exec_response = await self._execute_tool(tool, first_target, extra_args)
            return OrchestratorResponse(
                text=f"{header}\n\n{exec_response.text}",
                findings=exec_response.findings,
            )

        # step / manual mode — present for confirmation
        self._pending_recon = _PendingRecon(tool=tool, target=first_target, extra_args=extra_args)
        reasoning_block = f"\n\n_{reasoning}_" if reasoning else ""
        response_text = (
            f"{user_message}{reasoning_block}\n\n"
            f"Suggested command:\n`{suggested_cmd}`\n\n"
            "**Run this? (y/n)**"
        )
        return OrchestratorResponse(
            text=response_text,
            requires_confirmation=True,
            confirmation_action_id="initial_recon",
        )

    # ── Step-mode confirmation ──────────────────────────────────────────────

    async def confirm_and_execute(self, action_id: str) -> OrchestratorResponse:
        """
        Execute the pending recon plan after step-mode user confirmation.

        Args:
            action_id: Must match the `confirmation_action_id` from the pending response.
        """
        if action_id == "initial_recon" and self._pending_recon is not None:
            plan = self._pending_recon
            self._pending_recon = None
            self._context.record_destructive_confirmation(action_id)
            return await self._execute_tool(plan.tool, plan.target, plan.extra_args)

        return OrchestratorResponse(text="No pending action to confirm.")

    def cancel_pending(self) -> None:
        """Discard the current pending confirmation without running it."""
        self._pending_recon = None

    # ── Main message entry point ───────────────────────────────────────────

    async def handle_message(self, text: str) -> OrchestratorResponse:
        """
        Process a user message and return a response.

        Routes built-in commands (status, findings, plan, run <tool>) before
        falling through to the LLM for free-form conversation.
        """
        logger.debug(
            "User message received",
            extra={
                "event": "orchestrator.message_received",
                "message_length": len(text),
            },
        )
        t0 = time.monotonic()

        await self._db.append_chat(
            ChatEntry(
                project_id=self._context.project.id,
                session_id=self._context.session.id,
                role="user",
                content=text,
            )
        )

        lower = text.lower().strip()

        if lower.startswith("mode "):
            response = await self._handle_mode_change(lower.split(" ", 1)[1].strip())
        elif lower in ("status", "show status"):
            response = self._handle_status()
        elif lower in ("findings", "show findings", "list findings"):
            response = self._handle_findings()
        elif lower in ("plan", "show plan", "attack plan"):
            response = self._handle_plan()
        elif lower.startswith("run "):
            response = await self._handle_run_request(text[4:].strip())
        else:
            response = await self._llm_response(text)
            await self._persist_response(response.text)

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        logger.info(
            "Orchestrator response sent",
            extra={
                "event": "orchestrator.response_sent",
                "has_findings": bool(response.findings),
                "requires_confirmation": response.requires_confirmation,
                "elapsed_ms": elapsed_ms,
            },
        )
        return response

    # ── Built-in commands ──────────────────────────────────────────────────

    async def _handle_mode_change(self, mode_str: str) -> OrchestratorResponse:
        try:
            mode = ControlMode(mode_str)
        except ValueError:
            return OrchestratorResponse(text=f"Unknown mode '{mode_str}'. Use: auto, step, manual.")
        self._context.set_mode(mode)
        await self._db.update_project(self._context.project)
        return OrchestratorResponse(text=f"Control mode set to **{mode.value.upper()}**.")

    def _handle_status(self) -> OrchestratorResponse:
        ctx = self._context
        validated = ctx.get_validated_findings()
        critical = ctx.get_critical_findings()
        lines = [
            f"**{ctx.project.name}**",
            f"Targets: {', '.join(ctx.project.targets)}",
            f"Phase: {ctx.session.phase.value}",
            f"Mode: {ctx.mode.value}",
            f"Validated findings: {len(validated)} ({len(critical)} critical)",
        ]
        return OrchestratorResponse(text="\n".join(lines))

    def _handle_findings(self) -> OrchestratorResponse:
        validated = self._context.get_validated_findings()
        if not validated:
            return OrchestratorResponse(text="No validated findings yet.")
        lines = [f"Validated findings ({len(validated)}):"]
        for f in validated:
            lines.append(f"  [{f.severity.value.upper()}] {f.title} — {f.target}:{f.port}")
        return OrchestratorResponse(text="\n".join(lines), findings=validated)

    def _handle_plan(self) -> OrchestratorResponse:
        ctx = self._context
        targets_str = ", ".join(ctx.project.targets)
        lines = [
            f"**Attack plan for {ctx.project.name}**",
            f"Targets: {targets_str}",
            f"Current phase: {ctx.session.phase.value}",
            "",
            "Recommended sequence:",
            "  1. Recon     — nmap service + version scan",
            "  2. Enum      — web dir brute-force / DNS enum (if applicable)",
            "  3. Exploit   — manual or guided based on findings",
            "  4. Report    — generate findings report",
        ]
        return OrchestratorResponse(text="\n".join(lines))

    # ── Explicit tool execution ("run nmap on ...") ────────────────────────

    async def _handle_run_request(self, request: str) -> OrchestratorResponse:
        parts = request.lower().split()
        tool = parts[0] if parts else ""
        target = self._context.project.targets[0] if self._context.project.targets else ""

        if "on" in parts:
            idx = parts.index("on")
            if idx + 1 < len(parts):
                target = parts[idx + 1]

        if not tool:
            return OrchestratorResponse(text="Please specify a tool to run.")

        if not self._context.is_in_scope(target):
            return OrchestratorResponse(
                text=(
                    f"Target '{target}' is outside the project scope. "
                    "Add it to the project targets first."
                )
            )

        return await self._execute_tool(tool, target)

    # ── Core execution ─────────────────────────────────────────────────────

    async def _execute_tool(
        self,
        tool: str,
        target: str,
        extra_args: list[str] | None = None,
    ) -> OrchestratorResponse:
        """Spawn an executor, run it, route output through Analyst."""
        task_id = str(uuid.uuid4())[:8]

        logger.info(
            "Tool selected for execution",
            extra={
                "event": "orchestrator.tool_selected",
                "tool": tool,
                "task_id": task_id,
            },
        )

        task_record = TaskRecord(
            project_id=self._context.project.id,
            session_id=self._context.session.id,
            label=f"{tool} on {target}",
            tool=tool,
            status="running",
        )
        await self._db.create_task(task_record)
        self._notify_task(task_record.id, "running", task_record.label)

        try:
            executor = get_executor(tool, task_id, target, extra_args)
        except ValueError as exc:
            logger.warning(
                "Unknown tool requested",
                extra={
                    "event": "orchestrator.error",
                    "error_type": "UnknownTool",
                    "tool": tool,
                    "task_id": task_id,
                },
            )
            await self._db.update_task_status(task_record.id, "failed", str(exc))
            self._notify_task(task_record.id, "failed", str(exc))
            return OrchestratorResponse(text=f"Unknown tool: {tool}")

        try:
            result = await executor.run_streaming(
                lambda line: self._on_raw_line(task_record.id, line)
            )
        except ToolNotFoundError as exc:
            msg = str(exc)
            logger.error(
                "Tool binary not found",
                extra={
                    "event": "orchestrator.error",
                    "error_type": "ToolNotFound",
                    "tool": tool,
                    "task_id": task_id,
                },
            )
            await self._db.update_task_status(task_record.id, "failed", msg)
            self._notify_task(task_record.id, "failed", msg)
            return OrchestratorResponse(text=f"Tool not found: {msg}")
        except ScopeViolationError as exc:
            msg = str(exc)
            logger.warning(
                "Scope violation blocked execution",
                extra={
                    "event": "orchestrator.error",
                    "error_type": "ScopeViolation",
                    "tool": tool,
                    "task_id": task_id,
                },
            )
            await self._db.update_task_status(task_record.id, "failed", msg)
            self._notify_task(task_record.id, "failed", msg)
            return OrchestratorResponse(text=f"Scope violation: {msg}")

        findings = await self._analyst.process(result)

        for finding in findings:
            await self._db.create_finding(finding)
            self._context.add_finding(finding)

        await self._db.update_task_status(task_record.id, "done")
        self._notify_task(task_record.id, "done", "")

        # Advance phase to ENUMERATION after first recon task completes
        if self._context.current_phase == SessionPhase.RECON:
            self._context.advance_phase(SessionPhase.ENUMERATION)
            await self._db.update_session_phase(self._context.session.id, SessionPhase.ENUMERATION)

        if not findings:
            return OrchestratorResponse(
                text=(
                    f"`{tool}` completed on `{target}` in "
                    f"{result.elapsed_seconds:.1f}s. No findings extracted."
                )
            )

        lines = [
            f"`{tool}` completed on `{target}` in {result.elapsed_seconds:.1f}s. "
            f"{len(findings)} finding(s):"
        ]
        for f in findings:
            status_tag = f.status.value if f.status != FindingStatus.UNVERIFIED else "unverified"
            lines.append(
                f"  [{f.severity.value.upper()}] {f.title}"
                f" — {f.target}:{f.port or '?'} [{status_tag}]"
            )
        return OrchestratorResponse(text="\n".join(lines), findings=findings)

    # ── LLM conversation ───────────────────────────────────────────────────

    async def _llm_response(self, text: str) -> OrchestratorResponse:
        """
        Send a free-form message to the LLM with the full project context injected.
        """
        context_summary = self._context.build_llm_context_summary()
        messages = [
            {"role": "system", "content": _CONVERSATION_SYSTEM},
            {
                "role": "system",
                "content": f"Current engagement context:\n{context_summary}",
            },
            {"role": "user", "content": text},
        ]

        try:
            reply = await self._llm.chat(messages)
            return OrchestratorResponse(text=reply)
        except LLMError as exc:
            logger.warning(
                "LLM conversation failed",
                extra={
                    "event": "orchestrator.error",
                    "error_type": type(exc).__name__,
                    "context": "llm_response",
                },
            )
            return OrchestratorResponse(
                text=(
                    "I couldn't reach the AI model right now. "
                    "Check that Ollama is running (`ollama serve`) and try again.\n\n"
                    "Built-in commands still work: "
                    "`status` · `findings` · `plan` · `run <tool>` · `mode <auto|step|manual>`"
                )
            )

    # ── Helpers ────────────────────────────────────────────────────────────

    def _notify_task(self, task_id: str, status: str, note: str) -> None:
        if self._on_task_update:
            self._on_task_update(task_id, status, note)

    def _on_raw_line(self, task_id: str, line: str) -> None:
        if self._on_agent_output:
            self._on_agent_output(task_id, line)

    async def _persist_response(self, text: str) -> None:
        await self._db.append_chat(
            ChatEntry(
                project_id=self._context.project.id,
                session_id=self._context.session.id,
                role="nemesis",
                content=text,
            )
        )
