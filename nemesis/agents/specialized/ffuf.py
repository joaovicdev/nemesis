"""FfufAgent — fast web content discovery and fuzzing specialist."""

from __future__ import annotations

from nemesis.agents.specialized.base import BaseSpecializedAgent
from nemesis.core.config import config
from nemesis.core.wordlists import KALI_DEFAULT_SENTINEL, resolve_ffuf_wordlist
from nemesis.db.models import AgentResponse, PlanStep
from nemesis.tools.agent_allowlist import pick_fallback_tool

_FFUF_SYSTEM = """\
You are a web enumeration specialist agent within NEMESIS, an authorized \
penetration testing platform.
Your job: discover hidden web content, admin panels, API endpoints, and sensitive files \
using ffuf for fast fuzzing.
Focus on finding paths that could expose sensitive functionality or data.
You MUST only use tools from the allowed list provided to you.
Always respond with valid JSON only — no markdown, no explanation outside the JSON.\
"""

class FfufAgent(BaseSpecializedAgent):
    """
    Web fuzzing specialist agent.

    Uses ffuf for fast directory/file discovery. Auto-calibrates to filter
    false positives and outputs structured JSON for reliable parsing.
    """

    AGENT_NAME = "ffuf_agent"
    SYSTEM_PROMPT = _FFUF_SYSTEM

    def _merge_executor_cli_args(self, step: PlanStep, tool: str, llm_cli: list[str]) -> list[str]:
        if tool != "ffuf":
            return llm_cli

        merged: list[str] = []

        step_extra = step.args.get("extra_args", [])
        if isinstance(step_extra, list):
            merged.extend(str(a) for a in step_extra)

        merged.extend(llm_cli)

        if any(a == "-w" or str(a).startswith("-w") for a in merged):
            return merged

        pref = step.args.get("wordlist")
        preference = str(pref) if isinstance(pref, str) and pref else KALI_DEFAULT_SENTINEL
        wordlist = resolve_ffuf_wordlist(preference, config.default_ffuf_wordlist)
        return ["-w", wordlist, *merged]

    def _fallback_action(self, step: PlanStep, target: str) -> AgentResponse:
        tool = pick_fallback_tool(self.AGENT_NAME, "ffuf")
        if not tool:
            return AgentResponse(
                thought="ffuf not in registry.",
                action="error",
                tool=None,
                args={},
                result="ffuf is not installed or not in the tool manifest.",
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
