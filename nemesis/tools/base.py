"""BaseTool interface — contract for all tool wrappers in NEMESIS."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolDefinition:
    """Metadata about a tool registered in NEMESIS."""

    name: str
    binary: str
    description: str
    phase: str          # which pentest phase this tool belongs to
    destructive: bool = False
    requires_root: bool = False
    tags: list[str] = field(default_factory=list)
    install_hint: str = ""


# Global tool registry — populated by each executor module
TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "nmap": ToolDefinition(
        name="nmap",
        binary="nmap",
        description="Network scanner — port discovery, service version detection",
        phase="recon",
        tags=["network", "ports", "services"],
        install_hint="sudo apt install nmap",
    ),
    "whois": ToolDefinition(
        name="whois",
        binary="whois",
        description="Domain/IP registration information lookup",
        phase="recon",
        tags=["osint", "domain"],
        install_hint="sudo apt install whois",
    ),
    "dig": ToolDefinition(
        name="dig",
        binary="dig",
        description="DNS enumeration and record lookup",
        phase="recon",
        tags=["dns", "network"],
        install_hint="sudo apt install dnsutils",
    ),
    "gobuster": ToolDefinition(
        name="gobuster",
        binary="gobuster",
        description="Directory and file brute-forcing for web targets",
        phase="enumeration",
        tags=["web", "directories"],
        install_hint="sudo apt install gobuster",
    ),
    "nikto": ToolDefinition(
        name="nikto",
        binary="nikto",
        description="Web server vulnerability scanner",
        phase="enumeration",
        tags=["web", "vulnerabilities"],
        install_hint="sudo apt install nikto",
    ),
}
