"""BaseSpecializedAgent — abstract base for all specialized execution agents.

Every specialized agent (Recon, Scanning, Enumeration, Vulnerability) inherits
from this class and is scoped to a specific toolset and system prompt.

Execution pipeline:
  1. Ask LLM which tool + args to use (constrained to step.required_tools)
  2. Validate that the target is within project scope
  3. Run the chosen tool via the executor registry
  4. Pass raw output through the AnalystAgent → structured findings
  5. Add findings to ProjectContext (in-memory; DB persistence is the Orchestrator's job)
  6. Return a fully-populated AgentResponse
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod

from nemesis.agents.analyst import AnalystAgent
from nemesis.agents.executor import (
    ExecutorResult,
    ScopeViolationError,
    ToolNotFoundError,
    get_executor,
)
from nemesis.agents.llm_client import LLMClient, LLMError
from nemesis.core.project import ProjectContext
from nemesis.db.models import AgentResponse, PlanStep

logger = logging.getLogger(__name__)


_ACTION_PROMPT = """\
Step: {step_name}
Description: {step_description}
Allowed tools: {allowed_tools}
Target: {target}
Engagement context:
{context_summary}

Decide what to run. Reply with JSON only:
{{
  "thought": "why this action",
  "action": "run_tool",
  "tool": "{default_tool}",
  "args": [],
  "result": "",
  "next_step": null
}}

