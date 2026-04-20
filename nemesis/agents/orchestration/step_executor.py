"""StepExecutor — roda um PlanStep via agente especializado.

Responsabilidades:
  * Resolver wordlist de ffuf quando aplicável.
  * Instanciar o agente via AGENT_REGISTRY ou cair no ToolRunner quando
    o agente não existe.
  * Gerenciar TaskRecord + atualizações de estado do PlanStep no DB.
  * Persistir findings novos produzidos pelo agente.
  * Gerar chain suggestions com base nos findings novos.
"""

from __future__ import annotations

import logging

from nemesis.agents.analyst import AnalystAgent
from nemesis.agents.llm_client import LLMClient
from nemesis.agents.orchestration.callbacks import OrchestratorCallbacks
from nemesis.agents.orchestration.response import OrchestratorResponse
from nemesis.agents.orchestration.tool_runner import ToolRunner
from nemesis.agents.specialized import get_agent
from nemesis.core.config import config
from nemesis.core.project import ProjectContext
from nemesis.core.wordlists import KALI_DEFAULT_SENTINEL, resolve_ffuf_wordlist
from nemesis.db.database import Database
from nemesis.db.models import (
    AttackChainSuggestion,
    AttackPlan,
    PlanStep,
    PlanStepStatus,
    TaskRecord,
)
from nemesis.tools.agent_allowlist import default_tool_label_for_step
from nemesis.tools.base import TOOL_REGISTRY

logger = logging.getLogger(__name__)


class StepExecutor:
    """Executa um PlanStep e devolve um OrchestratorResponse consolidado."""

    def __init__(
        self,
        context: ProjectContext,
        db: Database,
        llm: LLMClient,
        analyst: AnalystAgent,
        tool_runner: ToolRunner,
        callbacks: OrchestratorCallbacks,
    ) -> None:
        self._context = context
        self._db = db
        self._llm = llm
        self._analyst = analyst
        self._tool_runner = tool_runner
        self._cb = callbacks
        self._active_plan: AttackPlan | None = None

    def set_active_plan(self, plan: AttackPlan | None) -> None:
        """O PlanRuntime chama isso para que a atualização do step no DB aconteça."""
        self._active_plan = plan

    async def execute(self, step: PlanStep) -> OrchestratorResponse:
        self._maybe_resolve_ffuf_wordlist(step)

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
            first_tool = default_tool_label_for_step(step.agent, list(step.required_tools or []))
            if first_tool not in TOOL_REGISTRY:
                first_tool = next(iter(sorted(TOOL_REGISTRY.keys())), "nmap")
            target = step.args.get("target", self._context.project.targets[0])
            extra_args: list[str] = [str(a) for a in step.args.get("extra_args", [])]
            return await self._tool_runner.run(first_tool, target, extra_args)

        task_record = TaskRecord(
            project_id=self._context.project.id,
            session_id=self._context.session.id,
            label=step.name,
            tool=default_tool_label_for_step(step.agent, list(step.required_tools or [])),
            status="running",
        )
        await self._db.create_task(task_record)
        self._cb.notify_task(step.id, "running", step.name)

        step.status = PlanStepStatus.RUNNING

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
            self._cb.notify_task(step.id, "failed", str(exc))
            return OrchestratorResponse(text=f"Step '{step.name}' failed: {exc}")

        new_findings = self._context.findings[pre_count:]
        for finding in new_findings:
            await self._db.create_finding(finding)

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
        self._cb.notify_task(step.id, task_status, agent_response.result)

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

        chain: list[AttackChainSuggestion] = []
        if new_findings:
            chain = await self._analyst.suggest_attack_chain(new_findings)

        return OrchestratorResponse(
            text="\n".join(lines),
            findings=new_findings if new_findings else None,
            attack_chain_suggestions=chain,
        )

    def _maybe_resolve_ffuf_wordlist(self, step: PlanStep) -> None:
        req = [str(t).lower() for t in (step.required_tools or [])]
        if "ffuf" not in req and step.agent != "ffuf_agent":
            return
        step.args.setdefault("wordlist", KALI_DEFAULT_SENTINEL)
        try:
            resolved = resolve_ffuf_wordlist(
                str(step.args.get("wordlist") or KALI_DEFAULT_SENTINEL),
                config.default_ffuf_wordlist,
            )
        except FileNotFoundError:
            resolved = None
        logger.info(
            "Resolved ffuf wordlist",
            extra={
                "event": "orchestrator.ffuf_wordlist_resolved",
                "step_id": step.id,
                "wordlist": resolved or "",
            },
        )
