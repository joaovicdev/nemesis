"""Tipos e constantes puras compartilhadas pelos colaboradores do Orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field

from nemesis.db.models import AttackChainSuggestion, Finding, SessionPhase

CONVERSATION_SYSTEM = (
    "You are NEMESIS, an AI penetration testing co-pilot. "
    "You assist authorized security professionals during engagements. "
    "Be concise, technical, and actionable. "
    "Never suggest actions outside the defined project scope."
)

PHASE_AFTER_PLAN: dict[SessionPhase, SessionPhase] = {
    SessionPhase.RECON: SessionPhase.ENUMERATION,
    SessionPhase.ENUMERATION: SessionPhase.EXPLOITATION,
    SessionPhase.EXPLOITATION: SessionPhase.POST_EXPLOITATION,
    SessionPhase.POST_EXPLOITATION: SessionPhase.REPORTING,
}


def normalize_scope_target(raw: str) -> str:
    """Strip URL scheme, path and port for scope checks."""
    t = raw.strip()
    lower = t.lower()
    if lower.startswith("https://"):
        t = t[8:]
    elif lower.startswith("http://"):
        t = t[7:]
    if "/" in t:
        t = t.split("/", 1)[0]
    host = t
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    return host.strip()


@dataclass
class OrchestratorResponse:
    """A response emitted by the Orchestrator to the TUI."""

    text: str
    findings: list[Finding] | None = None
    requires_confirmation: bool = False
    confirmation_action_id: str | None = None
    attack_chain_suggestions: list[AttackChainSuggestion] = field(default_factory=list)


@dataclass
class PendingRecon:
    """A single-tool plan waiting for step-mode user confirmation."""

    tool: str
    target: str
    extra_args: list[str] = field(default_factory=list)
