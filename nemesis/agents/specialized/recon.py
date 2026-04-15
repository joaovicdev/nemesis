"""ReconAgent — passive OSINT, DNS, and WHOIS specialist.

Allowed tools: whois, dig  (amass added in PLAN 4)
System prompt: OSINT-focused passive recon persona
Fallback: run whois on the first target when LLM is unreachable
"""

from __future__ import annotations

from nemesis.agents.specialized.base import BaseSpecializedAgent
from nemesis.db.models import AgentResponse, PlanStep

_RECON_SYSTEM = """\
You are a reconnaissance specialist agent within NEMESIS, an authorized \
penetration testing platform.
Your job: passive information gathering — OSINT, DNS, WHOIS, subdomain enumeration.
Focus on low-noise techniques that do not generate alerts on the target.
You MUST only use tools from the allowed list provided to you.
Always respond with valid JSON only — no markdown, no explanation outside the JSON.\
"""

ALLOWED_TOOLS: list[str] = ["whois", "dig", "amass"]


class ReconAgent(BaseSpecializedAgent):
    """
    Passive recon agent.

    Wraps whois and dig to collect domain intelligence before any active scanning.
    The agent asks the LLM to decide between whois (registrar/contact data) and
    dig (DNS record enumeration) based on the step description.
    """

    AGENT_NAME = "recon_agent"
    SYSTEM_PROMPT = _RECON_SYSTEM
    ALLOWED_TOOLS = ALLOWED_TOOLS

    def _fallback_action(self, step: PlanStep, target: str) -> AgentResponse:
        """When LLM is unreachable, default to whois on the target."""
        return AgentResponse(
            thought=f"LLM unavailable — running default whois on {target}",
            action="run_tool",
            tool="whois",
            args={},
            result="",
            next_step=None,
        )
