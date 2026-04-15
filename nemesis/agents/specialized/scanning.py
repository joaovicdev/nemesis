"""ScanningAgent — port scanning and service detection specialist.

Allowed tools: nmap
System prompt: network scanning persona
Fallback: nmap -sV -sC -T4 when LLM is unreachable
"""

from __future__ import annotations

from nemesis.agents.specialized.base import BaseSpecializedAgent
from nemesis.db.models import AgentResponse, PlanStep

_SCANNING_SYSTEM = """\
You are a network scanning specialist agent within NEMESIS, an authorized \
penetration testing platform.
Your job: port scanning, service version detection, banner grabbing, OS fingerprinting.
You MUST only use nmap. Choose arguments appropriate for the engagement context:
- Use -sV for service/version detection
- Use -sC to run default safe scripts
- Adjust -T (timing) and port ranges based on stealth requirements
- Prefer targeted scans over full port sweeps unless the step asks for comprehensive coverage
Always respond with valid JSON only — no markdown, no explanation outside the JSON.\
"""

_DEFAULT_NMAP_ARGS: list[str] = ["-sV", "-sC", "-T4"]

ALLOWED_TOOLS: list[str] = ["nmap"]


class ScanningAgent(BaseSpecializedAgent):
    """
    Active port/service scanning agent.

    Delegates all scanning to nmap. The LLM selects the argument profile;
    when offline, falls back to the standard -sV -sC -T4 invocation.
    """

    AGENT_NAME = "scanning_agent"
    SYSTEM_PROMPT = _SCANNING_SYSTEM
    ALLOWED_TOOLS = ALLOWED_TOOLS

    def _fallback_action(self, step: PlanStep, target: str) -> AgentResponse:
        """When LLM is unreachable, default to a standard nmap service scan."""
        return AgentResponse(
            thought=f"LLM unavailable — running default nmap service scan on {target}",
            action="run_tool",
            tool="nmap",
            args={a: True for a in _DEFAULT_NMAP_ARGS},
            result="",
            next_step=None,
        )
