"""ScanningAgent — port scanning and service detection specialist.

Allowed tools: manifest phase=scanning. Fallback prefers nmap.
"""

from __future__ import annotations

from nemesis.agents.specialized.base import BaseSpecializedAgent
from nemesis.db.models import AgentResponse, PlanStep
from nemesis.tools.agent_allowlist import pick_fallback_tool

_SCANNING_SYSTEM = """\
You are a network scanning specialist agent within NEMESIS, an authorized \
penetration testing platform.
Your job: port scanning, service version detection, banner grabbing, OS fingerprinting.
You MUST only use tools from the allowed list. Choose arguments appropriate for the context:
- Use -sV for service/version detection
- Use -sC to run default safe scripts
- Adjust -T (timing) and port ranges based on stealth requirements
- Prefer targeted scans over full port sweeps unless the step asks for comprehensive coverage
Always respond with valid JSON only — no markdown, no explanation outside the JSON.\
"""

_DEFAULT_NMAP_ARGS: list[str] = ["-sV", "-sC", "-T4"]


class ScanningAgent(BaseSpecializedAgent):
    """
    Active port/service scanning agent.

    The LLM selects tool + argument profile; when offline, prefers nmap with
    standard -sV -sC -T4 if nmap is in the allowlist.
    """

    AGENT_NAME = "scanning_agent"
    SYSTEM_PROMPT = _SCANNING_SYSTEM

    def _fallback_action(self, step: PlanStep, target: str) -> AgentResponse:
        """When LLM is unreachable, default to nmap service scan if available."""
        tool = pick_fallback_tool(self.AGENT_NAME, "nmap")
        if not tool:
            return AgentResponse(
                thought="No scanning tools in registry.",
                action="error",
                tool=None,
                args={},
                result="No scanning-phase tools installed.",
                next_step=None,
            )
        args: dict[str, bool] = {}
        if tool == "nmap":
            args = {a: True for a in _DEFAULT_NMAP_ARGS}
        return AgentResponse(
            thought=f"LLM unavailable — running default {tool} on {target}",
            action="run_tool",
            tool=tool,
            args=args,
            result="",
            next_step=None,
        )
