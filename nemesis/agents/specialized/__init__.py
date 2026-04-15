"""Specialized agent registry for NEMESIS.

Maps agent name strings (as used in PlanStep.agent) to their classes so the
Orchestrator can look them up without hard-coding imports.

Usage:
    agent_cls = get_agent("recon_agent")
    agent = agent_cls(context, llm, analyst)
    response = await agent.execute(step)
"""

from __future__ import annotations

from nemesis.agents.specialized.base import BaseSpecializedAgent
from nemesis.agents.specialized.enumeration import EnumerationAgent
from nemesis.agents.specialized.recon import ReconAgent
from nemesis.agents.specialized.scanning import ScanningAgent
from nemesis.agents.specialized.vulnerability import VulnerabilityAgent

AGENT_REGISTRY: dict[str, type[BaseSpecializedAgent]] = {
    "recon_agent": ReconAgent,
    "scanning_agent": ScanningAgent,
    "enumeration_agent": EnumerationAgent,
    "vulnerability_agent": VulnerabilityAgent,
}

_KNOWN_NAMES = list(AGENT_REGISTRY.keys())


def get_agent(name: str) -> type[BaseSpecializedAgent]:
    """
    Look up a specialized agent class by its registry name.

    Args:
        name: The agent name from PlanStep.agent (e.g. "recon_agent").

    Returns:
        The agent class — caller is responsible for instantiation.

    Raises:
        ValueError: If the name is not in the registry.
    """
    cls = AGENT_REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown specialized agent '{name}'. "
            f"Available agents: {_KNOWN_NAMES}"
        )
    return cls


__all__ = [
    "AGENT_REGISTRY",
    "BaseSpecializedAgent",
    "EnumerationAgent",
    "ReconAgent",
    "ScanningAgent",
    "VulnerabilityAgent",
    "get_agent",
]
