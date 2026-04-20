"""Orchestrator agent — fachada fina que compõe os colaboradores do pacote
`nemesis.agents.orchestration`.

Ciclo de vida:
    orc = Orchestrator(context, db, llm_client)
    await orc.start()
    response = await orc.on_project_activated()   # auto-trigger recon
    response = await orc.handle_message("run initial recon")
    await orc.shutdown()
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from nemesis.agents.analyst import AnalystAgent
from nemesis.agents.llm_client import LLMClient
from nemesis.agents.orchestration.callbacks import OrchestratorCallbacks
from nemesis.agents.orchestration.chain_suggester import ChainSuggester
from nemesis.agents.orchestration.command_router import CommandRouter
from nemesis.agents.orchestration.confirmation_gate import ConfirmationGate
from nemesis.agents.orchestration.llm_chat import LLMChat
from nemesis.agents.orchestration.plan_bootstrap import PlanBootstrap
from nemesis.agents.orchestration.plan_runtime import PlanRuntime
from nemesis.agents.orchestration.response import OrchestratorResponse
from nemesis.agents.orchestration.session_manager import SessionManager
from nemesis.agents.orchestration.step_executor import StepExecutor
from nemesis.agents.orchestration.tool_runner import ToolRunner
from nemesis.agents.planner import PlannerAgent
from nemesis.core.project import ProjectContext
from nemesis.db.database import Database
from nemesis.db.models import AttackChainSuggestion, AttackPlan, PlanStep

logger = logging.getLogger(__name__)


__all__ = ["Orchestrator", "OrchestratorResponse"]


class Orchestrator:
    """Fachada: instancia e compõe os colaboradores de orquestração."""

    def __init__(
        self,
        context: ProjectContext,
        db: Database,
        llm_client: LLMClient,
        on_response: Callable[[OrchestratorResponse], None] | None = None,
        on_task_update: Callable[[str, str, str], None] | None = None,
        on_agent_output: Callable[[str, str], None] | None = None,
        on_plan_ready: Callable[[AttackPlan, Path | None], None] | None = None,
    ) -> None:
        self._context = context
        self._db = db
        self._llm = llm_client

        analyst = AnalystAgent(context, llm_client)
        planner = PlannerAgent(context, llm_client)

        callbacks = OrchestratorCallbacks(
            on_response=on_response,
            on_task_update=on_task_update,
            on_agent_output=on_agent_output,
            on_plan_ready=on_plan_ready,
        )

        self._session = SessionManager(context)
        self._tool_runner = ToolRunner(context, db, analyst, callbacks)
        self._step_executor = StepExecutor(
            context, db, llm_client, analyst, self._tool_runner, callbacks
        )

        self._gate = ConfirmationGate(
            context,
            run_step=self._run_step_and_update,
            run_chain_tool=self._run_chain_tool,
            run_tool=self._tool_runner.run,
            continue_loop=self._continue_loop,
        )

        self._plan_runtime = PlanRuntime(
            context,
            db,
            self._step_executor,
            callbacks,
            arm_step=self._gate.arm_step,
        )

        self._chain = ChainSuggester(context, self._tool_runner, self._gate)

        self._llm_chat = LLMChat(context, llm_client)
        self._router = CommandRouter(
            context,
            db,
            self._llm_chat,
            run_tool=self._tool_runner.run,
            active_plan_provider=self._get_active_plan,
        )

        self._bootstrap = PlanBootstrap(
            context,
            db,
            planner,
            self._plan_runtime,
            callbacks,
            set_active_plan=self._set_active_plan,
        )

        self._active_plan: AttackPlan | None = None
        self._current_plan_md_path: Path | None = None

    # ── Ciclo de vida ─────────────────────────────────────────────────────

    async def start(self) -> None:
        await self._session.start()

    async def shutdown(self) -> None:
        await self._session.shutdown()

    # ── API pública usada pela TUI ────────────────────────────────────────

    async def on_project_activated(self) -> OrchestratorResponse:
        return await self._bootstrap.on_project_activated()

    async def handle_message(self, text: str) -> OrchestratorResponse:
        return await self._router.handle(text)

    async def confirm_and_execute(self, action_id: str) -> OrchestratorResponse:
        return await self._gate.confirm(action_id)

    def cancel_pending(self) -> None:
        self._gate.cancel()
        self._plan_runtime.clear_loop()

    async def run_plan_loop(
        self, plan: AttackPlan, *, max_parallel: int = 1
    ) -> OrchestratorResponse:
        return await self._plan_runtime.run(plan, max_parallel=max_parallel)

    async def execute_chain_suggestion(
        self, suggestion: AttackChainSuggestion
    ) -> OrchestratorResponse:
        return await self._chain.execute(suggestion)

    # ── Helpers internos para o gate ──────────────────────────────────────

    async def _run_step_and_update(self, step: PlanStep) -> OrchestratorResponse:
        return await self._step_executor.execute(step)

    async def _run_chain_tool(self, suggestion: AttackChainSuggestion) -> OrchestratorResponse:
        return await self._chain.run_tool(suggestion)

    async def _continue_loop(self) -> OrchestratorResponse | None:
        loop_plan = self._plan_runtime.loop_plan
        if loop_plan is None:
            return None
        return await self._plan_runtime.pick_next_confirmation(loop_plan)

    def _set_active_plan(self, plan: AttackPlan, md_path: Path | None) -> None:
        self._active_plan = plan
        self._current_plan_md_path = md_path
        self._step_executor.set_active_plan(plan)

    def _get_active_plan(self) -> AttackPlan | None:
        return self._active_plan
