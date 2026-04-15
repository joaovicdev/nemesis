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
from nemesis.agents.planner import PlannerAgent
from nemesis.agents.specialized import get_agent
from nemesis.core.logging_config import set_session_id
from nemesis.core.project import ProjectContext
from nemesis.db.database import Database
from nemesis.db.models import (
    AttackPlan,
    ChatEntry,
    ControlMode,
    Finding,
    FindingSeverity,
    FindingStatus,
    PlanStep,
    PlanStepStatus,
    SessionPhase,
    TaskRecord,
)

logger = logging.getLogger(__name__)

_CONVERSATION_SYSTEM = (
    "You are NEMESIS, an AI penetration testing co-pilot. "
    "You assist authorized security professionals during engagements. "
    "Be concise, technical, and actionable. "
    "Never suggest actions outside the defined project scope."
)

_PHASE_AFTER_PLAN: dict[SessionPhase, SessionPhase] = {
    SessionPhase.RECON: SessionPhase.ENUMERATION,
    SessionPhase.ENUMERATION: SessionPhase.EXPLOITATION,
    SessionPhase.EXPLOITATION: SessionPhase.POST_EXPLOITATION,
    SessionPhase.POST_EXPLOITATION: SessionPhase.REPORTING,
}


@dataclass
class OrchestratorResponse:
    """A response emitted by the Orchestrator to the TUI."""

    text: str
    findings: list[Finding] | None = None
    requires_confirmation: bool = False
    confirmation_action_id: str | None = None


