# Plan 6 — Report Generation (Markdown + HTML)

## Objetivo

Implementar a geração de relatório de pentest, acessível via `ctrl+r` na TUI (binding já
existe mas retorna "not yet implemented"). O relatório consolida todos os findings validados,
o plano executado, o resumo do engagement e recomendações — em formato Markdown e HTML.

---

## Contexto da Codebase

### Arquivos relevantes

```
nemesis/
  tui/
    screens/
      main.py              ← action_report() — preencher aqui
      report.py            ← CRIAR: ReportScreen
    app.py                 ← não precisa mudar
  core/
    project.py             ← ProjectContext (fonte dos dados)
  db/
    models.py              ← Finding, AttackPlan, PlanStep, Session, Project
    database.py            ← métodos de query existentes
  agents/
    report_builder.py      ← CRIAR: ReportBuilder (lógica pura, sem UI)
```

### Dados disponíveis para o relatório

Em `ProjectContext` (sempre em memória durante a sessão):
- `project.name`, `project.targets`, `project.out_of_scope`, `project.context`
- `session.phase`, `session.started_at`
- `findings: list[Finding]` — todos os findings da sessão

Em `Database` (precisa de query assíncrona):
- `get_attack_plans(project_id)` — não existe ainda, precisará ser criado
- `get_chat_history(session_id)` — existe

Campos do `Finding` relevantes para o report:
- `title`, `description`, `severity`, `status`, `confidence`
- `target`, `port`, `service`
- `cve_ids`, `remediation`, `tool_source`, `raw_evidence`
- `discovered_at`

### `action_report` atual (`tui/screens/main.py`)

```python
def action_report(self) -> None:
    chat = self.query_one("#chat-panel", ChatPanel)
    chat.append_system("Report generation not yet implemented.")
```

### Binding já existe (`tui/screens/main.py`)

```python
Binding("ctrl+r", "report", "Report", show=False),
```

---

## Implementação

### Passo 1 — `ReportBuilder` em `nemesis/agents/report_builder.py`

Criar arquivo novo com a lógica de geração, separada da UI:

```python
"""ReportBuilder — generates Markdown and HTML pentest reports from ProjectContext."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from nemesis.core.project import ProjectContext
from nemesis.db.models import Finding, FindingSeverity, FindingStatus

logger = logging.getLogger(__name__)

# Severity order for sorting (critical first)
_SEVERITY_ORDER: dict[str, int] = {
    "critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4,
}

# Severity badge colours for HTML
_SEVERITY_COLOR: dict[str, str] = {
    "critical": "#ff2b2b",
    "high": "#ff6b2b",
    "medium": "#ffb52b",
    "low": "#2baaff",
    "info": "#aaaaaa",
}


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
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        targets = ", ".join(p.targets)
        return (
            f"# Penetration Test Report\n\n"
            f"**Project:** {p.name}  \n"
            f"**Targets:** {targets}  \n"
            f"**Phase reached:** {s.phase.value.upper()}  \n"
            f"**Report generated:** {now}  \n"
            + (f"**Engagement context:** {p.context}  \n" if p.context else "")
        )

    def _md_executive_summary(self) -> str:
        findings = self._active_findings()
        counts = _count_by_severity(findings)
        validated = [f for f in findings if f.status == FindingStatus.VALIDATED]

        lines = [
            "## Executive Summary",
            "",
            f"This report summarises the findings from an authorized penetration test "
            f"against **{', '.join(self._ctx.project.targets)}**.",
            "",
            f"**Total findings:** {len(findings)} "
            f"({len(validated)} validated, {len(findings) - len(validated)} unverified)",
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
        findings = self._sorted_findings()
        if not findings:
            return "## Findings\n\nNo findings recorded."

        lines = [
            "## Findings Summary",
            "",
            "| # | Severity | Title | Target | Port | Status |",
            "|---|----------|-------|--------|------|--------|",
        ]
        for i, f in enumerate(findings, start=1):
            sev = f.severity.value.upper()
            status = f.status.value
            lines.append(
                f"| {i} | {sev} | {f.title} | `{f.target}` | {f.port or '—'} | {status} |"
            )
        return "\n".join(lines)

    def _md_findings_detail(self) -> str:
        findings = self._sorted_findings()
        if not findings:
            return ""

        lines = ["## Findings Detail"]
        for i, f in enumerate(findings, start=1):
            sev = f.severity.value.upper()
            lines += [
                "",
                f"### {i}. [{sev}] {f.title}",
                "",
                f"**Target:** `{f.target}` | **Port:** {f.port or '—'} | "
                f"**Service:** {f.service or '—'} | **Tool:** {f.tool_source or '—'}  ",
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
            "All activities were conducted within the defined scope and with explicit authorization."
        )

    def _md_footer(self) -> str:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        return (
            f"---\n\n"
            f"*Report generated by NEMESIS on {now}. "
            f"This report is confidential and intended for authorized recipients only.*"
        )

    # ── Helpers ────────────────────────────────────────────────────────────

    def _active_findings(self) -> list[Finding]:
        return [f for f in self._ctx.findings if f.status != FindingStatus.DISMISSED]

    def _sorted_findings(self) -> list[Finding]:
        return sorted(
            self._active_findings(),
            key=lambda f: _SEVERITY_ORDER.get(f.severity.value, 99),
        )


# ── Module-level helpers ───────────────────────────────────────────────────────


def _count_by_severity(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        sev = f.severity.value
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def _safe_filename(name: str) -> str:
    """Convert a project name to a safe filename."""
    import re
    return re.sub(r"[^\w\-]", "_", name).lower()


def _wrap_html(title: str, markdown_content: str) -> str:
    """
    Wrap markdown content in a minimal self-contained HTML page.

    Uses a simple CSS reset and monospace styling — no external dependencies.
    The markdown is rendered as pre-formatted text; for proper rendering,
    a future iteration can add a JS markdown renderer (marked.js via CDN).
    """
    escaped = markdown_content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — Pentest Report</title>
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
```

