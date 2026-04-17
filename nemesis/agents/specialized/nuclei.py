"""NucleiAgent — template-based CVE and misconfiguration scanner."""

from __future__ import annotations

from nemesis.agents.specialized.base import BaseSpecializedAgent
from nemesis.db.models import AgentResponse, PlanStep
from nemesis.tools.agent_allowlist import pick_fallback_tool

_NUCLEI_SYSTEM = """\
You are a vulnerability assessment specialist agent within NEMESIS, an authorized \
penetration testing platform.
Your job: run template-based vulnerability scans to detect CVEs, misconfigurations, \
exposed panels, and known exploits using nuclei.
Focus on medium, high, and critical severity templates.
You MUST only use tools from the allowed list provided to you.
Always respond with valid JSON only — no markdown, no explanation outside the JSON.\
"""

class NucleiAgent(BaseSpecializedAgent):
    """
    Template-based vulnerability scanner agent.

    Uses nuclei to run thousands of CVE and misconfiguration templates
    against the target and extract structured findings.
    """

    AGENT_NAME = "nuclei_agent"
    SYSTEM_PROMPT = _NUCLEI_SYSTEM

    def _fallback_action(self, step: PlanStep, target: str) -> AgentResponse:
        tool = pick_fallback_tool(self.AGENT_NAME, "nuclei")
        if not tool:
            return AgentResponse(
                thought="nuclei not in registry.",
                action="error",
                tool=None,
                args={},
                result="nuclei is not installed or not in the tool manifest.",
                next_step=None,
            )
        return AgentResponse(
            thought=f"LLM unavailable — running default {tool} on {target}",
            action="run_tool",
            tool=tool,
            args={},
            result="",
            next_step=None,
        )
