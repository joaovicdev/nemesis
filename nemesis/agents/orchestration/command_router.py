"""CommandRouter — interpreta comandos built-in do chat e delega o restante.

Responsabilidades:
  * Detectar comandos `mode`, `status`, `findings`, `plan`, `run <tool> on <target>`.
  * Executar cada comando built-in e montar a resposta textual correspondente.
  * Delegar mensagens free-form para o `LLMChat`.
  * Persistir a mensagem do usuário via `append_chat`.

Não conhece loop de plano, não conhece gate de confirmação. Recebe
colaboradores via injeção para manter desacoplamento.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from nemesis.agents.orchestration.llm_chat import LLMChat
from nemesis.agents.orchestration.response import OrchestratorResponse
from nemesis.core.project import ProjectContext
from nemesis.db.database import Database
from nemesis.db.models import AttackPlan, ChatEntry, ControlMode

logger = logging.getLogger(__name__)


RunToolFn = Callable[[str, str, list[str] | None], Awaitable[OrchestratorResponse]]
ActivePlanProvider = Callable[[], AttackPlan | None]


class CommandRouter:
    """Roteia mensagens do usuário para handlers built-in ou para o LLMChat."""

    def __init__(
        self,
        context: ProjectContext,
        db: Database,
        llm_chat: LLMChat,
        run_tool: RunToolFn,
        active_plan_provider: ActivePlanProvider,
    ) -> None:
        self._context = context
        self._db = db
        self._llm_chat = llm_chat
        self._run_tool = run_tool
        self._active_plan_provider = active_plan_provider

    async def handle(self, text: str) -> OrchestratorResponse:
        """Processa uma mensagem do usuário e devolve a resposta."""
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
            response = await self._llm_chat.respond(text)
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

    # ── Comandos built-in ─────────────────────────────────────────────────

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
        plan = self._active_plan_provider()
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

        return await self._run_tool(tool, target, None)

    async def _persist_response(self, text: str) -> None:
        await self._db.append_chat(
            ChatEntry(
                project_id=self._context.project.id,
                session_id=self._context.session.id,
                role="nemesis",
                content=text,
            )
        )
