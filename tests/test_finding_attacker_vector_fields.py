from __future__ import annotations

import json

import aiosqlite
import pytest

from nemesis.db.database import _row_to_finding


@pytest.mark.asyncio
async def test_row_to_finding_defaults_when_columns_missing() -> None:
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            CREATE TABLE findings (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'raw',
                confidence REAL NOT NULL DEFAULT 0.0,
                target TEXT NOT NULL DEFAULT '',
                port TEXT NOT NULL DEFAULT '',
                service TEXT NOT NULL DEFAULT '',
                cve_ids TEXT NOT NULL DEFAULT '[]',
                tool_source TEXT NOT NULL DEFAULT '',
                raw_evidence TEXT NOT NULL DEFAULT '',
                remediation TEXT NOT NULL DEFAULT '',
                related_finding_ids TEXT NOT NULL DEFAULT '[]',
                discovered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO findings (
                id, project_id, session_id, title, description, severity, status, confidence,
                target, port, service, cve_ids, tool_source, raw_evidence, remediation,
                related_finding_ids, discovered_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "f-1",
                "p-1",
                "s-1",
                "Test finding",
                "Something was found.",
                "medium",
                "unverified",
                0.8,
                "example.com",
                "443",
                "https",
                json.dumps(["CVE-2020-0000"]),
                "nuclei",
                "raw",
                "fix it",
                json.dumps([]),
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        await conn.commit()

        async with conn.execute("SELECT * FROM findings LIMIT 1") as cur:
            row = await cur.fetchone()
        assert row is not None

        finding = _row_to_finding(row)
        assert finding.attack_path_steps == []
        assert finding.impact_assessment == ""
        assert finding.remediation_guidance == ""


@pytest.mark.asyncio
async def test_row_to_finding_roundtrip_new_columns() -> None:
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            CREATE TABLE findings (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'raw',
                confidence REAL NOT NULL DEFAULT 0.0,
                target TEXT NOT NULL DEFAULT '',
                port TEXT NOT NULL DEFAULT '',
                service TEXT NOT NULL DEFAULT '',
                cve_ids TEXT NOT NULL DEFAULT '[]',
                tool_source TEXT NOT NULL DEFAULT '',
                raw_evidence TEXT NOT NULL DEFAULT '',
                remediation TEXT NOT NULL DEFAULT '',
                attack_path_steps TEXT NOT NULL DEFAULT '[]',
                impact_assessment TEXT NOT NULL DEFAULT '',
                remediation_guidance TEXT NOT NULL DEFAULT '',
                related_finding_ids TEXT NOT NULL DEFAULT '[]',
                discovered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO findings (
                id, project_id, session_id, title, description, severity, status, confidence,
                target, port, service, cve_ids, tool_source, raw_evidence, remediation,
                attack_path_steps, impact_assessment, remediation_guidance,
                related_finding_ids, discovered_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "f-2",
                "p-1",
                "s-1",
                "Test finding 2",
                "Desc",
                "high",
                "unverified",
                0.9,
                "example.com",
                "80",
                "http",
                json.dumps([]),
                "ffuf",
                "raw",
                "summary remediation",
                json.dumps(["Step one", "Step two"]),
                "Impact text",
                "Detailed guidance",
                json.dumps([]),
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        await conn.commit()

        async with conn.execute("SELECT * FROM findings LIMIT 1") as cur:
            row = await cur.fetchone()
        assert row is not None

        finding = _row_to_finding(row)
        assert finding.attack_path_steps == ["Step one", "Step two"]
        assert finding.impact_assessment == "Impact text"
        assert finding.remediation_guidance == "Detailed guidance"
