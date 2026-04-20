"""Testes do StepExecutor."""

from __future__ import annotations

import pytest

from nemesis.agents.orchestration.callbacks import OrchestratorCallbacks
from nemesis.agents.orchestration.response import OrchestratorResponse
from nemesis.agents.orchestration.step_executor import StepExecutor
from nemesis.db.models import AgentResponse, PlanStepStatus
from tests.conftest import make_attack_plan, make_finding, make_plan_step

pytestmark = pytest.mark.asyncio


class FakeToolRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[str] | None]] = []

    async def run(self, tool, target, extra_args):
        self.calls.append((tool, target, extra_args))
        return OrchestratorResponse(text=f"fallback:{tool}")


def _build(context, db, llm, analyst, monkeypatch, agent_factory=None):
    callbacks = OrchestratorCallbacks()
    tr = FakeToolRunner()
    se = StepExecutor(context, db, llm, analyst, tr, callbacks)

    if agent_factory is not None:
        from nemesis.agents.orchestration import step_executor as module

        monkeypatch.setattr(module, "get_agent", lambda name: agent_factory)
    return se, tr


async def test_unknown_agent_falls_back_to_tool_runner(context, db, llm, analyst, monkeypatch):
    from nemesis.agents.orchestration import step_executor as module

    def raise_value_error(name):
        raise ValueError(f"Unknown agent {name}")

    monkeypatch.setattr(module, "get_agent", raise_value_error)
    # Ajusta default_tool_label_for_step para devolver algo não-presente no
    # registry → cai no fallback "nmap" (ou primeiro disponível)
    monkeypatch.setattr(module, "TOOL_REGISTRY", {"nmap": object()})

    callbacks = OrchestratorCallbacks()
    tr = FakeToolRunner()
    se = StepExecutor(context, db, llm, analyst, tr, callbacks)

    step = make_plan_step(
        agent="nope_agent", required_tools=["nmap"], args={"target": "10.10.10.10"}
    )

    resp = await se.execute(step)

    assert tr.calls == [("nmap", "10.10.10.10", [])]
    assert "fallback:nmap" in resp.text


async def test_agent_exception_marks_failed(context, db, llm, analyst, monkeypatch):
    class ExplodingAgent:
        def __init__(self, *a, **k) -> None:
            pass

        async def execute(self, step):
            raise RuntimeError("boom")

    se, _tr = _build(context, db, llm, analyst, monkeypatch, agent_factory=ExplodingAgent)
    step = make_plan_step(agent="recon_agent")

    resp = await se.execute(step)

    assert "failed: boom" in resp.text
    assert step.status == PlanStepStatus.FAILED
    assert db.task_status_updates[-1][1] == "failed"


async def test_happy_path_persists_new_findings_and_updates_plan_step(
    context, db, llm, analyst, monkeypatch
):
    class HappyAgent:
        def __init__(self, ctx, llm_client, analyst_agent):
            self._ctx = ctx

        async def execute(self, step):
            self._ctx.add_finding(make_finding(title="open 80"))
            self._ctx.add_finding(make_finding(title="open 443"))
            return AgentResponse(
                thought="scan done",
                action="run_tool",
                result="Ran nmap, 2 findings",
                next_step="enumerate HTTP",
            )

    se, _tr = _build(context, db, llm, analyst, monkeypatch, agent_factory=HappyAgent)
    step = make_plan_step(agent="recon_agent")
    plan = make_attack_plan([step])
    se.set_active_plan(plan)

    analyst._chain = []  # sem chain suggestions
    resp = await se.execute(step)

    assert step.status == PlanStepStatus.DONE
    assert step.findings_count == 2
    assert "Ran nmap" in resp.text
    assert resp.findings is not None and len(resp.findings) == 2
    assert len(db.findings) == 2
    assert db.plan_step_updates, "plan step deveria ter sido atualizado no DB"
    assert db.plan_step_updates[-1]["status"] == PlanStepStatus.DONE


async def test_agent_error_action_marks_step_failed(context, db, llm, analyst, monkeypatch):
    class ErrorAgent:
        def __init__(self, *a, **k):
            pass

        async def execute(self, step):
            return AgentResponse(
                thought="t",
                action="error",
                result="boom",
            )

    se, _tr = _build(context, db, llm, analyst, monkeypatch, agent_factory=ErrorAgent)
    step = make_plan_step(agent="recon_agent")
    se.set_active_plan(make_attack_plan([step]))

    await se.execute(step)

    assert step.status == PlanStepStatus.FAILED
    assert db.task_status_updates[-1][1] == "failed"
    assert db.plan_step_updates[-1]["status"] == PlanStepStatus.FAILED


async def test_ffuf_step_resolves_wordlist(context, db, llm, analyst, monkeypatch):
    from nemesis.agents.orchestration import step_executor as module

    resolved: dict[str, str] = {}

    def fake_resolve(pref, default):
        resolved["pref"] = pref
        return "/usr/share/wordlists/dirb/common.txt"

    monkeypatch.setattr(module, "resolve_ffuf_wordlist", fake_resolve)

    class NoopAgent:
        def __init__(self, *a, **k):
            pass

        async def execute(self, step):
            return AgentResponse(thought="t", action="run_tool", result="ok")

    se, _tr = _build(context, db, llm, analyst, monkeypatch, agent_factory=NoopAgent)
    step = make_plan_step(agent="ffuf_agent", required_tools=["ffuf"])

    await se.execute(step)

    assert "wordlist" in step.args
    assert resolved  # resolve_ffuf_wordlist deve ter sido chamado
