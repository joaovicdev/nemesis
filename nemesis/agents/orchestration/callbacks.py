"""Callbacks opcionais expostos pelo Orchestrator para a TUI.

Agrupa os 4 callbacks injetados pela `MainScreen` num único objeto, o que
simplifica a assinatura dos colaboradores e evita espalhar `if callback is not None`
por todo lado.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nemesis.agents.orchestration.response import OrchestratorResponse
from nemesis.db.models import AttackPlan


@dataclass
class OrchestratorCallbacks:
    """Hooks opcionais para a camada de apresentação."""

    on_response: Callable[[OrchestratorResponse], None] | None = None
    on_task_update: Callable[[str, str, str], None] | None = None
    on_agent_output: Callable[[str, str], None] | None = None
    on_plan_ready: Callable[[AttackPlan, Path | None], None] | None = None

    def emit_response(self, response: OrchestratorResponse) -> None:
        if self.on_response is not None:
            self.on_response(response)

    def notify_task(self, task_id: str, status: str, note: str) -> None:
        if self.on_task_update is not None:
            self.on_task_update(task_id, status, note)

    def emit_agent_output(self, task_id: str, line: str) -> None:
        if self.on_agent_output is not None:
            self.on_agent_output(task_id, line)

    def emit_plan_ready(self, plan: AttackPlan, md_path: Path | None) -> bool:
        """Encaminha o plano para a TUI. Retorna True se havia callback registrado."""
        if self.on_plan_ready is None:
            return False
        self.on_plan_ready(plan, md_path)
        return True
