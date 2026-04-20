"""ConfirmationGate — gate de confirmação para ações (step / chain / initial recon).

Mantém o estado pendente entre a pergunta da TUI e a resposta do usuário.
Delega a execução real aos colaboradores via callables injetadas, sem conhecer
o PlanRuntime ou o StepExecutor diretamente.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from nemesis.agents.orchestration.response import OrchestratorResponse, PendingRecon
from nemesis.core.project import ProjectContext
from nemesis.db.models import AttackChainSuggestion, PlanStep

logger = logging.getLogger(__name__)


RunStepFn = Callable[[PlanStep], Awaitable[OrchestratorResponse]]
RunChainToolFn = Callable[[AttackChainSuggestion], Awaitable[OrchestratorResponse]]
RunToolFn = Callable[[str, str, list[str] | None], Awaitable[OrchestratorResponse]]
ContinueLoopFn = Callable[[], Awaitable[OrchestratorResponse | None]]


class ConfirmationGate:
    """Segura ações pendentes até o usuário confirmar (ou cancelar)."""

    def __init__(
        self,
        context: ProjectContext,
        run_step: RunStepFn,
        run_chain_tool: RunChainToolFn,
        run_tool: RunToolFn,
        continue_loop: ContinueLoopFn,
    ) -> None:
        self._context = context
        self._run_step = run_step
        self._run_chain_tool = run_chain_tool
        self._run_tool = run_tool
        self._continue_loop = continue_loop
        self._pending_recon: PendingRecon | None = None
        self._pending_step: PlanStep | None = None
        self._pending_chain: AttackChainSuggestion | None = None

    # ── Armadilhas ─────────────────────────────────────────────────────────

    def arm_step(self, step: PlanStep) -> None:
        self._pending_step = step

    def arm_chain(self, suggestion: AttackChainSuggestion) -> None:
        self._pending_chain = suggestion

    def arm_recon(self, recon: PendingRecon) -> None:
        self._pending_recon = recon

    def cancel(self) -> None:
        self._pending_recon = None
        self._pending_step = None
        self._pending_chain = None

    # ── Confirmação ────────────────────────────────────────────────────────

    async def confirm(self, action_id: str) -> OrchestratorResponse:
        if action_id.startswith("step:"):
            self._context.record_destructive_confirmation(action_id)

            if self._pending_step is None:
                return OrchestratorResponse(text="No pending step to confirm.")

            step = self._pending_step
            self._pending_step = None
            step_response = await self._run_step(step)

            continuation = await self._continue_loop()
            if continuation is None:
                return step_response
            combined_text = step_response.text + "\n\n---\n\n" + continuation.text
            return OrchestratorResponse(
                text=combined_text,
                findings=step_response.findings,
                requires_confirmation=continuation.requires_confirmation,
                confirmation_action_id=continuation.confirmation_action_id,
                attack_chain_suggestions=step_response.attack_chain_suggestions,
            )

        if action_id.startswith("chain:"):
            self._context.record_destructive_confirmation(action_id)
            if self._pending_chain is None:
                return OrchestratorResponse(text="No pending chain action to confirm.")
            pending = self._pending_chain
            self._pending_chain = None
            return await self._run_chain_tool(pending)

        if action_id == "initial_recon":
            self._context.record_destructive_confirmation(action_id)

            if self._pending_step is not None:
                step = self._pending_step
                self._pending_step = None
                return await self._run_step(step)

            if self._pending_recon is not None:
                pending = self._pending_recon
                self._pending_recon = None
                return await self._run_tool(pending.tool, pending.target, pending.extra_args)

        return OrchestratorResponse(text="No pending action to confirm.")
