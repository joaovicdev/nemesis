"""Tests for manifest-derived agent tool allowlists."""

from __future__ import annotations

from unittest.mock import patch

import nemesis.tools.base as tools_base
from nemesis.tools.agent_allowlist import (
    collect_names_for_agent,
    default_tool_label_for_step,
    pick_fallback_tool,
    resolve_allowed_tool_names,
)
from nemesis.tools.base import ToolDefinition

MOCK_REG: dict[str, ToolDefinition] = {
    "whois": ToolDefinition(name="whois", binary="whois", description="", phase="recon"),
    "dig": ToolDefinition(name="dig", binary="dig", description="", phase="recon"),
    "nmap": ToolDefinition(name="nmap", binary="nmap", description="", phase="scanning"),
    "aenum": ToolDefinition(name="aenum", binary="aenum", description="", phase="enumeration"),
    "ze_enum": ToolDefinition(
        name="ze_enum", binary="ze_enum", description="", phase="enumeration"
    ),
    "nuclei": ToolDefinition(name="nuclei", binary="nuclei", description="", phase="vulnerability"),
}


def test_recon_phase_filter() -> None:
    names = collect_names_for_agent("recon_agent", MOCK_REG)
    assert set(names) == {"whois", "dig"}


def test_nuclei_single_tool() -> None:
    names = collect_names_for_agent("nuclei_agent", MOCK_REG)
    assert names == ["nuclei"]


def test_nuclei_missing() -> None:
    reg = {k: v for k, v in MOCK_REG.items() if k != "nuclei"}
    assert collect_names_for_agent("nuclei_agent", reg) == []


def test_cap_truncates() -> None:
    many = {
        f"t{i}": ToolDefinition(name=f"t{i}", binary=f"t{i}", description="", phase="enumeration")
        for i in range(200)
    }
    out = resolve_allowed_tool_names("enumeration_agent", registry=many, max_names=10)
    assert len(out) == 10
    assert out == sorted(out)


def test_pick_fallback_prefers() -> None:
    with patch.object(tools_base, "TOOL_REGISTRY", MOCK_REG):
        assert pick_fallback_tool("recon_agent", "whois") == "whois"
        assert pick_fallback_tool("recon_agent", "missing") == "dig"


def test_default_tool_label_uses_required_first() -> None:
    assert default_tool_label_for_step("scanning_agent", ["Nmap"]) == "nmap"


def test_default_tool_label_fallback_uses_manifest() -> None:
    with patch.object(tools_base, "TOOL_REGISTRY", MOCK_REG):
        assert default_tool_label_for_step("scanning_agent", []) == "nmap"
