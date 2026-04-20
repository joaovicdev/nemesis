"""Testes do ChainSuggester."""

from __future__ import annotations

import pytest

from nemesis.agents.orchestration.chain_suggester import ChainSuggester
from nemesis.agents.orchestration.confirmation_gate import ConfirmationGate
from nemesis.agents.orchestration.response import OrchestratorResponse
from nemesis.db.models import AttackChainSuggestion

pytestmark = pytest.mark.asyncio


class FakeToolRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[str] | None]] = []

    async def run(self, tool, target, extra_args):
        self.calls.append((tool, target, extra_args))
        return OrchestratorResponse(text=f"ran {tool}")


def _build(context):
    tr = FakeToolRunner()

    async def run_step(_s):
        return OrchestratorResponse(text="")

    async def run_chain_tool(s):
        return await tr.run(s.tool, s.target, None)

    async def run_tool(tool, target, extra_args):
        return await tr.run(tool, target, extra_args)

    async def continue_loop():
        return None

    gate = ConfirmationGate(
        context,
        run_step=run_step,
        run_chain_tool=run_chain_tool,
        run_tool=run_tool,
        continue_loop=continue_loop,
    )
    suggester = ChainSuggester(context, tr, gate)  # type: ignore[arg-type]
    return suggester, gate, tr


async def test_out_of_scope_rejected(context):
    suggester, _gate, tr = _build(context)
    suggestion = AttackChainSuggestion(action="a", tool="nmap", target="8.8.8.8")

    resp = await suggester.execute(suggestion)

    assert "outside the project scope" in resp.text
    assert tr.calls == []


async def test_non_destructive_runs_immediately(context):
    suggester, _gate, tr = _build(context)
    suggestion = AttackChainSuggestion(action="scan", tool="nmap", target="10.10.10.10", port="80")

    await suggester.execute(suggestion)

    assert tr.calls == [("nmap", "10.10.10.10", ["-p", "80"])]


async def test_destructive_arms_gate_instead_of_running(context):
    suggester, gate, tr = _build(context)
    suggestion = AttackChainSuggestion(
        action="brute", tool="hydra", target="10.10.10.10", destructive=True
    )

    resp = await suggester.execute(suggestion)

    assert resp.requires_confirmation is True
    assert resp.confirmation_action_id is not None
    assert resp.confirmation_action_id.startswith("chain:")
    assert tr.calls == []
    # Gate agora tem a sugestão pendente — confirmar deve dispará-la
    confirm_resp = await gate.confirm(resp.confirmation_action_id)
    assert tr.calls == [("hydra", "10.10.10.10", None)]
    assert "ran hydra" in confirm_resp.text


async def test_searchsploit_bypasses_scope_check(context):
    suggester, _gate, tr = _build(context)
    suggestion = AttackChainSuggestion(
        action="lookup", tool="searchsploit", target="CVE-2021-41773"
    )

    await suggester.execute(suggestion)

    assert tr.calls == [("searchsploit", "CVE-2021-41773", None)]
