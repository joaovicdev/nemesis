"""Database models — SQLite schema definitions and Pydantic domain models."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────────


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    COMPLETED = "completed"


class SessionPhase(str, Enum):
    RECON = "recon"
    ENUMERATION = "enumeration"
    EXPLOITATION = "exploitation"
    POST_EXPLOITATION = "post_exploitation"
    REPORTING = "reporting"


class FindingStatus(str, Enum):
    """Lifecycle stages a finding must follow in order."""
    RAW = "raw"                  # emitted by executor
    UNVERIFIED = "unverified"    # processed by analyst, has confidence score
    VALIDATED = "validated"      # confirmed by orchestrator / user
    DISMISSED = "dismissed"      # false positive, discarded
    REPORTED = "reported"        # included in final report


class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ControlMode(str, Enum):
    AUTO = "auto"
    STEP = "step"
    MANUAL = "manual"


# ── Domain models (Pydantic) ───────────────────────────────────────────────────


class Project(BaseModel):
    """A pentest engagement project."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    targets: list[str]
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
    tool_source: str = ""       # which tool produced this
    raw_evidence: str = ""      # raw tool output snippet
    remediation: str = ""
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


# ── SQL DDL ────────────────────────────────────────────────────────────────────

CREATE_TABLES_SQL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS projects (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        targets     TEXT NOT NULL,   -- JSON array
        context     TEXT NOT NULL DEFAULT '',
        status      TEXT NOT NULL DEFAULT 'active',
        mode        TEXT NOT NULL DEFAULT 'step',
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
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
    # Indexes for common queries
    "CREATE INDEX IF NOT EXISTS idx_findings_project ON findings(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(project_id, severity)",
    "CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(project_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_entries(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id)",
]
