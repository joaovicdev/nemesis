"""ReportBuilder — generates Markdown and HTML pentest reports from ProjectContext."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from nemesis.core.project import ProjectContext
from nemesis.db.models import Finding, FindingStatus

logger = logging.getLogger(__name__)

# Severity order for sorting (critical first)
_SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

_REPORTABLE_STATUSES: frozenset[FindingStatus] = frozenset(
    (FindingStatus.VALIDATED, FindingStatus.REPORTED)
)


class ReportBuilder:
    """
    Builds a structured pentest report from a ProjectContext.

    Usage:
        builder = ReportBuilder(context)
        md_path = builder.save_markdown(output_dir)
        html_path = builder.save_html(output_dir)
    """

    def __init__(self, context: ProjectContext) -> None:
        self._ctx = context

    # ── Public API ─────────────────────────────────────────────────────────

    def build_markdown(self) -> str:
        """Return the full report as a Markdown string."""
        sections = [
            self._md_header(),
            self._md_executive_summary(),
            self._md_scope(),
            self._md_findings_table(),
            self._md_findings_detail(),
            self._md_methodology(),
            self._md_footer(),
        ]
        return "\n\n---\n\n".join(s for s in sections if s.strip())

    def build_html(self) -> str:
        """Return the full report as an HTML string (self-contained, no external deps)."""
        md_content = self.build_markdown()
        return _wrap_html(self._ctx.project.name, md_content)

    def save_markdown(self, output_dir: Path) -> Path:
        """Write report.md to output_dir and return the path."""
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{_safe_filename(self._ctx.project.name)}_report.md"
        path.write_text(self.build_markdown(), encoding="utf-8")
        logger.info(
            "Markdown report saved",
            extra={"event": "report.saved", "format": "markdown", "path": str(path)},
        )
        return path

    def save_html(self, output_dir: Path) -> Path:
        """Write report.html to output_dir and return the path."""
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{_safe_filename(self._ctx.project.name)}_report.html"
        path.write_text(self.build_html(), encoding="utf-8")
        logger.info(
            "HTML report saved",
            extra={"event": "report.saved", "format": "html", "path": str(path)},
        )
        return path

    # ── Markdown sections ──────────────────────────────────────────────────

    def _md_header(self) -> str:
        p = self._ctx.project
        s = self._ctx.session
        now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        targets = ", ".join(p.targets)
        lines = [
            "# Penetration Test Report",
            "",
            f"**Project:** {p.name}  ",
            f"**Targets:** {targets}  ",
            f"**Phase reached:** {s.phase.value.upper()}  ",
            f"**Report generated:** {now}  ",
        ]
        if p.context:
            lines.append(f"**Engagement context:** {p.context}  ")
        return "\n".join(lines)

    def _md_executive_summary(self) -> str:
        in_report = self._reportable_findings()
        pending_review = self._pending_review_findings()
        counts = _count_by_severity(in_report)

        lines = [
            "## Executive Summary",
            "",
            f"This report summarises validated findings from an authorized penetration test "
            f"against **{', '.join(self._ctx.project.targets)}**.",
            "",
            f"**Findings in this report:** {len(in_report)}",
        ]
        if pending_review:
            lines.append(
                f"**Additional findings under review (not in this report):** {len(pending_review)}"
            )
        lines += [
            "",
            "| Severity | Count |",
            "|----------|-------|",
        ]
        for sev in ("critical", "high", "medium", "low", "info"):
            n = counts.get(sev, 0)
            if n:
                lines.append(f"| {sev.capitalize()} | {n} |")

        if counts.get("critical", 0) + counts.get("high", 0) > 0:
            lines += [
                "",
                "> **⚠ Immediate action required.** Critical and high severity findings "
                "represent direct risks and should be remediated as a priority.",
            ]
        return "\n".join(lines)

    def _md_scope(self) -> str:
        p = self._ctx.project
        lines = ["## Scope", "", "**In-scope targets:**"]
        for t in p.targets:
            lines.append(f"- `{t}`")
        if p.out_of_scope:
            lines += ["", "**Excluded from scope:**"]
            for t in p.out_of_scope:
                lines.append(f"- `{t}`")
        return "\n".join(lines)

    def _md_findings_table(self) -> str:
        findings = self._sorted_reportable()
        if not findings:
            return "## Findings\n\nNo validated findings to include in this report yet."

        lines = [
            "## Findings Summary",
            "",
            "| # | Severity | Title | Target | Port | Status |",
            "|---|----------|-------|--------|------|--------|",
        ]
        for i, f in enumerate(findings, start=1):
            sev = f.severity.value.upper()
            status = f.status.value
            port_cell = f.port if f.port else "—"
            lines.append(f"| {i} | {sev} | {f.title} | `{f.target}` | {port_cell} | {status} |")
        return "\n".join(lines)

    def _md_findings_detail(self) -> str:
        findings = self._sorted_reportable()
        if not findings:
            return ""

        lines = ["## Findings Detail"]
        for i, f in enumerate(findings, start=1):
            sev = f.severity.value.upper()
            port_disp = f.port if f.port else "—"
            svc_disp = f.service if f.service else "—"
            tool_disp = f.tool_source if f.tool_source else "—"
            lines += [
                "",
                f"### {i}. [{sev}] {f.title}",
                "",
                f"**Target:** `{f.target}` | **Port:** {port_disp} | "
                f"**Service:** {svc_disp} | **Tool:** {tool_disp}  ",
                f"**Status:** {f.status.value} | **Confidence:** {f.confidence:.0%}  ",
            ]
            if f.cve_ids:
                lines.append(f"**CVEs:** {', '.join(f.cve_ids)}  ")
            lines += ["", f.description, ""]
            if f.remediation:
                lines += [f"**Remediation:** {f.remediation}", ""]
            if f.raw_evidence:
                evidence_preview = f.raw_evidence[:500]
                if len(f.raw_evidence) > 500:
                    evidence_preview += "\n... (truncated)"
                lines += [
                    "<details><summary>Raw Evidence</summary>",
                    "",
                    "```",
                    evidence_preview,
                    "```",
                    "",
                    "</details>",
                    "",
                ]
        return "\n".join(lines)

    def _md_methodology(self) -> str:
        return (
            "## Methodology\n\n"
            "This assessment was conducted using the NEMESIS automated penetration testing "
            "platform. The following phases were executed:\n\n"
            "- **Reconnaissance** — Passive OSINT, DNS enumeration, WHOIS lookups\n"
            "- **Scanning** — Port discovery and service version detection\n"
            "- **Enumeration** — Web content discovery, directory brute-forcing\n"
            "- **Vulnerability Assessment** — Template-based CVE scanning and analysis\n\n"
            "All activities were conducted within the defined scope and with explicit "
            "authorization."
        )

    def _md_footer(self) -> str:
        now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        return (
            f"---\n\n"
            f"*Report generated by NEMESIS on {now}. "
            f"This report is confidential and intended for authorized recipients only.*"
        )

    # ── Helpers ────────────────────────────────────────────────────────────

    def _reportable_findings(self) -> list[Finding]:
        return [f for f in self._ctx.findings if f.status in _REPORTABLE_STATUSES]

    def _pending_review_findings(self) -> list[Finding]:
        return [
            f
            for f in self._ctx.findings
            if f.status not in _REPORTABLE_STATUSES and f.status != FindingStatus.DISMISSED
        ]

    def _sorted_reportable(self) -> list[Finding]:
        return sorted(
            self._reportable_findings(),
            key=lambda f: _SEVERITY_ORDER.get(f.severity.value, 99),
        )


def _count_by_severity(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        sev = f.severity.value
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def _safe_filename(name: str) -> str:
    """Convert a project name to a safe filename."""
    return re.sub(r"[^\w\-]", "_", name).lower()


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _wrap_html(title: str, markdown_content: str) -> str:
    """
    Wrap markdown content in a minimal self-contained HTML page.

    Uses a simple CSS reset and monospace styling — no external dependencies.
    The markdown is rendered as pre-formatted text; for proper rendering,
    a future iteration can add a JS markdown renderer (marked.js via CDN).
    """
    safe_title = _html_escape(title)
    escaped = _html_escape(markdown_content)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title} — Pentest Report</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: #0a0a0a; color: #e0e0e0;
      max-width: 960px; margin: 0 auto; padding: 2rem;
      line-height: 1.6;
    }}
    pre {{
      background: #111; border: 1px solid #333;
      padding: 1.5rem; border-radius: 6px;
      overflow-x: auto; white-space: pre-wrap;
      font-family: 'Courier New', monospace;
      font-size: 0.9rem; color: #c8f7c5;
    }}
    a {{ color: #00d4ff; }}
  </style>
</head>
<body>
  <pre>{escaped}</pre>
</body>
</html>"""
