"""Fixtures compartilhadas para a suite de testes do Orchestrator."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from nemesis.agents.executor import ExecutorResult
from nemesis.core.project import ProjectContext
from nemesis.db.models import (
    AttackChainSuggestion,
    AttackPlan,
    ChatEntry,
    ControlMode,
    Finding,
    FindingSeverity,
    FindingStatus,
    PlanStep,
    PlanStepStatus,
    Project,
    Session,
    SessionPhase,
    TaskRecord,
)

# ── ProjectContext em memória ──────────────────────────────────────────────────


def make_project_context(
    *,
    targets: list[str] | None = None,
    mode: ControlMode = ControlMode.STEP,
    phase: SessionPhase = SessionPhase.RECON,
) -> ProjectContext:
    project = Project(
        name="Test Engagement",
        targets=targets or ["10.10.10.10"],
        mode=mode,
    )
    session = Session(project_id=project.id, phase=phase)
    return ProjectContext(project=project, session=session)


@pytest.fixture
def context() -> ProjectContext:
    return make_project_context()


# ── FakeDatabase ───────────────────────────────────────────────────────────────


@dataclass
class FakeDatabase:
    """Stub async do subset de Database usado pela camada de orquestração."""

    chats: list[ChatEntry] = field(default_factory=list)
    tasks: list[TaskRecord] = field(default_factory=list)
    task_status_updates: list[tuple[str, str, str]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    plans: list[AttackPlan] = field(default_factory=list)
    plan_step_updates: list[dict[str, Any]] = field(default_factory=list)
    projects_updated: int = 0
    phase_updates: list[tuple[str, SessionPhase]] = field(default_factory=list)

    async def append_chat(self, entry: ChatEntry) -> None:
        self.chats.append(entry)

    async def create_task(self, task: TaskRecord) -> TaskRecord:
        self.tasks.append(task)
        return task

    async def update_task_status(self, task_id: str, status: str, note: str = "") -> None:
        self.task_status_updates.append((task_id, status, note))

    async def create_finding(self, finding: Finding) -> Finding:
        self.findings.append(finding)
        return finding

    async def create_plan(self, plan: AttackPlan) -> None:
        self.plans.append(plan)

    async def update_plan_step(
        self,
        plan_id: str,
        step_id: str,
        status: PlanStepStatus,
        result_summary: str,
        findings_count: int,
    ) -> None:
        self.plan_step_updates.append(
            {
                "plan_id": plan_id,
                "step_id": step_id,
                "status": status,
                "result_summary": result_summary,
                "findings_count": findings_count,
            }
        )

    async def update_project(self, project: Project) -> None:
        self.projects_updated += 1

    async def update_session_phase(self, session_id: str, phase: SessionPhase) -> None:
        self.phase_updates.append((session_id, phase))


@pytest.fixture
def db() -> FakeDatabase:
    return FakeDatabase()


# ── FakeLLMClient ──────────────────────────────────────────────────────────────


class FakeLLMClient:
    """Stub do LLMClient com resposta programável e erro injetável."""

    def __init__(self, reply: str = "stub reply", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[list[dict[str, str]]] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        return self.reply


@pytest.fixture
def llm() -> FakeLLMClient:
    return FakeLLMClient()


# ── FakeAnalyst ────────────────────────────────────────────────────────────────


class FakeAnalyst:
    """Stub do AnalystAgent com `process` e `suggest_attack_chain` programáveis."""

    def __init__(
        self,
        findings: list[Finding] | None = None,
        chain: list[AttackChainSuggestion] | None = None,
    ) -> None:
        self._findings = findings or []
        self._chain = chain or []
        self.process_calls: list[ExecutorResult] = []
        self.chain_calls: list[list[Finding]] = []

    async def process(self, result: ExecutorResult) -> list[Finding]:
        self.process_calls.append(result)
        return list(self._findings)

    async def suggest_attack_chain(self, findings: list[Finding]) -> list[AttackChainSuggestion]:
        self.chain_calls.append(list(findings))
        return list(self._chain)


@pytest.fixture
def analyst() -> FakeAnalyst:
    return FakeAnalyst()


# ── Factories convenientes ─────────────────────────────────────────────────────


def make_finding(
    *,
    title: str = "Finding",
    severity: FindingSeverity = FindingSeverity.MEDIUM,
    status: FindingStatus = FindingStatus.UNVERIFIED,
    target: str = "10.10.10.10",
    port: str = "80",
) -> Finding:
    return Finding(
        project_id="p1",
        session_id="s1",
        title=title,
        description="",
        severity=severity,
        status=status,
        target=target,
        port=port,
    )


def make_plan_step(
    *,
    id: str = "step-001",
    name: str = "Step 1",
    agent: str = "recon_agent",
    required_tools: list[str] | None = None,
    depends_on: list[str] | None = None,
    args: dict[str, Any] | None = None,
) -> PlanStep:
    return PlanStep(
        id=id,
        name=name,
        description="desc",
        required_tools=required_tools or ["nmap"],
        depends_on=depends_on or [],
        agent=agent,
        args=args or {},
    )


def make_attack_plan(steps: list[PlanStep]) -> AttackPlan:
    return AttackPlan(
        project_id="p1",
        session_id="s1",
        goal="test",
        steps=steps,
    )


def make_executor_result(
    *, task_id: str = "abcd1234", tool: str = "nmap", target: str = "10.10.10.10"
) -> ExecutorResult:
    return ExecutorResult(
        task_id=task_id,
        tool=tool,
        target=target,
        exit_code=0,
        stdout="",
        stderr="",
        elapsed_seconds=0.5,
        success=True,
    )


# ── FakeExecutor ───────────────────────────────────────────────────────────────


class FakeExecutor:
    """Implementa run_streaming devolvendo um resultado pré-definido."""

    def __init__(
        self,
        result: ExecutorResult,
        on_run: Callable[[], Awaitable[None]] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._result = result
        self._on_run = on_run
        self._raises = raises
        self.streaming_calls: int = 0

    async def run_streaming(self, on_line: Callable[[str], None]) -> ExecutorResult:
        self.streaming_calls += 1
        if self._raises is not None:
            raise self._raises
        if self._on_run is not None:
            await self._on_run()
        return self._result
