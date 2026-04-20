"""Testes do LLMChat."""

from __future__ import annotations

import pytest

from nemesis.agents.llm_client import LLMError
from nemesis.agents.orchestration.llm_chat import LLMChat

pytestmark = pytest.mark.asyncio


async def test_respond_returns_llm_reply_unchanged(context, llm):
    llm.reply = "hello from model"
    chat = LLMChat(context, llm)

    response = await chat.respond("hi")

    assert response.text == "hello from model"
    assert response.requires_confirmation is False
    assert llm.calls, "chat() deveria ter sido invocado"


async def test_respond_injects_engagement_context_into_prompt(context, llm):
    chat = LLMChat(context, llm)

    await chat.respond("what's next?")

    sys_messages = [m for m in llm.calls[0] if m["role"] == "system"]
    assert any("Current engagement context" in m["content"] for m in sys_messages)
    assert any("You are NEMESIS" in m["content"] for m in sys_messages)


async def test_respond_falls_back_when_llm_fails(context, llm):
    llm.error = LLMError("ollama down")
    chat = LLMChat(context, llm)

    response = await chat.respond("anything")

    assert "couldn't reach the AI model" in response.text
    assert "ollama serve" in response.text
