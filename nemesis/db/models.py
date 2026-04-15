"""Database models — SQLite schema definitions and Pydantic domain models."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ── Enums ──────────────────────────────────────────────────────────────────────


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    COMPLETED = "completed"


class SessionPhase(StrEnum):
    RECON = "recon"
    ENUMERATION = "enumeration"
    EXPLOITATION = "exploitation"
    POST_EXPLOITATION = "post_exploitation"
    REPORTING = "reporting"


class FindingStatus(StrEnum):
    """Lifecycle stages a finding must follow in order."""

    RAW = "raw"  # emitted by executor
    UNVERIFIED = "unverified"  # processed by analyst, has confidence score
    VALIDATED = "validated"  # confirmed by orchestrator / user
    DISMISSED = "dismissed"  # false positive, discarded
    REPORTED = "reported"  # included in final report


class FindingSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ControlMode(StrEnum):
    AUTO = "auto"
    STEP = "step"
    MANUAL = "manual"


# ── Domain models (Pydantic) ───────────────────────────────────────────────────


class Project(BaseModel):
    """A pentest engagement project."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    targets: list[str]
    out_of_scope: list[str] = Field(default_factory=list)
    context: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    mode: ControlMode = ControlMode.STEP
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Session(BaseModel):
    """A single work session within a project (one terminal run)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    phase: SessionPhase = SessionPhase.RECON
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    notes: str = ""


class Finding(BaseModel):
    """A security finding discovered during a session."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    session_id: str
    title: str
    description: str
    severity: FindingSeverity
    status: FindingStatus = FindingStatus.RAW
    confidence: float = 0.0  # 0.0 – 1.0, set by Analyst
    target: str = ""
    port: str = ""
    service: str = ""
    cve_ids: list[str] = Field(default_factory=list)
    tool_source: str = ""  # which tool produced this
    raw_evidence: str = ""  # raw tool output snippet
    remediation: str = ""
    attack_path_steps: list[str] = Field(default_factory=list)
    impact_assessment: str = ""
    remediation_guidance: str = ""
    related_finding_ids: list[str] = Field(default_factory=list)
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ChatEntry(BaseModel):
    """A single message in the project chat history."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    session_id: str
    role: str  # "user" | "nemesis" | "system"
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TaskRecord(BaseModel):
    """A task/step recorded in the attack plan."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    session_id: str
    label: str
    tool: str
    status: str = "pending"
    note: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None


# ── Plan models ────────────────────────────────────────────────────────────────


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStep(BaseModel):
    """A single step in a structured attack plan."""

    id: str  # e.g. "step-001"
    name: str
    description: str
    required_tools: list[str]  # e.g. ["nmap"]
    depends_on: list[str]  # list of step ids
    agent: str  # e.g. "recon_agent"
    args: dict[str, Any] = Field(default_factory=dict)
    status: PlanStepStatus = PlanStepStatus.PENDING
    result_summary: str = ""
    findings_count: int = 0


class AttackPlan(BaseModel):
    """A structured multi-step attack plan for a project session."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    session_id: str
    goal: str
    steps: list[PlanStep]
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentResponse(BaseModel):
    """Structured output format for agent actions."""

    thought: str
    action: str
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    result: str
    next_step: str | None = None

    @field_validator("next_step", mode="before")
    @classmethod
    def coerce_next_step(cls, v: object) -> str | None:
        """Accept str | None; discard any other type the LLM may return (e.g. dict)."""
        if v is None or isinstance(v, str):
            return v
        return None


# ── SQL DDL ────────────────────────────────────────────────────────────────────

CREATE_TABLES_SQL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS projects (
        id           TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        targets      TEXT NOT NULL,   -- JSON array
        out_of_scope TEXT NOT NULL DEFAULT '[]',  -- JSON array
        context      TEXT NOT NULL DEFAULT '',
        status       TEXT NOT NULL DEFAULT 'active',
        mode         TEXT NOT NULL DEFAULT 'step',
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id          TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL REFERENCES projects(id),
        phase       TEXT NOT NULL DEFAULT 'recon',
        started_at  TEXT NOT NULL,
        ended_at    TEXT,
        notes       TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS findings (
        id                  TEXT PRIMARY KEY,
        project_id          TEXT NOT NULL REFERENCES projects(id),
        session_id          TEXT NOT NULL REFERENCES sessions(id),
        title               TEXT NOT NULL,
        description         TEXT NOT NULL DEFAULT '',
        severity            TEXT NOT NULL,
        status              TEXT NOT NULL DEFAULT 'raw',
        confidence          REAL NOT NULL DEFAULT 0.0,
        target              TEXT NOT NULL DEFAULT '',
        port                TEXT NOT NULL DEFAULT '',
        service             TEXT NOT NULL DEFAULT '',
        cve_ids             TEXT NOT NULL DEFAULT '[]',   -- JSON array
        tool_source         TEXT NOT NULL DEFAULT '',
        raw_evidence        TEXT NOT NULL DEFAULT '',
        remediation         TEXT NOT NULL DEFAULT '',
        attack_path_steps   TEXT NOT NULL DEFAULT '[]',  -- JSON array
        impact_assessment   TEXT NOT NULL DEFAULT '',
        remediation_guidance TEXT NOT NULL DEFAULT '',
        related_finding_ids TEXT NOT NULL DEFAULT '[]',  -- JSON array
        discovered_at       TEXT NOT NULL,
        updated_at          TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_entries (
        id          TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL REFERENCES projects(id),
        session_id  TEXT NOT NULL REFERENCES sessions(id),
        role        TEXT NOT NULL,
        content     TEXT NOT NULL,
        timestamp   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id           TEXT PRIMARY KEY,
        project_id   TEXT NOT NULL REFERENCES projects(id),
        session_id   TEXT NOT NULL REFERENCES sessions(id),
        label        TEXT NOT NULL,
        tool         TEXT NOT NULL DEFAULT '',
        status       TEXT NOT NULL DEFAULT 'pending',
        note         TEXT NOT NULL DEFAULT '',
        created_at   TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attack_plans (
        id          TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL REFERENCES projects(id),
        session_id  TEXT NOT NULL REFERENCES sessions(id),
        goal        TEXT NOT NULL,
        steps       TEXT NOT NULL,   -- JSON blob of PlanStep list
        created_at  TEXT NOT NULL
    )
    """,
    # Indexes for common queries
    "CREATE INDEX IF NOT EXISTS idx_findings_project ON findings(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_attack_plans_project ON attack_plans(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(project_id, severity)",
    "CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(project_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_entries(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id)",
]
