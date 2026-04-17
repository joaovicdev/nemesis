"""Resolve which tool names an specialized agent may offer to the LLM.

When PlanStep.required_tools is empty, names come from TOOL_REGISTRY filtered by
ToolDefinition.phase (or single-tool agents). Lists are sorted; optional cap
limits prompt size (see MAX_TOOLS_IN_AGENT_PROMPT).
"""

from __future__ import annotations

import logging

from nemesis.tools.base import ToolDefinition

logger = logging.getLogger(__name__)

MAX_TOOLS_IN_AGENT_PROMPT = 128

AGENT_PHASE_FILTERS: dict[str, frozenset[str]] = {
    "recon_agent": frozenset({"recon"}),
    "scanning_agent": frozenset({"scanning"}),
    "enumeration_agent": frozenset({"enumeration"}),
}

SINGLE_TOOL_AGENTS: dict[str, str] = {
    "nuclei_agent": "nuclei",
    "ffuf_agent": "ffuf",
}


def collect_names_for_agent(
    agent_name: str,
    registry: dict[str, ToolDefinition],
) -> list[str]:
    """All registry keys for this agent role (no cap, unsorted)."""
    if agent_name in SINGLE_TOOL_AGENTS:
        want = SINGLE_TOOL_AGENTS[agent_name]
        if want in registry:
            return [want]
        return []

    phases = AGENT_PHASE_FILTERS.get(agent_name)
    if phases is None:
        logger.warning(
            "Unknown agent for manifest allowlist",
            extra={"event": "tools.agent_allowlist_unknown_agent", "agent": agent_name},
        )
        return []

    out: list[str] = []
    for key, defn in registry.items():
        if defn.phase in phases:
            out.append(key)
    return out


def resolve_allowed_tool_names(
    agent_name: str,
    *,
    registry: dict[str, ToolDefinition] | None = None,
    max_names: int | None = MAX_TOOLS_IN_AGENT_PROMPT,
) -> list[str]:
    """
    Sorted tool names for LLM prompts. When max_names is None, no truncation.

    Cap and warning apply only when max_names is a positive int and the list
    exceeds it (manifest fallback path).
    """
    from nemesis.tools.base import TOOL_REGISTRY

    reg = registry if registry is not None else TOOL_REGISTRY
    names = collect_names_for_agent(agent_name, reg)
    names.sort()
    if max_names is not None and len(names) > max_names:
        logger.warning(
            "Agent allowlist capped for LLM prompt",
            extra={
                "event": "tools.agent_allowlist_capped",
                "agent": agent_name,
                "total": len(names),
                "capped_to": max_names,
            },
        )
        names = names[:max_names]
    return names


def pick_fallback_tool(agent_name: str, preferred: str) -> str:
    """Prefer *preferred* if it appears in the uncapped allowlist; else first sorted name."""
    names = resolve_allowed_tool_names(agent_name, max_names=None)
    if preferred in names:
        return preferred
    return names[0] if names else ""


def default_tool_label_for_step(agent_name: str, required_tools: list[str]) -> str:
    """First tool name for UI / task labels when required_tools may be empty."""
    if required_tools:
        return str(required_tools[0]).strip().lower()
    names = resolve_allowed_tool_names(agent_name, max_names=None)
    return names[0] if names else agent_name
