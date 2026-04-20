"""Orchestration subsystem — colaboradores que compõem o Orchestrator.

O `Orchestrator` em `nemesis/agents/orchestrator.py` é uma fachada fina que
instancia e compõe os módulos deste pacote. A API pública usada pela TUI
permanece em `nemesis.agents.orchestrator`.
"""

from __future__ import annotations

from nemesis.agents.orchestration.response import (
    CONVERSATION_SYSTEM,
    PHASE_AFTER_PLAN,
    OrchestratorResponse,
    PendingRecon,
    normalize_scope_target,
)

__all__ = [
    "CONVERSATION_SYSTEM",
    "PHASE_AFTER_PLAN",
    "OrchestratorResponse",
    "PendingRecon",
    "normalize_scope_target",
]
