"""ChainSuggester — executa uma AttackChainSuggestion, respeitando scope e gate."""

from __future__ import annotations

import logging
import uuid

from nemesis.agents.orchestration.confirmation_gate import ConfirmationGate
from nemesis.agents.orchestration.response import OrchestratorResponse, normalize_scope_target
from nemesis.agents.orchestration.tool_runner import ToolRunner
from nemesis.core.project import ProjectContext
from nemesis.db.models import AttackChainSuggestion

logger = logging.getLogger(__name__)


class ChainSuggester:
    """Valida e executa (ou arma gate) uma chain suggestion do Analyst."""

    def __init__(
        self,
        context: ProjectContext,
        tool_runner: ToolRunner,
        gate: ConfirmationGate,
    ) -> None:
        self._context = context
        self._tool_runner = tool_runner
        self._gate = gate

    async def run_tool(self, suggestion: AttackChainSuggestion) -> OrchestratorResponse:
        """Executa diretamente a sugestão, sem gate — usado após confirmação."""
        extra: list[str] | None = None
        if suggestion.port.strip() and suggestion.tool.lower() == "nmap":
            extra = ["-p", suggestion.port.strip()]
        return await self._tool_runner.run(
            suggestion.tool.strip(),
            suggestion.target.strip(),
            extra,
        )

    async def execute(self, suggestion: AttackChainSuggestion) -> OrchestratorResponse:
        """Valida escopo, arma gate para ações destrutivas, ou executa direto."""
        if suggestion.tool.lower() != "searchsploit":
            try:
                self._context.assert_in_scope(normalize_scope_target(suggestion.target))
            except ValueError as exc:
                return OrchestratorResponse(text=str(exc))

        if suggestion.destructive:
            action_id = f"chain:{uuid.uuid4().hex[:12]}"
            self._gate.arm_chain(suggestion)
            logger.info(
                "Chain action awaiting destructive confirmation",
                extra={
                    "event": "orchestrator.chain_pending",
                    "action_id": action_id,
                },
            )
            return OrchestratorResponse(
                text=(
                    "**Destructive / high-impact action**\n\n"
                    f"**{suggestion.action}**\n"
                    f"`{suggestion.tool}` on `{suggestion.target}`\n\n"
                    "**Continue? (y/n)**"
                ),
                requires_confirmation=True,
                confirmation_action_id=action_id,
            )

        return await self.run_tool(suggestion)
