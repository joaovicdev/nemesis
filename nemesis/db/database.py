"""Async SQLite database connection and CRUD operations."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import aiosqlite

from nemesis.db.models import (
    CREATE_TABLES_SQL,
    ChatEntry,
    Finding,
    FindingStatus,
    Project,
    Session,
    SessionPhase,
    TaskRecord,
)

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path.home() / ".nemesis" / "nemesis.db"

# Queries slower than this threshold are logged as warnings
_SLOW_QUERY_THRESHOLD_MS = 100


class Database:
    """Async SQLite wrapper — single connection, WAL mode for concurrency."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_DB_PATH
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the database connection and create tables if needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._initialize_schema()
        logger.info(
            "Database opened",
            extra={"event": "db.opened", "path": str(self._path)},
        )

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _initialize_schema(self) -> None:
        assert self._conn
        for statement in CREATE_TABLES_SQL:
            await self._conn.execute(statement)
        await self._conn.commit()

    @asynccontextmanager
    async def _cursor(self) -> AsyncIterator[aiosqlite.Cursor]:
        assert self._conn, "Database not connected — call connect() first"
        async with self._conn.cursor() as cursor:
            yield cursor

    @asynccontextmanager
    async def _timed(self, op: str) -> AsyncIterator[None]:
        """Context manager that logs slow DB operations and catches errors."""
        t0 = time.monotonic()
        try:
            yield
        except Exception as exc:
            logger.error(
                "Database operation failed",
                extra={
                    "event": "db.error",
                    "op": op,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        finally:
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            if elapsed_ms >= _SLOW_QUERY_THRESHOLD_MS:
                logger.warning(
                    "Slow database operation",
                    extra={
                        "event": "db.query_slow",
                        "op": op,
                        "elapsed_ms": elapsed_ms,
                    },
                )
            else:
                logger.debug(
                    "Database operation completed",
                    extra={
                        "event": "db.query_ok",
                        "op": op,
                        "elapsed_ms": elapsed_ms,
                    },
                )

    # ── Project CRUD ───────────────────────────────────────────────────────

    async def create_project(self, project: Project) -> Project:
        async with self._timed("create_project"):
            async with self._cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO projects
                        (id, name, targets, out_of_scope, context, status, mode, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project.id,
                        project.name,
                        json.dumps(project.targets),
                        json.dumps(project.out_of_scope),
                        project.context,
                        project.status.value,
                        project.mode.value,
                        project.created_at.isoformat(),
                        project.updated_at.isoformat(),
                    ),
                )
            await self._conn.commit()  # type: ignore[union-attr]
        return project

    async def get_project(self, project_id: str) -> Project | None:
        async with self._timed("get_project"), self._cursor() as cur:
            await cur.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
            row = await cur.fetchone()
        return _row_to_project(row) if row else None

    async def list_projects(self) -> list[Project]:
        async with self._timed("list_projects"), self._cursor() as cur:
            await cur.execute("SELECT * FROM projects ORDER BY updated_at DESC")
            rows = await cur.fetchall()
        return [_row_to_project(r) for r in rows]

    async def update_project(self, project: Project) -> None:
        project.updated_at = datetime.utcnow()
        async with self._timed("update_project"):
            async with self._cursor() as cur:
                await cur.execute(
                    """
                    UPDATE projects
                    SET name=?, targets=?, out_of_scope=?, context=?, status=?, mode=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        project.name,
                        json.dumps(project.targets),
                        json.dumps(project.out_of_scope),
                        project.context,
                        project.status.value,
                        project.mode.value,
                        project.updated_at.isoformat(),
                        project.id,
                    ),
                )
            await self._conn.commit()  # type: ignore[union-attr]

    # ── Session CRUD ───────────────────────────────────────────────────────

    async def create_session(self, session: Session) -> Session:
        async with self._timed("create_session"):
            async with self._cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO sessions (id, project_id, phase, started_at, ended_at, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.id,
                        session.project_id,
                        session.phase.value,
                        session.started_at.isoformat(),
                        session.ended_at.isoformat() if session.ended_at else None,
                        session.notes,
                    ),
                )
            await self._conn.commit()  # type: ignore[union-attr]
        return session

    async def get_latest_session(self, project_id: str) -> Session | None:
        async with self._timed("get_latest_session"), self._cursor() as cur:
            await cur.execute(
                "SELECT * FROM sessions WHERE project_id=? ORDER BY started_at DESC LIMIT 1",
                (project_id,),
            )
            row = await cur.fetchone()
        return _row_to_session(row) if row else None

    async def update_session_phase(self, session_id: str, phase: SessionPhase) -> None:
        async with self._timed("update_session_phase"):
            async with self._cursor() as cur:
                await cur.execute(
                    "UPDATE sessions SET phase=? WHERE id=?",
                    (phase.value, session_id),
                )
            await self._conn.commit()  # type: ignore[union-attr]

    async def close_session(self, session_id: str) -> None:
        async with self._timed("close_session"):
            async with self._cursor() as cur:
                await cur.execute(
                    "UPDATE sessions SET ended_at=? WHERE id=?",
                    (datetime.utcnow().isoformat(), session_id),
                )
            await self._conn.commit()  # type: ignore[union-attr]

    # ── Finding CRUD ───────────────────────────────────────────────────────

    async def create_finding(self, finding: Finding) -> Finding:
        async with self._timed("create_finding"):
            async with self._cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO findings (
                        id, project_id, session_id, title, description, severity, status,
                        confidence, target, port, service, cve_ids, tool_source,
                        raw_evidence, remediation, related_finding_ids, discovered_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        finding.id,
                        finding.project_id,
                        finding.session_id,
                        finding.title,
                        finding.description,
                        finding.severity.value,
                        finding.status.value,
                        finding.confidence,
                        finding.target,
                        finding.port,
                        finding.service,
                        json.dumps(finding.cve_ids),
                        finding.tool_source,
                        finding.raw_evidence,
                        finding.remediation,
                        json.dumps(finding.related_finding_ids),
                        finding.discovered_at.isoformat(),
                        finding.updated_at.isoformat(),
                    ),
                )
            await self._conn.commit()  # type: ignore[union-attr]
        return finding

    async def update_finding_status(
        self, finding_id: str, status: FindingStatus, note: str = ""
    ) -> None:
        async with self._timed("update_finding_status"):
            async with self._cursor() as cur:
                await cur.execute(
                    "UPDATE findings SET status=?, updated_at=? WHERE id=?",
                    (status.value, datetime.utcnow().isoformat(), finding_id),
                )
            await self._conn.commit()  # type: ignore[union-attr]

    async def list_findings(
        self,
        project_id: str,
        status: FindingStatus | None = None,
    ) -> list[Finding]:
        async with self._timed("list_findings"):  # noqa: SIM117
            async with self._cursor() as cur:
                if status:
                    await cur.execute(
                        "SELECT * FROM findings WHERE project_id=? AND status=? ORDER BY discovered_at DESC",
                        (project_id, status.value),
                    )
                else:
                    await cur.execute(
                        "SELECT * FROM findings WHERE project_id=? ORDER BY discovered_at DESC",
                        (project_id,),
                    )
                rows = await cur.fetchall()
        return [_row_to_finding(r) for r in rows]

    # ── Chat history ───────────────────────────────────────────────────────

    async def append_chat(self, entry: ChatEntry) -> None:
        async with self._timed("append_chat"):
            async with self._cursor() as cur:
                await cur.execute(
                    "INSERT INTO chat_entries (id, project_id, session_id, role, content, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        entry.id,
                        entry.project_id,
                        entry.session_id,
                        entry.role,
                        entry.content,
                        entry.timestamp.isoformat(),
                    ),
                )
            await self._conn.commit()  # type: ignore[union-attr]

    async def get_chat_history(self, session_id: str, limit: int = 100) -> list[ChatEntry]:
        async with self._timed("get_chat_history"), self._cursor() as cur:
            await cur.execute(
                "SELECT * FROM chat_entries WHERE session_id=? ORDER BY timestamp ASC LIMIT ?",
                (session_id, limit),
            )
            rows = await cur.fetchall()
        return [_row_to_chat(r) for r in rows]

    # ── Tasks ──────────────────────────────────────────────────────────────

    async def create_task(self, task: TaskRecord) -> TaskRecord:
        async with self._timed("create_task"):
            async with self._cursor() as cur:
                await cur.execute(
                    "INSERT INTO tasks (id, project_id, session_id, label, tool, status, note, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        task.id,
                        task.project_id,
                        task.session_id,
                        task.label,
                        task.tool,
                        task.status,
                        task.note,
                        task.created_at.isoformat(),
                    ),
                )
            await self._conn.commit()  # type: ignore[union-attr]
        return task

    async def update_task_status(self, task_id: str, status: str, note: str = "") -> None:
        completed_at = datetime.utcnow().isoformat() if status == "done" else None
        async with self._timed("update_task_status"):
            async with self._cursor() as cur:
                await cur.execute(
                    "UPDATE tasks SET status=?, note=?, completed_at=? WHERE id=?",
                    (status, note, completed_at, task_id),
                )
            await self._conn.commit()  # type: ignore[union-attr]

    async def list_tasks(self, session_id: str) -> list[TaskRecord]:
        async with self._timed("list_tasks"), self._cursor() as cur:
            await cur.execute(
                "SELECT * FROM tasks WHERE session_id=? ORDER BY created_at ASC",
                (session_id,),
            )
            rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]


# ── Row deserializers ──────────────────────────────────────────────────────────


def _row_to_project(row: aiosqlite.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        targets=json.loads(row["targets"]),
        out_of_scope=json.loads(row["out_of_scope"]),
        context=row["context"],
        status=row["status"],
        mode=row["mode"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_session(row: aiosqlite.Row) -> Session:
    return Session(
        id=row["id"],
        project_id=row["project_id"],
        phase=row["phase"],
        started_at=datetime.fromisoformat(row["started_at"]),
        ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
        notes=row["notes"],
    )


def _row_to_finding(row: aiosqlite.Row) -> Finding:
    return Finding(
        id=row["id"],
        project_id=row["project_id"],
        session_id=row["session_id"],
        title=row["title"],
        description=row["description"],
        severity=row["severity"],
        status=row["status"],
        confidence=row["confidence"],
        target=row["target"],
        port=row["port"],
        service=row["service"],
        cve_ids=json.loads(row["cve_ids"]),
        tool_source=row["tool_source"],
        raw_evidence=row["raw_evidence"],
        remediation=row["remediation"],
        related_finding_ids=json.loads(row["related_finding_ids"]),
        discovered_at=datetime.fromisoformat(row["discovered_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_chat(row: aiosqlite.Row) -> ChatEntry:
    return ChatEntry(
        id=row["id"],
        project_id=row["project_id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
    )


def _row_to_task(row: aiosqlite.Row) -> TaskRecord:
    return TaskRecord(
        id=row["id"],
        project_id=row["project_id"],
        session_id=row["session_id"],
        label=row["label"],
        tool=row["tool"],
        status=row["status"],
        note=row["note"],
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
    )
