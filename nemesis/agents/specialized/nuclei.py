"""NucleiAgent — template-based CVE and misconfiguration scanner."""

from __future__ import annotations

from nemesis.agents.specialized.base import BaseSpecializedAgent
from nemesis.db.models import AgentResponse, PlanStep

_NUCLEI_SYSTEM = """\
You are a vulnerability assessment specialist agent within NEMESIS, an authorized \
penetration testing platform.
Your job: run template-based vulnerability scans to detect CVEs, misconfigurations, \
exposed panels, and known exploits using nuclei.
Focus on medium, high, and critical severity templates.
You MUST only use tools from the allowed list provided to you.
Always respond with valid JSON only — no markdown, no explanation outside the JSON.\
"""

ALLOWED_TOOLS: list[str] = ["nuclei"]


class NucleiAgent(BaseSpecializedAgent):
    """
    Template-based vulnerability scanner agent.

    Uses nuclei to run thousands of CVE and misconfiguration templates
    against the target and extract structured findings.
    """

    AGENT_NAME = "nuclei_agent"
    SYSTEM_PROMPT = _NUCLEI_SYSTEM
    ALLOWED_TOOLS = ALLOWED_TOOLS

    def _fallback_action(self, step: PlanStep, target: str) -> AgentResponse:
        return AgentResponse(
            thought=f"LLM unavailable — running default nuclei scan on {target}",
            action="run_tool",
            tool="nuclei",
            args={},
            result="",
            next_step=None,
        )
