"""ReconAgent — passive OSINT, DNS, and WHOIS specialist.

Allowed tools: manifest phase=recon (installed). Fallback prefers whois.
"""

from __future__ import annotations

from nemesis.agents.specialized.base import BaseSpecializedAgent
from nemesis.db.models import AgentResponse, PlanStep
from nemesis.tools.agent_allowlist import pick_fallback_tool

_RECON_SYSTEM = """\
You are a reconnaissance specialist agent within NEMESIS, an authorized \
penetration testing platform.
Your job: passive information gathering — OSINT, DNS, WHOIS, subdomain enumeration.
Focus on low-noise techniques that do not generate alerts on the target.
You MUST only use tools from the allowed list provided to you.
Always respond with valid JSON only — no markdown, no explanation outside the JSON.\
"""

class ReconAgent(BaseSpecializedAgent):
    """
    Passive recon agent.

    Wraps whois and dig to collect domain intelligence before any active scanning.
    The agent asks the LLM to decide between whois (registrar/contact data) and
    dig (DNS record enumeration) based on the step description.
    """

    AGENT_NAME = "recon_agent"
    SYSTEM_PROMPT = _RECON_SYSTEM

    def _fallback_action(self, step: PlanStep, target: str) -> AgentResponse:
        """When LLM is unreachable, default to whois if available in manifest allowlist."""
        tool = pick_fallback_tool(self.AGENT_NAME, "whois")
        if not tool:
            return AgentResponse(
                thought="No recon tools in registry.",
                action="error",
                tool=None,
                args={},
                result="No recon-phase tools installed.",
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
