"""SessionManager — ciclo de vida da sessão do Orchestrator."""

from __future__ import annotations

import asyncio
import logging

from nemesis.agents.executor import ExecutorResult
from nemesis.core.logging_config import set_session_id
from nemesis.core.project import ProjectContext

logger = logging.getLogger(__name__)


class SessionManager:
    """Gerencia início e término da sessão, incluindo cancelamento de executores."""

    def __init__(self, context: ProjectContext) -> None:
        self._context = context
        self._running_executors: dict[str, asyncio.Task[ExecutorResult]] = {}

    @property
    def running_executors(self) -> dict[str, asyncio.Task[ExecutorResult]]:
        return self._running_executors

    async def start(self) -> None:
        set_session_id(self._context.session.id)
        self._context.log_activated()
        logger.info(
            "Orchestrator session started",
            extra={
                "event": "orchestrator.session_started",
                "project_id": self._context.project.id,
                "session_id": self._context.session.id,
                "mode": self._context.mode.value,
            },
        )

    async def shutdown(self) -> None:
        cancelled = 0
        for task_id, task in list(self._running_executors.items()):
            if not task.done():
                task.cancel()
                cancelled += 1
                logger.info(
                    "Executor cancelled",
                    extra={
                        "event": "orchestrator.executor_cancelled",
                        "task_id": task_id,
                    },
                )
        self._running_executors.clear()
        logger.info(
            "Orchestrator shutdown",
            extra={
                "event": "orchestrator.session_ended",
                "executors_cancelled": cancelled,
            },
        )
