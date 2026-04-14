"""EnumerationAgent — web directory brute-force and web server vulnerability scanning.

Allowed tools: gobuster, nikto
System prompt: web enumeration persona
Scope guard: only acts on HTTP/HTTPS targets
Fallback: gobuster dir scan when LLM is unreachable
"""

from __future__ import annotations

import logging

from nemesis.agents.analyst import AnalystAgent
from nemesis.agents.llm_client import LLMClient
from nemesis.agents.specialized.base import BaseSpecializedAgent
from nemesis.core.project import ProjectContext
from nemesis.db.models import AgentResponse, PlanStep

logger = logging.getLogger(__name__)

_ENUMERATION_SYSTEM = """\
You are a web enumeration specialist agent within NEMESIS, an authorized \
penetration testing platform.
Your job: web directory brute-forcing, web server probing, and HTTP service analysis.
Only act on HTTP/HTTPS targets. Never scan out-of-scope hosts.
Choose gobuster for directory enumeration and nikto for vulnerability scanning.
Always respond with valid JSON only — no markdown, no explanation outside the JSON.\
"""

_WEB_SCHEMES = ("http://", "https://")
_WEB_PORTS = {"80", "443", "8080", "8443", "8000", "8888"}

ALLOWED_TOOLS: list[str] = ["gobuster", "nikto"]


def _is_web_target(target: str) -> bool:
    """Return True if the target looks like a web endpoint."""
    return any(target.lower().startswith(s) for s in _WEB_SCHEMES)


class EnumerationAgent(BaseSpecializedAgent):
    """
    Web enumeration agent.

    Runs gobuster (directory brute-force) and nikto (web vulnerability scanner)
    against HTTP/HTTPS targets. Rejects non-web targets before any execution.
    """

    AGENT_NAME = "enumeration_agent"
    SYSTEM_PROMPT = _ENUMERATION_SYSTEM
    ALLOWED_TOOLS = ALLOWED_TOOLS

    def __init__(
        self,
        context: ProjectContext,
        llm: LLMClient,
        analyst: AnalystAgent,
    ) -> None:
        super().__init__(context, llm, analyst)

    async def execute(self, step: PlanStep) -> AgentResponse:
        """
        Extend base execute() with a web-target scope guard.

        If the target is not an HTTP/HTTPS URL, check whether port 80 or 443
        was found in existing context findings before proceeding. If no web
        surface is confirmed, return an informational AgentResponse and skip.
        """
        target = self._resolve_target(step)

        if not _is_web_target(target) and not self._has_web_port_in_findings(target):
            logger.info(
                "Enumeration skipped — no confirmed web surface",
                extra={
                    "event": "enumeration_agent.no_web_surface",
                    "target": target,
                    "step_id": step.id,
                },
            )
            return AgentResponse(
                thought=(
                    f"Target '{target}' does not appear to expose an HTTP/HTTPS service. "
                    "Skipping web enumeration until a web port is confirmed."
                ),
                action="skipped",
                tool=None,
                args={},
                result="Skipped — no web surface detected on target.",
                next_step=None,
            )

        # Ensure target has a scheme for gobuster/nikto
        if not any(target.lower().startswith(s) for s in _WEB_SCHEMES):
            target = f"http://{target}"
            step = step.model_copy(update={"args": {**step.args, "target": target}})

        return await super().execute(step)

    def _fallback_action(self, step: PlanStep, target: str) -> AgentResponse:
        """When LLM is unreachable, default to gobuster directory scan."""
        return AgentResponse(
            thought=f"LLM unavailable — running default gobuster dir scan on {target}",
            action="run_tool",
            tool="gobuster",
            args={},
            result="",
            next_step=None,
        )

    def _has_web_port_in_findings(self, target: str) -> bool:
        """Check whether any existing finding for this target has a web port open."""
        for finding in self._context.findings:
            if finding.target == target and finding.port in _WEB_PORTS:
                return True
        return False