@dataclass
class _PendingRecon:
    """Stores a single-tool plan waiting for step-mode user confirmation."""

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
        on_plan_ready: Callable[[AttackPlan], None] | None = None,
    ) -> None:
        self._context = context
        self._db = db
        self._llm = llm_client
        self._on_response = on_response
        self._on_task_update = on_task_update
        self._on_agent_output = on_agent_output
        self._on_plan_ready = on_plan_ready
        self._analyst = AnalystAgent(context, llm_client)
        self._planner = PlannerAgent(context, llm_client)
        self._running_executors: dict[str, asyncio.Task[ExecutorResult]] = {}
        self._pending_recon: _PendingRecon | None = None
        self._pending_step: PlanStep | None = None
        self._active_plan: AttackPlan | None = None
        self._loop_plan: AttackPlan | None = None
        self._loop_max_parallel: int = 1

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

        Invokes PlannerAgent to build a structured multi-step attack plan,
        persists it to the database, then either kicks off the first step
        (AUTO mode) or presents the plan to the user for approval (STEP/MANUAL).
        """
        targets = self._context.project.targets
        if not targets:
            return OrchestratorResponse(text="No targets configured. Add a target first.")

        goal = f"Full penetration test of {', '.join(targets)}" + (
            f" — {self._context.project.context}" if self._context.project.context else ""
        )

        plan = await self._planner.generate_plan(goal)
        await self._db.create_plan(plan)
        self._active_plan = plan

        plan_lines = [
            f"**Attack plan generated** — {len(plan.steps)} step(s):",
            f"_Goal: {plan.goal}_",
            "",
        ]
        for step in plan.steps:
            tools_str = ", ".join(step.required_tools) if step.required_tools else "—"
            dep_str = f" (after {', '.join(step.depends_on)})" if step.depends_on else ""
            plan_lines.append(f"  **{step.id}** · {step.name}{dep_str} · tools: `{tools_str}`")
            plan_lines.append(f"    _{step.description}_")

        plan_header = "\n".join(plan_lines)

        # If a TUI callback is registered, hand the plan off for approval and
        # let the TUI drive execution. Otherwise fall through to the loop here.
        if self._on_plan_ready is not None:
            self._on_plan_ready(plan)
            return OrchestratorResponse(
                text=f"{plan_header}\n\nReview the plan above and approve to start execution.",
            )

        self._emit_plan_to_tui(plan)
        loop_response = await self.run_plan_loop(plan)
        return OrchestratorResponse(
            text=f"{plan_header}\n\n{loop_response.text}",
            findings=loop_response.findings,
            requires_confirmation=loop_response.requires_confirmation,
            confirmation_action_id=loop_response.confirmation_action_id,
        )

    # ── Step-mode confirmation ──────────────────────────────────────────────

    async def confirm_and_execute(self, action_id: str) -> OrchestratorResponse:
        """
        Execute the pending recon plan after step-mode user confirmation.

        Args:
            action_id: Must match the `confirmation_action_id` from the pending response.
        """
        if action_id.startswith("step:"):
            self._context.record_destructive_confirmation(action_id)

            if self._pending_step is None:
                return OrchestratorResponse(text="No pending step to confirm.")

            step = self._pending_step
            self._pending_step = None
            step_response = await self._execute_step(step)

            if self._loop_plan is not None:
                continuation = await self._pick_next_step_confirmation(self._loop_plan)
                combined_text = step_response.text + "\n\n---\n\n" + continuation.text
                return OrchestratorResponse(
                    text=combined_text,
                    findings=step_response.findings,
                    requires_confirmation=continuation.requires_confirmation,
                    confirmation_action_id=continuation.confirmation_action_id,
                )
            return step_response

        if action_id == "initial_recon":
            self._context.record_destructive_confirmation(action_id)

            if self._pending_step is not None:
                step = self._pending_step
                self._pending_step = None
                return await self._execute_step(step)

            if self._pending_recon is not None:
                pending = self._pending_recon
                self._pending_recon = None
                return await self._execute_tool(pending.tool, pending.target, pending.extra_args)

        return OrchestratorResponse(text="No pending action to confirm.")

    def cancel_pending(self) -> None:
        """Discard the current pending confirmation without running it."""
        self._pending_recon = None
        self._pending_step = None
        self._loop_plan = None

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
        plan = self._active_plan
        if plan is None:
            return OrchestratorResponse(text="No plan generated yet.")
        lines = [f"**Attack plan — {plan.goal}**", ""]
        for step in plan.steps:
            icon = {"done": "✓", "running": "⚡", "failed": "✗", "pending": "○"}.get(
                step.status.value, "○"
            )
            lines.append(f"  {icon} [{step.id}] {step.name} ({step.agent})")
            if step.result_summary:
                lines.append(f"       → {step.result_summary}")
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

    # ── Specialized-agent step execution ──────────────────────────────────

    async def _execute_step(self, step: PlanStep) -> OrchestratorResponse:
        """
        Route a PlanStep to the correct specialized agent and persist results.

        Looks up the agent class via the AGENT_REGISTRY, instantiates it with
        the current session context, then executes the step. Findings added to
        the context by the agent are persisted to the database here.

        Falls back to _execute_tool() if the agent name is not registered.
        """
        try:
            agent_cls = get_agent(step.agent)
        except ValueError:
            logger.warning(
                "Unknown specialized agent — falling back to direct tool execution",
                extra={
                    "event": "orchestrator.agent_not_found",
                    "agent_name": step.agent,
                    "step_id": step.id,
                },
            )
            first_tool = step.required_tools[0] if step.required_tools else "nmap"
            target = step.args.get("target", self._context.project.targets[0])
            extra_args: list[str] = [str(a) for a in step.args.get("extra_args", [])]
            return await self._execute_tool(first_tool, target, extra_args)

        task_record = TaskRecord(
            project_id=self._context.project.id,
            session_id=self._context.session.id,
            label=step.name,
            tool=step.required_tools[0] if step.required_tools else step.agent,
            status="running",
        )
        await self._db.create_task(task_record)
        self._notify_task(step.id, "running", step.name)

        # Mark step as running in the active plan
        step.status = PlanStepStatus.RUNNING

        # Snapshot finding count before execution so we can identify new findings
        pre_count = len(self._context.findings)

        agent = agent_cls(self._context, self._llm, self._analyst)

        try:
            agent_response = await agent.execute(step)
        except Exception as exc:
            logger.error(
                "Specialized agent raised unexpected exception",
                extra={
                    "event": "orchestrator.agent_error",
                    "agent": step.agent,
                    "step_id": step.id,
                    "error_type": type(exc).__name__,
                },
            )
            step.status = PlanStepStatus.FAILED
            await self._db.update_task_status(task_record.id, "failed", str(exc))
            self._notify_task(step.id, "failed", str(exc))
            return OrchestratorResponse(text=f"Step '{step.name}' failed: {exc}")

        # Persist any findings the agent added to context
        new_findings = self._context.findings[pre_count:]
        for finding in new_findings:
            await self._db.create_finding(finding)

        # Update plan step status and summary
        if agent_response.action == "error":
            step.status = PlanStepStatus.FAILED
            task_status = "failed"
        elif agent_response.action == "skipped":
            step.status = PlanStepStatus.SKIPPED
            task_status = "done"
        else:
            step.status = PlanStepStatus.DONE
            task_status = "done"

        step.result_summary = agent_response.result[:200]
        step.findings_count = len(new_findings)

        await self._db.update_task_status(task_record.id, task_status)
        self._notify_task(step.id, task_status, agent_response.result)

        # Persist step status and summary to DB if this step belongs to an active plan
        if self._active_plan is not None:
            await self._db.update_plan_step(
                plan_id=self._active_plan.id,
                step_id=step.id,
                status=step.status,
                result_summary=step.result_summary,
                findings_count=step.findings_count,
            )

        lines = [f"**{step.name}** completed."]
        if agent_response.thought:
            lines.append(f"_{agent_response.thought}_")
        lines.append(agent_response.result)
        if new_findings:
            lines.append(f"\n{len(new_findings)} finding(s) extracted:")
            for f in new_findings:
                lines.append(
                    f"  [{f.severity.value.upper()}] {f.title} — {f.target}:{f.port or '?'}"
                )
        if agent_response.next_step:
            lines.append(f"\nSuggested next: _{agent_response.next_step}_")

        return OrchestratorResponse(
            text="\n".join(lines),
            findings=new_findings if new_findings else None,
        )

    # ── Execution loop ─────────────────────────────────────────────────────

    def _next_ready_steps(self, plan: AttackPlan, max_parallel: int) -> list[PlanStep]:
        """
        Return up to *max_parallel* steps whose dependencies are all DONE.

        A step is eligible when:
          - status == PENDING
          - every step id in step.depends_on has status == DONE
        """
        done_ids = {s.id for s in plan.steps if s.status == PlanStepStatus.DONE}
        ready = [
            s
            for s in plan.steps
            if s.status == PlanStepStatus.PENDING and all(dep in done_ids for dep in s.depends_on)
        ]
        return ready[:max_parallel]

    async def run_plan_loop(
        self,
        plan: AttackPlan,
        *,
        max_parallel: int = 1,
    ) -> OrchestratorResponse:
        """
        Drive the full AttackPlan to completion.

        In AUTO mode: runs all ready steps in a tight loop until the plan is
        done or blocked, emitting intermediate step results via _on_response.
        In STEP/MANUAL mode: pauses before each step and waits for user
        confirmation via the existing confirmation gate.
        """
        self._loop_plan = plan
        self._loop_max_parallel = max_parallel

        if self._context.mode == ControlMode.AUTO:
            return await self._run_loop_auto(plan, max_parallel)
        return await self._pick_next_step_confirmation(plan)

    async def _run_loop_auto(
        self,
        plan: AttackPlan,
        max_parallel: int,
    ) -> OrchestratorResponse:
        """Full AUTO-mode loop — runs every ready step until done or blocked."""
        while True:
            ready = self._next_ready_steps(plan, max_parallel)

            if not ready:
                pending = [s for s in plan.steps if s.status == PlanStepStatus.PENDING]
                if not pending:
                    return await self._finish_plan(plan)
                return self._make_blocked_response(plan)

            if max_parallel > 1 and len(ready) > 1:
                tasks = [asyncio.create_task(self._execute_step(s)) for s in ready]
                results: list[OrchestratorResponse | BaseException] = await asyncio.gather(
                    *tasks, return_exceptions=True
                )
                for step, result in zip(ready, results, strict=True):
                    if isinstance(result, BaseException):
                        step.status = PlanStepStatus.FAILED
                        err_resp = OrchestratorResponse(
                            text=f"Step '{step.name}' failed unexpectedly: {result}"
                        )
                        if self._on_response:
                            self._on_response(err_resp)
                    elif isinstance(result, OrchestratorResponse) and self._on_response:
                        self._on_response(result)
            else:
                for step in ready:
                    response = await self._execute_step(step)
                    if self._on_response:
                        self._on_response(response)

    async def _pick_next_step_confirmation(self, plan: AttackPlan) -> OrchestratorResponse:
        """
        STEP/MANUAL mode helper: select the next ready step, store it as
        _pending_step, and return a confirmation-gate response.

        Returns a plan-complete or blocked message if no step is ready.
        """
        ready = self._next_ready_steps(plan, 1)

        if not ready:
            pending = [s for s in plan.steps if s.status == PlanStepStatus.PENDING]
            if not pending:
                return await self._finish_plan(plan)
            return self._make_blocked_response(plan)

        step = ready[0]
        self._pending_step = step

        first_tool = step.required_tools[0] if step.required_tools else step.agent
        target = step.args.get(
            "target",
            self._context.project.targets[0] if self._context.project.targets else "?",
        )
        return OrchestratorResponse(
            text=(
                f"Next step: **{step.name}**\n"
                f"Run `{first_tool}` on `{target}`?\n\n"
                "**Continue? (y/n)**"
            ),
            requires_confirmation=True,
            confirmation_action_id=f"step:{step.id}",
        )

    async def _finish_plan(self, plan: AttackPlan) -> OrchestratorResponse:
        """Emit the plan-complete summary and advance the session phase."""
        total = len(plan.steps)
        done_count = sum(1 for s in plan.steps if s.status == PlanStepStatus.DONE)
        failed_count = sum(1 for s in plan.steps if s.status == PlanStepStatus.FAILED)

        findings = self._context.findings
        critical = sum(1 for f in findings if f.severity == FindingSeverity.CRITICAL)
        high = sum(1 for f in findings if f.severity == FindingSeverity.HIGH)
        medium = sum(1 for f in findings if f.severity == FindingSeverity.MEDIUM)

        current_phase = self._context.session.phase
        next_phase = _PHASE_AFTER_PLAN.get(current_phase)
        phase_line = ""
        if next_phase:
            self._context.advance_phase(next_phase)
            await self._db.update_session_phase(self._context.session.id, next_phase)
            phase_line = (
                f"\n  Phase advanced: {current_phase.value.upper()} → {next_phase.value.upper()}"
            )

        logger.info(
            "Attack plan complete",
            extra={
                "event": "orchestrator.plan_complete",
                "steps_total": total,
                "steps_done": done_count,
                "steps_failed": failed_count,
                "findings_total": len(findings),
            },
        )

        lines = [
            "**Plan complete.**",
            f"  Steps run:    {total}",
            f"  Steps done:   {done_count}",
            f"  Steps failed: {failed_count}",
            f"  Findings:     {len(findings)} ({critical} critical, {high} high, {medium} medium)",
        ]
        if phase_line:
            lines.append(phase_line)
        return OrchestratorResponse(text="\n".join(lines))

    def _make_blocked_response(self, plan: AttackPlan) -> OrchestratorResponse:
        """Return a warning when pending steps cannot advance (all deps failed/blocked)."""
        pending_names = [s.name for s in plan.steps if s.status == PlanStepStatus.PENDING]
        logger.warning(
            "Plan execution blocked — pending steps with unresolvable dependencies",
            extra={
                "event": "orchestrator.plan_blocked",
                "blocked_steps": pending_names,
            },
        )
        return OrchestratorResponse(
            text=(
                "**Plan blocked.** "
                f"The following steps have unresolvable dependencies: "
                f"{', '.join(pending_names)}. "
                "Check that earlier steps completed successfully."
            )
        )

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

    def _emit_plan_to_tui(self, plan: AttackPlan) -> None:
        """Push each plan step to the TUI task list as a pending task."""
        for step in plan.steps:
            self._notify_task(step.id, "pending", step.name)

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
