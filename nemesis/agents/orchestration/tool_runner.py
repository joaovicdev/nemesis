"""ToolRunner — executa uma ferramenta isolada via o executor e roteia o output.

Fluxo:
  1. Cria TaskRecord e notifica a TUI.
  2. Obtém o executor via `get_executor`; se o nome for inválido, falha com mensagem.
  3. Roda em streaming, entregando cada linha à TUI via callback.
  4. Trata `ToolNotFoundError` / `ScopeViolationError` como falhas de task
     (persistidas), retornando `OrchestratorResponse` amigável.
  5. Passa o resultado pelo Analyst; persiste findings; avança fase
     RECON→ENUMERATION na primeira task completa.
  6. Gera chain suggestions a partir dos findings extraídos.
"""

from __future__ import annotations

import logging
import uuid

from nemesis.agents.analyst import AnalystAgent
from nemesis.agents.executor import (
    ScopeViolationError,
    ToolNotFoundError,
    get_executor,
)
from nemesis.agents.orchestration.callbacks import OrchestratorCallbacks
from nemesis.agents.orchestration.response import OrchestratorResponse
from nemesis.core.project import ProjectContext
from nemesis.db.database import Database
from nemesis.db.models import (
    AttackChainSuggestion,
    FindingStatus,
    SessionPhase,
    TaskRecord,
)

logger = logging.getLogger(__name__)


class ToolRunner:
    """Executor leve: sem plano, sem gate — só manifest tool + Analyst."""

    def __init__(
        self,
        context: ProjectContext,
        db: Database,
        analyst: AnalystAgent,
        callbacks: OrchestratorCallbacks,
    ) -> None:
        self._context = context
        self._db = db
        self._analyst = analyst
        self._cb = callbacks

    async def run(
        self,
        tool: str,
        target: str,
        extra_args: list[str] | None = None,
    ) -> OrchestratorResponse:
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
        self._cb.notify_task(task_record.id, "running", task_record.label)

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
            self._cb.notify_task(task_record.id, "failed", str(exc))
            return OrchestratorResponse(text=f"Unknown tool: {tool}")

        try:
            result = await executor.run_streaming(
                lambda line: self._cb.emit_agent_output(task_record.id, line)
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
            self._cb.notify_task(task_record.id, "failed", msg)
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
            self._cb.notify_task(task_record.id, "failed", msg)
            return OrchestratorResponse(text=f"Scope violation: {msg}")

        findings = await self._analyst.process(result)

        for finding in findings:
            await self._db.create_finding(finding)
            self._context.add_finding(finding)

        await self._db.update_task_status(task_record.id, "done")
        self._cb.notify_task(task_record.id, "done", "")

        if self._context.current_phase == SessionPhase.RECON:
            self._context.advance_phase(SessionPhase.ENUMERATION)
            await self._db.update_session_phase(self._context.session.id, SessionPhase.ENUMERATION)

        chain: list[AttackChainSuggestion] = []
        if findings:
            chain = await self._analyst.suggest_attack_chain(findings)

        if not findings:
            return OrchestratorResponse(
                text=(
                    f"`{tool}` completed on `{target}` in "
                    f"{result.elapsed_seconds:.1f}s. No findings extracted."
                ),
                attack_chain_suggestions=chain,
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
        return OrchestratorResponse(
            text="\n".join(lines),
            findings=findings,
            attack_chain_suggestions=chain,
        )
