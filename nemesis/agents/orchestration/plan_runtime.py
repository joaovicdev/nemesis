"""PlanRuntime — dirige o ciclo de execução do AttackPlan.

Em AUTO, roda cada step pronto (respeitando `depends_on`), opcionalmente em
paralelo. Em STEP/MANUAL, devolve a próxima confirmação pendente para a TUI.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from nemesis.agents.orchestration.callbacks import OrchestratorCallbacks
from nemesis.agents.orchestration.response import PHASE_AFTER_PLAN, OrchestratorResponse
from nemesis.agents.orchestration.step_executor import StepExecutor
from nemesis.core.config import config
from nemesis.core.project import ProjectContext
from nemesis.core.wordlists import KALI_DEFAULT_SENTINEL, suggest_ffuf_wordlist_display
from nemesis.db.database import Database
from nemesis.db.models import (
    AttackPlan,
    ControlMode,
    FindingSeverity,
    PlanStep,
    PlanStepStatus,
)
from nemesis.tools.agent_allowlist import default_tool_label_for_step

logger = logging.getLogger(__name__)


ArmStepFn = Callable[[PlanStep], None]


class PlanRuntime:
    """Guarda estado do loop e coordena AUTO / STEP."""

    def __init__(
        self,
        context: ProjectContext,
        db: Database,
        step_executor: StepExecutor,
        callbacks: OrchestratorCallbacks,
        arm_step: ArmStepFn,
    ) -> None:
        self._context = context
        self._db = db
        self._step_executor = step_executor
        self._cb = callbacks
        self._arm_step = arm_step
        self._loop_plan: AttackPlan | None = None
        self._loop_max_parallel: int = 1

    @property
    def loop_plan(self) -> AttackPlan | None:
        return self._loop_plan

    def clear_loop(self) -> None:
        self._loop_plan = None

    def emit_plan_to_tui(self, plan: AttackPlan) -> None:
        for step in plan.steps:
            self._cb.notify_task(step.id, "pending", step.name)

    @staticmethod
    def next_ready_steps(plan: AttackPlan, max_parallel: int) -> list[PlanStep]:
        """Retorna até *max_parallel* steps prontos para execução."""
        done_ids = {s.id for s in plan.steps if s.status == PlanStepStatus.DONE}
        ready = [
            s
            for s in plan.steps
            if s.status == PlanStepStatus.PENDING and all(dep in done_ids for dep in s.depends_on)
        ]
        return ready[:max_parallel]

    async def run(
        self,
        plan: AttackPlan,
        *,
        max_parallel: int = 1,
    ) -> OrchestratorResponse:
        self._loop_plan = plan
        self._loop_max_parallel = max_parallel
        self._step_executor.set_active_plan(plan)

        if self._context.mode == ControlMode.AUTO:
            return await self._run_auto(plan, max_parallel)
        return await self.pick_next_confirmation(plan)

    async def _run_auto(
        self,
        plan: AttackPlan,
        max_parallel: int,
    ) -> OrchestratorResponse:
        while True:
            ready = self.next_ready_steps(plan, max_parallel)

            if not ready:
                pending = [s for s in plan.steps if s.status == PlanStepStatus.PENDING]
                if not pending:
                    return await self.finish(plan)
                return self.make_blocked_response(plan)

            if max_parallel > 1 and len(ready) > 1:
                tasks = [asyncio.create_task(self._step_executor.execute(s)) for s in ready]
                results: list[OrchestratorResponse | BaseException] = await asyncio.gather(
                    *tasks, return_exceptions=True
                )
                for step, result in zip(ready, results, strict=True):
                    if isinstance(result, BaseException):
                        step.status = PlanStepStatus.FAILED
                        err_resp = OrchestratorResponse(
                            text=f"Step '{step.name}' failed unexpectedly: {result}"
                        )
                        self._cb.emit_response(err_resp)
                    elif isinstance(result, OrchestratorResponse):
                        self._cb.emit_response(result)
            else:
                for step in ready:
                    response = await self._step_executor.execute(step)
                    self._cb.emit_response(response)

    async def pick_next_confirmation(self, plan: AttackPlan) -> OrchestratorResponse:
        """STEP/MANUAL: arma o próximo step e devolve pedido de confirmação."""
        ready = self.next_ready_steps(plan, 1)

        if not ready:
            pending = [s for s in plan.steps if s.status == PlanStepStatus.PENDING]
            if not pending:
                return await self.finish(plan)
            return self.make_blocked_response(plan)

        step = ready[0]
        self._arm_step(step)

        first_tool = default_tool_label_for_step(step.agent, list(step.required_tools or []))
        target = step.args.get(
            "target",
            self._context.project.targets[0] if self._context.project.targets else "?",
        )
        extra_line = ""
        req_lower = [str(t).lower() for t in (step.required_tools or [])]
        if "ffuf" in req_lower or step.agent == "ffuf_agent":
            step.args.setdefault("wordlist", KALI_DEFAULT_SENTINEL)
            suggested = suggest_ffuf_wordlist_display(
                str(step.args.get("wordlist") or KALI_DEFAULT_SENTINEL),
                config.default_ffuf_wordlist,
            )
            extra_line = (
                f"\n\nWordlist suggestion: `{suggested}`\n"
                "You can press **W** in the step card to edit it before running."
            )

        return OrchestratorResponse(
            text=(
                f"Next step: **{step.name}**\n"
                f"Run `{first_tool}` on `{target}`?"
                f"{extra_line}\n\n"
                "**Continue? (y/n)**"
            ),
            requires_confirmation=True,
            confirmation_action_id=f"step:{step.id}",
        )

    async def finish(self, plan: AttackPlan) -> OrchestratorResponse:
        total = len(plan.steps)
        done_count = sum(1 for s in plan.steps if s.status == PlanStepStatus.DONE)
        failed_count = sum(1 for s in plan.steps if s.status == PlanStepStatus.FAILED)

        findings = self._context.findings
        critical = sum(1 for f in findings if f.severity == FindingSeverity.CRITICAL)
        high = sum(1 for f in findings if f.severity == FindingSeverity.HIGH)
        medium = sum(1 for f in findings if f.severity == FindingSeverity.MEDIUM)

        current_phase = self._context.session.phase
        next_phase = PHASE_AFTER_PLAN.get(current_phase)
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

    def make_blocked_response(self, plan: AttackPlan) -> OrchestratorResponse:
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
