"""Testes do PlanRuntime."""

from __future__ import annotations

from typing import Any

import pytest

from nemesis.agents.orchestration.callbacks import OrchestratorCallbacks
from nemesis.agents.orchestration.plan_runtime import PlanRuntime
from nemesis.agents.orchestration.response import OrchestratorResponse
from nemesis.db.models import ControlMode, PlanStep, PlanStepStatus, SessionPhase
from tests.conftest import make_attack_plan, make_plan_step, make_project_context

pytestmark = pytest.mark.asyncio


class FakeStepExecutor:
    """Roda cada step imediatamente, marcando-o como DONE (ou FAILED se pedido)."""

    def __init__(self, fail_ids: set[str] | None = None) -> None:
        self._fail = fail_ids or set()
        self.executed: list[str] = []
        self.active_plan: Any = None

    def set_active_plan(self, plan) -> None:
        self.active_plan = plan

    async def execute(self, step: PlanStep) -> OrchestratorResponse:
        self.executed.append(step.id)
        if step.id in self._fail:
            step.status = PlanStepStatus.FAILED
            return OrchestratorResponse(text=f"{step.id} failed")
        step.status = PlanStepStatus.DONE
        return OrchestratorResponse(text=f"{step.id} done")


def _build(context, db, step_exec, armed: list[PlanStep] | None = None):
    armed = armed if armed is not None else []
    callbacks = OrchestratorCallbacks()
    rt = PlanRuntime(
        context,
        db,
        step_exec,  # type: ignore[arg-type]
        callbacks,
        arm_step=armed.append,
    )
    return rt, armed


async def test_next_ready_steps_respects_deps():
    a = make_plan_step(id="a", depends_on=[])
    b = make_plan_step(id="b", depends_on=["a"])
    c = make_plan_step(id="c", depends_on=["a"])
    plan = make_attack_plan([a, b, c])

    ready = PlanRuntime.next_ready_steps(plan, max_parallel=3)
    assert [s.id for s in ready] == ["a"]

    a.status = PlanStepStatus.DONE
    ready = PlanRuntime.next_ready_steps(plan, max_parallel=3)
    assert sorted(s.id for s in ready) == ["b", "c"]


async def test_auto_mode_runs_full_plan_linearly(db):
    context = make_project_context(mode=ControlMode.AUTO)
    a = make_plan_step(id="a")
    b = make_plan_step(id="b", depends_on=["a"])
    plan = make_attack_plan([a, b])

    exec_ = FakeStepExecutor()
    rt, _ = _build(context, db, exec_)

    resp = await rt.run(plan, max_parallel=1)

    assert exec_.executed == ["a", "b"]
    assert "Plan complete" in resp.text
    # Fase avançou (RECON → ENUMERATION)
    assert context.current_phase == SessionPhase.ENUMERATION
    assert db.phase_updates == [(context.session.id, SessionPhase.ENUMERATION)]


async def test_auto_mode_parallel_runs_ready_siblings(db):
    context = make_project_context(mode=ControlMode.AUTO)
    a = make_plan_step(id="a")
    b = make_plan_step(id="b")
    c = make_plan_step(id="c", depends_on=["a", "b"])
    plan = make_attack_plan([a, b, c])

    exec_ = FakeStepExecutor()
    rt, _ = _build(context, db, exec_)

    await rt.run(plan, max_parallel=2)

    assert set(exec_.executed[:2]) == {"a", "b"}
    assert exec_.executed[2] == "c"


async def test_step_mode_returns_confirmation(db):
    context = make_project_context(mode=ControlMode.STEP)
    step = make_plan_step(id="a", name="Recon")
    plan = make_attack_plan([step])

    exec_ = FakeStepExecutor()
    rt, armed = _build(context, db, exec_)

    resp = await rt.run(plan)

    assert resp.requires_confirmation is True
    assert resp.confirmation_action_id == "step:a"
    assert armed == [step]
    assert "Next step" in resp.text


async def test_blocked_plan_returns_warning(db):
    context = make_project_context(mode=ControlMode.AUTO)
    a = make_plan_step(id="a")
    b = make_plan_step(id="b", depends_on=["a"])
    plan = make_attack_plan([a, b])

    exec_ = FakeStepExecutor(fail_ids={"a"})
    rt, _ = _build(context, db, exec_)

    resp = await rt.run(plan, max_parallel=1)

    assert "Plan blocked" in resp.text
    assert "b" in resp.text  # cita o step bloqueado


async def test_finish_advances_phase_through_map(db):
    context = make_project_context(mode=ControlMode.AUTO, phase=SessionPhase.ENUMERATION)
    a = make_plan_step(id="a")
    a.status = PlanStepStatus.DONE
    plan = make_attack_plan([a])
    exec_ = FakeStepExecutor()
    rt, _ = _build(context, db, exec_)

    await rt.finish(plan)

    assert context.current_phase == SessionPhase.EXPLOITATION
    assert db.phase_updates[-1] == (context.session.id, SessionPhase.EXPLOITATION)


async def test_pick_next_confirmation_ffuf_includes_wordlist_hint(db, monkeypatch):
    from nemesis.agents.orchestration import plan_runtime as module

    monkeypatch.setattr(module, "suggest_ffuf_wordlist_display", lambda *a, **k: "/tmp/wl.txt")
    context = make_project_context(mode=ControlMode.STEP)
    step = make_plan_step(id="a", agent="ffuf_agent", required_tools=["ffuf"])
    plan = make_attack_plan([step])

    exec_ = FakeStepExecutor()
    rt, _ = _build(context, db, exec_)

    resp = await rt.pick_next_confirmation(plan)

    assert "Wordlist suggestion" in resp.text
    assert "/tmp/wl.txt" in resp.text
