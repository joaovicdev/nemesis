"""Testes do ConfirmationGate."""

from __future__ import annotations

import pytest

from nemesis.agents.orchestration.confirmation_gate import ConfirmationGate
from nemesis.agents.orchestration.response import OrchestratorResponse, PendingRecon
from nemesis.db.models import AttackChainSuggestion
from tests.conftest import make_plan_step

pytestmark = pytest.mark.asyncio


def _build(context, *, continuation: OrchestratorResponse | None = None):
    calls: dict[str, list] = {"step": [], "chain_tool": [], "tool": []}

    async def run_step(step):
        calls["step"].append(step.id)
        return OrchestratorResponse(text=f"ran step {step.id}")

    async def run_chain_tool(suggestion):
        calls["chain_tool"].append(suggestion)
        return OrchestratorResponse(text=f"ran chain {suggestion.tool}")

    async def run_tool(tool, target, extra_args):
        calls["tool"].append((tool, target, extra_args))
        return OrchestratorResponse(text=f"ran tool {tool}")

    async def continue_loop():
        return continuation

    gate = ConfirmationGate(
        context,
        run_step=run_step,
        run_chain_tool=run_chain_tool,
        run_tool=run_tool,
        continue_loop=continue_loop,
    )
    return gate, calls


async def test_confirm_without_pending_returns_placeholder(context):
    gate, _ = _build(context)
    resp = await gate.confirm("step:doesnt-matter")
    assert "No pending step" in resp.text


async def test_confirm_step_runs_and_chains_continuation(context):
    step = make_plan_step(id="s1")
    continuation = OrchestratorResponse(
        text="Next up", requires_confirmation=True, confirmation_action_id="step:s2"
    )
    gate, calls = _build(context, continuation=continuation)
    gate.arm_step(step)

    resp = await gate.confirm("step:s1")

    assert calls["step"] == ["s1"]
    assert "ran step s1" in resp.text
    assert "Next up" in resp.text
    assert resp.requires_confirmation is True
    assert resp.confirmation_action_id == "step:s2"
    assert context.was_confirmed("step:s1")


async def test_confirm_step_without_continuation_returns_step_response(context):
    step = make_plan_step(id="s1")
    gate, _ = _build(context, continuation=None)
    gate.arm_step(step)

    resp = await gate.confirm("step:s1")

    assert resp.text == "ran step s1"
    assert resp.requires_confirmation is False


async def test_confirm_chain_runs_chain_tool(context):
    gate, calls = _build(context)
    suggestion = AttackChainSuggestion(
        action="probe", tool="hydra", target="10.10.10.10", destructive=True
    )
    gate.arm_chain(suggestion)

    resp = await gate.confirm("chain:abcd1234")

    assert calls["chain_tool"] == [suggestion]
    assert "ran chain hydra" in resp.text


async def test_confirm_initial_recon_uses_pending_step_first(context):
    step = make_plan_step(id="s1")
    gate, calls = _build(context)
    gate.arm_step(step)

    resp = await gate.confirm("initial_recon")

    assert calls["step"] == ["s1"]
    assert "ran step s1" in resp.text


async def test_confirm_initial_recon_falls_back_to_pending_recon(context):
    gate, calls = _build(context)
    gate.arm_recon(PendingRecon(tool="nmap", target="10.10.10.10"))

    resp = await gate.confirm("initial_recon")

    assert calls["tool"] == [("nmap", "10.10.10.10", [])]
    assert "ran tool nmap" in resp.text


async def test_cancel_clears_all_pending(context):
    gate, calls = _build(context)
    gate.arm_step(make_plan_step(id="s1"))
    gate.arm_chain(AttackChainSuggestion(action="a", tool="nmap", target="10.10.10.10"))
    gate.arm_recon(PendingRecon(tool="nmap", target="10.10.10.10"))

    gate.cancel()

    assert (await gate.confirm("step:s1")).text.startswith("No pending step")
    assert (await gate.confirm("chain:xyz")).text.startswith("No pending chain")
    assert calls["step"] == []
    assert calls["chain_tool"] == []
