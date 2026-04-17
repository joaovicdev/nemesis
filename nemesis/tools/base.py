"""BaseTool interface — contract for all tool wrappers in NEMESIS."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_MANIFEST_RESOURCE = "kali_tools.yml"


@dataclass
class ToolDefinition:
    """Metadata about a tool registered in NEMESIS (from kali_tools.yml)."""

    name: str
    binary: str
    description: str
    phase: str
    destructive: bool = False
    requires_root: bool = False
    tags: list[str] = field(default_factory=list)
    install_hint: str = ""
    output_format: str = "text"
    default_args: list[str] = field(default_factory=list)
    invocation_profile: str | None = None


def _parse_tool_row(raw: dict[str, Any]) -> ToolDefinition:
    name = str(raw["name"]).strip()
    binary = str(raw.get("binary", name)).strip()
    return ToolDefinition(
        name=name,
        binary=binary,
        description=str(raw.get("description", "")).strip(),
        phase=str(raw.get("phase", "recon")).strip(),
        destructive=bool(raw.get("destructive", False)),
        requires_root=bool(raw.get("requires_root", False)),
        tags=[str(t) for t in raw.get("tags", []) if str(t).strip()],
        install_hint=str(raw.get("install_hint", "")).strip(),
        output_format=str(raw.get("output_format", "text")).strip().lower() or "text",
        default_args=[str(a) for a in raw.get("default_args", [])],
        invocation_profile=(
            str(raw["invocation_profile"]).strip() if raw.get("invocation_profile") else None
        ),
    )


def load_tool_definitions_from_manifest(
    *,
    require_on_path: bool = True,
) -> dict[str, ToolDefinition]:
    """
    Load tool definitions from the packaged kali_tools.yml.

    When require_on_path is True, only tools whose binary resolves via
    shutil.which are included (case-sensitive binary name as in the manifest).
    """
    text = resources.files(__package__).joinpath(_MANIFEST_RESOURCE).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or "tools" not in data:
        logger.warning(
            "Invalid kali_tools.yml shape — expected mapping with 'tools' key",
            extra={"event": "tools.manifest_invalid"},
        )
        return {}

    rows = data["tools"]
    if not isinstance(rows, list):
        return {}

    out: dict[str, ToolDefinition] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            defn = _parse_tool_row(item)
        except KeyError:
            continue
        key = defn.name.lower()
        if key in out:
            logger.debug(
                "Duplicate tool name in manifest — skipping",
                extra={"event": "tools.duplicate_skipped", "tool": key},
            )
            continue
        if require_on_path and not shutil.which(defn.binary):
            logger.debug(
                "Tool binary not on PATH — omitted from registry",
                extra={"event": "tools.binary_missing", "tool": key, "binary": defn.binary},
            )
            continue
        out[key] = defn

    return out


# Populated at import: only binaries found on PATH.
TOOL_REGISTRY: dict[str, ToolDefinition] = load_tool_definitions_from_manifest()

__all__ = [
    "TOOL_REGISTRY",
    "ToolDefinition",
    "load_tool_definitions_from_manifest",
]
