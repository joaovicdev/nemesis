"""Tests for kali_tools.yml loading and manifest-only argv building."""

from __future__ import annotations

import pytest

from nemesis.agents.executor import build_argv
from nemesis.tools.base import ToolDefinition, load_tool_definitions_from_manifest


def test_manifest_loads_without_path_filter() -> None:
    defs = load_tool_definitions_from_manifest(require_on_path=False)
    assert "nmap" in defs
    assert "ffuf" in defs
    assert defs["nmap"].invocation_profile == "nmap_default_kali"


def test_build_argv_nmap_profile() -> None:
    d = ToolDefinition(
        name="nmap",
        binary="nmap",
        description="x",
        phase="scanning",
        invocation_profile="nmap_default_kali",
    )
    argv = build_argv(d, "127.0.0.1", ["-p", "22"])
    assert argv[:5] == ["nmap", "-sV", "-sC", "-T4", "127.0.0.1"]
    assert argv[5:] == ["-p", "22"]


def test_build_argv_declarative_whois() -> None:
    d = ToolDefinition(
        name="whois",
        binary="whois",
        description="x",
        phase="recon",
        default_args=["{target}"],
    )
    argv = build_argv(d, "example.com", [])
    assert argv == ["whois", "example.com"]


def test_build_argv_unknown_profile_raises() -> None:
    d = ToolDefinition(
        name="x",
        binary="x",
        description="x",
        phase="recon",
        invocation_profile="not_a_real_profile",
    )
    with pytest.raises(ValueError, match="Unknown invocation_profile"):
        build_argv(d, "t", [])