### Passo 2 — `ReportScreen` em `nemesis/tui/screens/report.py`

Criar arquivo novo — tela simples que mostra o caminho do arquivo salvo:

```python
"""ReportScreen — displays report generation result."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class ReportScreen(ModalScreen[None]):
    """Modal that shows the report was saved and where."""

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    DEFAULT_CSS = """
    ReportScreen {
        align: center middle;
    }
    #report-dialog {
        background: #0f0f1a;
        border: tall #00d4ff;
        width: 70;
        height: auto;
        padding: 2 4;
    }
    #report-title {
        text-style: bold;
        color: #00d4ff;
        margin-bottom: 1;
    }
    #report-paths {
        margin: 1 0;
        color: #aaaaaa;
    }
    #close-btn {
        margin-top: 1;
        width: 100%;
    }
    """

    def __init__(self, md_path: Path, html_path: Path) -> None:
        super().__init__()
        self._md_path = md_path
        self._html_path = html_path

    def compose(self) -> ComposeResult:
        with Static(id="report-dialog"):
            yield Label("Report Generated", id="report-title")
            yield Label(
                f"Markdown: {self._md_path}\nHTML:     {self._html_path}",
                id="report-paths",
            )
            yield Button("Close (Esc)", id="close-btn", variant="primary")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss()
```

### Passo 3 — Implementar `action_report` em `nemesis/tui/screens/main.py`

Substituir:
```python
def action_report(self) -> None:
    chat = self.query_one("#chat-panel", ChatPanel)
    chat.append_system("Report generation not yet implemented.")
```

Por:
```python
def action_report(self) -> None:
    if self._project_ctx is None:
        chat = self.query_one("#chat-panel", ChatPanel)
        chat.append_system("No active project. Load a project first.")
        return
    self.run_worker(
        self._generate_report(),
        exclusive=False,
        name="report-generation",
    )

async def _generate_report(self) -> None:
    from pathlib import Path
    from nemesis.agents.report_builder import ReportBuilder
    from nemesis.tui.screens.report import ReportScreen

    if self._project_ctx is None:
        return

    chat = self.query_one("#chat-panel", ChatPanel)
    chat.append_system("Generating report…")

    try:
        builder = ReportBuilder(self._project_ctx)
        output_dir = Path.home() / ".nemesis" / "reports"
        md_path = await asyncio.to_thread(builder.save_markdown, output_dir)
        html_path = await asyncio.to_thread(builder.save_html, output_dir)
    except Exception:
        logger.exception("[MainScreen] Report generation failed.")
        chat.append_system("Report generation failed. Check logs.")
        return

    chat.append_system(f"Report saved to: {md_path}")
    self.app.push_screen(ReportScreen(md_path, html_path))
```

Adicionar import no topo de `main.py`:
```python
import asyncio   # já deve existir — verificar antes de duplicar
```

---

## Validação

1. `uv run ruff check nemesis/agents/report_builder.py nemesis/tui/screens/report.py nemesis/tui/screens/main.py`
2. `uv run ruff format` nos arquivos alterados
3. Iniciar o NEMESIS, criar um projeto com findings, pressionar `ctrl+r`
4. Verificar que os arquivos são criados em `~/.nemesis/reports/`
5. Abrir o HTML no browser e verificar que é legível
6. Verificar que pressionar `ctrl+r` sem projeto ativo mostra mensagem de erro no chat e não crasha
