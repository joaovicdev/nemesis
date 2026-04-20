"""LLMChat — encapsula conversa free-form com o modelo e degrada graciosamente."""

from __future__ import annotations

import logging

from nemesis.agents.llm_client import LLMClient, LLMError
from nemesis.agents.orchestration.response import CONVERSATION_SYSTEM, OrchestratorResponse
from nemesis.core.project import ProjectContext

logger = logging.getLogger(__name__)


class LLMChat:
    """Faz uma chamada de chat ao LLM injetando o contexto compacto do projeto."""

    def __init__(self, context: ProjectContext, llm: LLMClient) -> None:
        self._context = context
        self._llm = llm

    async def respond(self, text: str) -> OrchestratorResponse:
        context_summary = self._context.build_llm_context_summary()
        messages = [
            {"role": "system", "content": CONVERSATION_SYSTEM},
            {
                "role": "system",
                "content": f"Current engagement context:\n{context_summary}",
            },
            {"role": "user", "content": text},
        ]

        try:
            reply = await self._llm.chat(messages)
            return OrchestratorResponse(text=reply)
        except LLMError as exc:
            logger.warning(
                "LLM conversation failed",
                extra={
                    "event": "orchestrator.error",
                    "error_type": type(exc).__name__,
                    "context": "llm_response",
                },
            )
            return OrchestratorResponse(
                text=(
                    "I couldn't reach the AI model right now. "
                    "Check that Ollama is running (`ollama serve`) and try again.\n\n"
                    "Built-in commands still work: "
                    "`status` · `findings` · `plan` · `run <tool>` · `mode <auto|step|manual>`"
                )
            )
