"""PlanBootstrap — gera o plano inicial ao ativar o projeto.

Fluxo:
  1. Invoca PlannerAgent para construir um AttackPlan.
  2. Persiste o plano no DB.
  3. Escreve a versão markdown em disco via plan_writer (tolera falha de I/O).
  4. Se a TUI registrou `on_plan_ready`, entrega o plano e devolve apenas o
     cabeçalho do plano para o chat (execução acontece após aprovação).
  5. Caso contrário, emite o plano para a task list e dispara o loop de execução.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from nemesis.agents.orchestration.callbacks import OrchestratorCallbacks
from nemesis.agents.orchestration.plan_runtime import PlanRuntime
from nemesis.agents.orchestration.response import OrchestratorResponse
from nemesis.agents.planner import PlannerAgent
from nemesis.core import plan_writer
from nemesis.core.project import ProjectContext
from nemesis.db.database import Database
from nemesis.db.models import AttackPlan

logger = logging.getLogger(__name__)


class PlanBootstrap:
    """Constrói e ativa o plano inicial para o engajamento."""

    def __init__(
        self,
        context: ProjectContext,
        db: Database,
        planner: PlannerAgent,
        plan_runtime: PlanRuntime,
        callbacks: OrchestratorCallbacks,
        set_active_plan: Callable[[AttackPlan, Path | None], None],
    ) -> None:
        self._context = context
        self._db = db
        self._planner = planner
        self._plan_runtime = plan_runtime
        self._cb = callbacks
        self._set_active_plan = set_active_plan

    async def on_project_activated(self) -> OrchestratorResponse:
        targets = self._context.project.targets
        if not targets:
            return OrchestratorResponse(text="No targets configured. Add a target first.")

        targets_label = ", ".join(targets)
        pg = (self._context.project.pentest_goals or "").strip()
        if pg:
            goal = f"{pg} (primary targets: {targets_label})"
        else:
            goal = f"Authorized security assessment of {targets_label}"

        plan = await self._planner.generate_plan(goal)
        await self._db.create_plan(plan)

        md_path = self._write_plan_markdown(plan)
        self._set_active_plan(plan, md_path)

        plan_header = self._format_plan_header(plan)
        path_line = (
            f"\n_Plan file: `{md_path}`_\n"
            if md_path is not None
            else "\n_Plan file: (not saved)_\n"
        )

        if self._cb.emit_plan_ready(plan, md_path):
            return OrchestratorResponse(
                text=(
                    f"{plan_header}{path_line}\n"
                    "Review the plan in the approval dialog and approve to start execution."
                ),
            )

        self._plan_runtime.emit_plan_to_tui(plan)
        loop_response = await self._plan_runtime.run(plan)
        return OrchestratorResponse(
            text=f"{plan_header}{path_line}\n\n{loop_response.text}",
            findings=loop_response.findings,
            requires_confirmation=loop_response.requires_confirmation,
            confirmation_action_id=loop_response.confirmation_action_id,
            attack_chain_suggestions=loop_response.attack_chain_suggestions,
        )

    def _write_plan_markdown(self, plan: AttackPlan) -> Path | None:
        try:
            md_path = plan_writer.write(
                plan,
                self._context.project.name,
                self._context.session.id,
            )
            logger.info(
                "Attack plan written to markdown",
                extra={"event": "orchestrator.plan_markdown_saved", "path": str(md_path)},
            )
            return md_path
        except OSError as exc:
            logger.warning(
                "Could not write plan markdown file",
                extra={
                    "event": "orchestrator.plan_markdown_failed",
                    "error_type": type(exc).__name__,
                },
            )
            return None

    @staticmethod
    def _format_plan_header(plan: AttackPlan) -> str:
        lines = [
            f"**Attack plan generated** — {len(plan.steps)} step(s):",
            f"_Goal: {plan.goal}_",
            "",
        ]
        for step in plan.steps:
            tools_str = ", ".join(step.required_tools) if step.required_tools else "—"
            dep_str = f" (after {', '.join(step.depends_on)})" if step.depends_on else ""
            lines.append(f"  **{step.id}** · {step.name}{dep_str} · tools: `{tools_str}`")
            lines.append(f"    _{step.description}_")
        return "\n".join(lines)