IMPORTANT: "next_step" MUST be a plain string label or null — do NOT return a JSON object."""


class BaseSpecializedAgent(ABC):
    """
    Abstract base for all specialized agents.

    Subclasses MUST define:
      - AGENT_NAME   — registry key used by the Orchestrator
      - SYSTEM_PROMPT — LLM persona for this agent
      - ALLOWED_TOOLS — fallback tool whitelist (overridden per-step by step.required_tools)

    Subclasses SHOULD override:
      - _fallback_action() — default action when LLM is unreachable
    """

    AGENT_NAME: str = ""
    SYSTEM_PROMPT: str = ""
    ALLOWED_TOOLS: list[str] = []

    def __init__(
        self,
        context: ProjectContext,
        llm: LLMClient,
        analyst: AnalystAgent,
    ) -> None:
        self._context = context
        self._llm = llm
        self._analyst = analyst
        self._log = logging.getLogger(f"{__name__}.{self.AGENT_NAME or type(self).__name__}")

    # ── Public API ─────────────────────────────────────────────────────────

    async def execute(self, step: PlanStep) -> AgentResponse:
        """
        Execute one plan step end-to-end.

        Returns a fully-populated AgentResponse. Findings extracted by the
        Analyst are added to the ProjectContext as a side effect so that the
        Orchestrator can read and persist them after this call returns.
        """
        target = self._resolve_target(step)

        # 1. Ask LLM for the action decision
        try:
            action: AgentResponse = await self._ask_llm_for_action(step)
        except LLMError as exc:
            self._log.warning(
                "LLM unreachable — using fallback action",
                extra={
                    "event": f"{self.AGENT_NAME}.llm_fallback",
                    "error_type": type(exc).__name__,
                    "step_id": step.id,
                },
            )
            action = self._fallback_action(step, target)

        allowed = step.required_tools or self.ALLOWED_TOOLS
        default_tool = allowed[0] if allowed else ""

        tool: str = action.tool or default_tool
        thought: str = action.thought or f"Executing step {step.id}"
        args: list[str] = list(str(v) for v in action.args.values()) if action.args else []
        next_step: str | None = action.next_step

        # Guard: only allow tools within the step's declared scope
        if tool not in allowed:
            self._log.warning(
                "LLM selected tool outside allowed list — rejecting",
                extra={
                    "event": f"{self.AGENT_NAME}.tool_constraint_violation",
                    "tool": tool,
                    "allowed": allowed,
                    "step_id": step.id,
                },
            )
            return AgentResponse(
                thought=thought,
                action="error",
                tool=None,
                args={},
                result=f"Tool '{tool}' not in allowed list for this step: {allowed}",
                next_step=None,
            )

        # 2. Scope validation
        if not self._context.is_in_scope(target):
            return AgentResponse(
                thought=thought,
                action="error",
                tool=None,
                args={},
                result=f"Target '{target}' is out of scope.",
                next_step=None,
            )

        # 3. Run the tool
        try:
            executor_result = await self._run_tool(tool, target, args)
        except ToolNotFoundError as exc:
            return AgentResponse(
                thought=thought,
                action="error",
                tool=tool,
                args={a: True for a in args},
                result=f"Tool binary not found: {exc}",
                next_step=None,
            )
        except ScopeViolationError as exc:
            return AgentResponse(
                thought=thought,
                action="error",
                tool=tool,
                args={a: True for a in args},
                result=f"Scope violation: {exc}",
                next_step=None,
            )

        # 4. Pass through analyst → findings
        findings = await self._analyst.process(executor_result)

        # 5. Add findings to context (Orchestrator persists to DB)
        for finding in findings:
            self._context.add_finding(finding)

        finding_summary = (
            f"{len(findings)} finding(s): "
            + ", ".join(
                f"{f.service or f.title} [{f.port}]" if f.port else f.title
                for f in findings[:5]
            )
            if findings
            else "no findings extracted"
        )

        self._log.info(
            "Step executed",
            extra={
                "event": f"{self.AGENT_NAME}.step_executed",
                "step_id": step.id,
                "tool": tool,
                "findings_count": len(findings),
                "elapsed_s": round(executor_result.elapsed_seconds, 2),
            },
        )

        return AgentResponse(
            thought=thought,
            action="run_tool",
            tool=tool,
            args={a: True for a in args},
            result=f"Ran {tool} on {target} in {executor_result.elapsed_seconds:.1f}s — {finding_summary}",
            next_step=next_step,
        )

    # ── LLM action decision ────────────────────────────────────────────────

    async def _ask_llm_for_action(self, step: PlanStep) -> AgentResponse:
        """
        Query the LLM to decide which tool and args to use.

        The prompt lists only the tools in step.required_tools so the LLM
        cannot pick anything outside that scope. If it does anyway, the caller
        enforces the constraint before execution.
        """
        target = self._resolve_target(step)
        allowed = step.required_tools or self.ALLOWED_TOOLS
        default_tool = allowed[0] if allowed else ""

        prompt = _ACTION_PROMPT.format(
            step_name=step.name,
            step_description=step.description,
            allowed_tools=", ".join(allowed),
            target=target,
            context_summary=self._context.build_llm_context_summary(),
            default_tool=default_tool,
        )

        response = await self._llm.chat_agent_response(
            [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
        )

        # Hard-enforce tool constraint even if LLM ignores it
        if response.tool and response.tool not in allowed:
            self._log.warning(
                "LLM tool overridden by constraint",
                extra={
                    "event": f"{self.AGENT_NAME}.tool_overridden",
                    "original": response.tool,
                    "forced": default_tool,
                    "step_id": step.id,
                },
            )
            response = response.model_copy(update={"tool": default_tool})

        return response

    # ── Tool execution ─────────────────────────────────────────────────────

    async def _run_tool(self, tool: str, target: str, args: list[str]) -> ExecutorResult:
        """Instantiate an executor for the given tool and run it."""
        task_id = str(uuid.uuid4())[:8]
        executor = get_executor(tool, task_id, target, args)
        return await executor.run()

    # ── Subclass hooks ─────────────────────────────────────────────────────

    @abstractmethod
    def _fallback_action(self, step: PlanStep, target: str) -> AgentResponse:
        """Return a safe default AgentResponse when the LLM is unreachable."""
        ...

    # ── Internal helpers ───────────────────────────────────────────────────

    def _resolve_target(self, step: PlanStep) -> str:
        """Extract the target for this step, falling back to the first project target."""
        t = step.args.get("target")
        if isinstance(t, str) and t:
            return t
        return self._context.project.targets[0] if self._context.project.targets else ""
