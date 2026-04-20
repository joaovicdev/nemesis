"""Smoke test do Orchestrator fachada — garante que a API pública continua funcional."""

from __future__ import annotations

import pytest

from nemesis.agents.orchestrator import Orchestrator, OrchestratorResponse

pytestmark = pytest.mark.asyncio


def _build(context, db, llm, monkeypatch):
    # Neutraliza AnalystAgent e PlannerAgent para evitar qualquer chamada real.
    from nemesis.agents import orchestrator as module

    class NoopAnalyst:
        def __init__(self, *a, **k):
            pass

        async def process(self, _result):
            return []

        async def suggest_attack_chain(self, _findings):
            return []

    class NoopPlanner:
        def __init__(self, *a, **k):
            pass

        async def generate_plan(self, _goal):  # pragma: no cover - não usado aqui
            from nemesis.db.models import AttackPlan

            return AttackPlan(
                project_id="p",
                session_id="s",
                goal="g",
                steps=[],
            )

    monkeypatch.setattr(module, "AnalystAgent", NoopAnalyst)
    monkeypatch.setattr(module, "PlannerAgent", NoopPlanner)
    return Orchestrator(context, db, llm)


async def test_start_and_shutdown_are_safe(context, db, llm, monkeypatch):
    orc = _build(context, db, llm, monkeypatch)
    await orc.start()
    await orc.shutdown()


async def test_handle_message_status_returns_response(context, db, llm, monkeypatch):
    orc = _build(context, db, llm, monkeypatch)
    resp = await orc.handle_message("status")
    assert isinstance(resp, OrchestratorResponse)
    assert context.project.name in resp.text


async def test_handle_message_plan_without_plan(context, db, llm, monkeypatch):
    orc = _build(context, db, llm, monkeypatch)
    resp = await orc.handle_message("plan")
    assert "No plan generated" in resp.text


async def test_confirm_without_pending_returns_placeholder(context, db, llm, monkeypatch):
    orc = _build(context, db, llm, monkeypatch)
    resp = await orc.confirm_and_execute("initial_recon")
    assert "No pending action" in resp.text


async def test_cancel_pending_is_idempotent(context, db, llm, monkeypatch):
    orc = _build(context, db, llm, monkeypatch)
    orc.cancel_pending()
    orc.cancel_pending()  # segunda chamada não deve falhar


async def test_freeform_message_delegates_to_llm(context, db, llm, monkeypatch):
    llm.reply = "facade reply"
    orc = _build(context, db, llm, monkeypatch)
    resp = await orc.handle_message("what do you see?")
    assert resp.text == "facade reply"
