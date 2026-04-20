"""Testes do ToolRunner."""

from __future__ import annotations

import pytest

from nemesis.agents.executor import ScopeViolationError, ToolNotFoundError
from nemesis.agents.orchestration.callbacks import OrchestratorCallbacks
from nemesis.agents.orchestration.tool_runner import ToolRunner
from nemesis.db.models import AttackChainSuggestion, SessionPhase
from tests.conftest import FakeExecutor, make_executor_result, make_finding

pytestmark = pytest.mark.asyncio


@pytest.fixture
def callbacks():
    emitted_tasks: list[tuple[str, str, str]] = []
    emitted_lines: list[tuple[str, str]] = []
    cb = OrchestratorCallbacks(
        on_task_update=lambda tid, s, n: emitted_tasks.append((tid, s, n)),
        on_agent_output=lambda tid, line: emitted_lines.append((tid, line)),
    )
    cb._emitted_tasks = emitted_tasks  # type: ignore[attr-defined]
    cb._emitted_lines = emitted_lines  # type: ignore[attr-defined]
    return cb


def _patch_executor(monkeypatch, executor):
    from nemesis.agents.orchestration import tool_runner as module

    monkeypatch.setattr(module, "get_executor", lambda *a, **k: executor)


async def test_unknown_tool_fails_task(context, db, analyst, callbacks, monkeypatch):
    from nemesis.agents.orchestration import tool_runner as module

    def raise_value_error(*a, **k):
        raise ValueError("Unknown tool 'zzz'")

    monkeypatch.setattr(module, "get_executor", raise_value_error)

    runner = ToolRunner(context, db, analyst, callbacks)
    resp = await runner.run("zzz", "10.10.10.10")

    assert "Unknown tool" in resp.text
    assert db.task_status_updates[-1][1] == "failed"


async def test_tool_not_found_error_persists_failure(context, db, analyst, callbacks, monkeypatch):
    exec_ = FakeExecutor(make_executor_result(), raises=ToolNotFoundError("nmap missing"))
    _patch_executor(monkeypatch, exec_)

    runner = ToolRunner(context, db, analyst, callbacks)
    resp = await runner.run("nmap", "10.10.10.10")

    assert "Tool not found" in resp.text
    assert db.task_status_updates[-1][1] == "failed"


async def test_scope_violation_persists_failure(context, db, analyst, callbacks, monkeypatch):
    exec_ = FakeExecutor(make_executor_result(), raises=ScopeViolationError("target out of scope"))
    _patch_executor(monkeypatch, exec_)

    runner = ToolRunner(context, db, analyst, callbacks)
    resp = await runner.run("nmap", "10.10.10.10")

    assert "Scope violation" in resp.text
    assert db.task_status_updates[-1][1] == "failed"


async def test_happy_path_no_findings_advances_phase(context, db, analyst, callbacks, monkeypatch):
    exec_ = FakeExecutor(make_executor_result(tool="nmap"))
    _patch_executor(monkeypatch, exec_)

    assert context.current_phase == SessionPhase.RECON
    runner = ToolRunner(context, db, analyst, callbacks)

    resp = await runner.run("nmap", "10.10.10.10")

    assert "No findings extracted" in resp.text
    assert resp.findings is None
    assert context.current_phase == SessionPhase.ENUMERATION
    assert db.phase_updates == [(context.session.id, SessionPhase.ENUMERATION)]
    assert db.task_status_updates[-1][1] == "done"


async def test_happy_path_with_findings_persists_and_returns_chain(
    context, db, analyst, callbacks, monkeypatch
):
    analyst._findings = [make_finding(title="Port 22 open")]
    analyst._chain = [
        AttackChainSuggestion(
            action="probe ssh",
            tool="hydra",
            target="10.10.10.10",
            port="22",
            destructive=True,
        )
    ]
    exec_ = FakeExecutor(make_executor_result(tool="nmap"))
    _patch_executor(monkeypatch, exec_)

    runner = ToolRunner(context, db, analyst, callbacks)
    resp = await runner.run("nmap", "10.10.10.10")

    assert resp.findings is not None and len(resp.findings) == 1
    assert "Port 22 open" in resp.text
    assert len(db.findings) == 1
    assert len(context.findings) == 1
    assert len(resp.attack_chain_suggestions) == 1


async def test_streaming_lines_forwarded_to_callback(context, db, analyst, callbacks, monkeypatch):
    class StreamingExecutor:
        def __init__(self):
            self.streaming_calls = 0

        async def run_streaming(self, on_line):
            self.streaming_calls += 1
            on_line("line 1")
            on_line("line 2")
            return make_executor_result(tool="nmap")

    exec_ = StreamingExecutor()
    _patch_executor(monkeypatch, exec_)

    runner = ToolRunner(context, db, analyst, callbacks)
    await runner.run("nmap", "10.10.10.10")

    lines = callbacks._emitted_lines  # type: ignore[attr-defined]
    assert [line for (_tid, line) in lines] == ["line 1", "line 2"]


async def test_phase_not_advanced_twice(context, db, analyst, callbacks, monkeypatch):
    exec_ = FakeExecutor(make_executor_result(tool="nmap"))
    _patch_executor(monkeypatch, exec_)

    runner = ToolRunner(context, db, analyst, callbacks)
    await runner.run("nmap", "10.10.10.10")
    await runner.run("nmap", "10.10.10.10")

    assert db.phase_updates == [(context.session.id, SessionPhase.ENUMERATION)]
