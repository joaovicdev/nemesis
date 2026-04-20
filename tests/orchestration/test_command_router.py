"""Testes do CommandRouter."""

from __future__ import annotations

import pytest

from nemesis.agents.orchestration.command_router import CommandRouter
from nemesis.agents.orchestration.llm_chat import LLMChat
from nemesis.agents.orchestration.response import OrchestratorResponse
from nemesis.db.models import AttackPlan, ControlMode, FindingStatus, PlanStep, PlanStepStatus
from tests.conftest import make_finding

pytestmark = pytest.mark.asyncio


def _build_router(context, db, llm, *, active_plan: AttackPlan | None = None):
    calls: list[tuple[str, str, list[str] | None]] = []

    async def run_tool(tool: str, target: str, extra_args):
        calls.append((tool, target, extra_args))
        return OrchestratorResponse(text=f"ran {tool} on {target}")

    router = CommandRouter(
        context,
        db,
        LLMChat(context, llm),
        run_tool=run_tool,
        active_plan_provider=lambda: active_plan,
    )
    return router, calls


async def test_mode_change_updates_context_and_persists(context, db, llm):
    router, _ = _build_router(context, db, llm)

    resp = await router.handle("mode auto")

    assert "AUTO" in resp.text
    assert context.mode == ControlMode.AUTO
    assert db.projects_updated == 1


async def test_mode_change_invalid_returns_hint(context, db, llm):
    router, _ = _build_router(context, db, llm)

    resp = await router.handle("mode turbo")

    assert "Unknown mode" in resp.text
    assert db.projects_updated == 0


async def test_status_lists_phase_and_mode(context, db, llm):
    router, _ = _build_router(context, db, llm)

    resp = await router.handle("status")

    assert context.project.name in resp.text
    assert context.session.phase.value in resp.text


async def test_findings_empty(context, db, llm):
    router, _ = _build_router(context, db, llm)
    resp = await router.handle("findings")
    assert "No validated findings" in resp.text


async def test_findings_lists_validated(context, db, llm):
    context.add_finding(make_finding(title="SSH weak", status=FindingStatus.VALIDATED, port="22"))
    router, _ = _build_router(context, db, llm)

    resp = await router.handle("findings")

    assert "SSH weak" in resp.text
    assert resp.findings is not None and len(resp.findings) == 1


async def test_plan_none(context, db, llm):
    router, _ = _build_router(context, db, llm)
    resp = await router.handle("plan")
    assert "No plan generated" in resp.text


async def test_plan_renders_steps(context, db, llm):
    step = PlanStep(
        id="s1",
        name="Recon",
        description="",
        required_tools=["nmap"],
        depends_on=[],
        agent="recon_agent",
        status=PlanStepStatus.DONE,
        result_summary="done",
    )
    plan = AttackPlan(
        project_id=context.project.id, session_id=context.session.id, goal="g", steps=[step]
    )
    router, _ = _build_router(context, db, llm, active_plan=plan)

    resp = await router.handle("plan")

    assert "Recon" in resp.text
    assert "recon_agent" in resp.text


async def test_run_tool_happy_path(context, db, llm):
    router, calls = _build_router(context, db, llm)

    resp = await router.handle("run nmap on 10.10.10.10")

    assert calls == [("nmap", "10.10.10.10", None)]
    assert "ran nmap" in resp.text


async def test_run_tool_out_of_scope(context, db, llm):
    router, calls = _build_router(context, db, llm)

    resp = await router.handle("run nmap on 8.8.8.8")

    assert "outside the project scope" in resp.text
    assert calls == []


async def test_freeform_falls_through_to_llm_and_persists_both_sides(context, db, llm):
    llm.reply = "an llm answer"
    router, _ = _build_router(context, db, llm)

    resp = await router.handle("what do you think?")

    assert resp.text == "an llm answer"
    roles = [c.role for c in db.chats]
    assert roles == ["user", "nemesis"]
    assert db.chats[0].content == "what do you think?"
    assert db.chats[1].content == "an llm answer"


async def test_builtin_commands_persist_only_user_message(context, db, llm):
    router, _ = _build_router(context, db, llm)

    await router.handle("status")

    assert [c.role for c in db.chats] == ["user"]
