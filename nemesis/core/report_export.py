"""Persist pentest reports (Markdown + HTML) from DB-backed or in-memory project state."""

from __future__ import annotations

import asyncio
from pathlib import Path

from nemesis.core.project import ProjectContext
from nemesis.core.report_builder import ReportBuilder
from nemesis.db.database import Database
from nemesis.db.models import Session

_DEFAULT_REPORTS_DIR = Path.home() / ".nemesis" / "reports"


def _save_reports_sync(ctx: ProjectContext, output_dir: Path) -> tuple[Path, Path]:
    builder = ReportBuilder(ctx)
    md_path = builder.save_markdown(output_dir)
    html_path = builder.save_html(output_dir)
    return md_path, html_path


async def export_context_reports(
    ctx: ProjectContext,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Write MD/HTML for the given in-memory context (e.g. active MainScreen session)."""
    out = output_dir or _DEFAULT_REPORTS_DIR
    return await asyncio.to_thread(_save_reports_sync, ctx, out)


async def export_project_reports(
    db: Database,
    project_id: str,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    """
    Load latest project row, latest session (create if missing), all project findings,
    then write MD/HTML.
    """
    project = await db.get_project(project_id)
    if project is None:
        raise ValueError("Project not found")

    session = await db.get_latest_session(project_id)
    if session is None:
        session = Session(project_id=project_id)
        session = await db.create_session(session)

    findings = await db.list_findings(project_id)
    ctx = ProjectContext(project=project, session=session, findings=findings)
    return await export_context_reports(ctx, output_dir)
