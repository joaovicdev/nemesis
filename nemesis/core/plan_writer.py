"""Write attack plans as Markdown files under ~/.nemesis/plans/."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from nemesis.core.config import config
from nemesis.db.models import AttackPlan, PlanStep


def _slug_project_name(name: str) -> str:
    """Safe filename fragment from project name."""
    s = re.sub(r"[^\w\s-]", "", name.lower())
    s = re.sub(r"[-\s]+", "_", s).strip("_")
    return s or "project"


def _format_briefing_markdown(step: PlanStep) -> str:
    """Render analyst_briefing dict as markdown (no Description — caller adds it)."""
    b = step.analyst_briefing
    if not b or not isinstance(b, dict):
        return "### Analyst Briefing\n\n_No structured briefing (offline/default plan)._\n"

    lines: list[str] = []
    obj = b.get("objective")
    if obj and str(obj).strip():
        lines.append(f"**Objective:** {obj}\n")

    look = b.get("look_for")
    if isinstance(look, list) and look:
        lines.append("**Look for:**\n")
        for item in look:
            if str(item).strip():
                lines.append(f"- {item}\n")
        lines.append("")
    elif look and str(look).strip():
        lines.append(f"**Look for:** {look}\n")

    sc = b.get("success_criteria")
    if sc and str(sc).strip():
        lines.append(f"**Success criteria:** {sc}\n")

    risk = b.get("risk_if_skipped")
    if risk and str(risk).strip():
        lines.append(f"**Risk if skipped:** {risk}\n")

    nxt = b.get("next_step_logic")
    if nxt and str(nxt).strip():
        lines.append(f"**Next step logic:** {nxt}\n")

    if not lines:
        return "### Analyst Briefing\n\n_No briefing fields populated._\n"

    return "### Analyst Briefing\n\n" + "\n".join(lines).rstrip() + "\n"


def step_preview_markdown(step: PlanStep) -> str:
    """Markdown for one plan step (e.g. TUI approval preview panel)."""
    tools = ", ".join(f"`{t}`" for t in step.required_tools) if step.required_tools else "—"
    deps = ", ".join(step.depends_on) if step.depends_on else "—"
    return (
        f"## {step.name}\n\n"
        f"**Id:** `{step.id}` · **Agent:** `{step.agent}` · **Tools:** {tools} · "
        f"**Depends on:** {deps}\n\n"
        f"### Description\n\n{step.description}\n\n"
        f"{_format_briefing_markdown(step)}"
    )


def render_plan_markdown(plan: AttackPlan, project_name: str, session_id: str) -> str:
    """Build full markdown document for an attack plan."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    sid_short = session_id[:6] if len(session_id) >= 6 else session_id

    header = (
        f"# NEMESIS Attack Plan\n\n"
        f"**Project:** {project_name}  **Session:** {sid_short}  "
        f"**Generated:** {now}\n\n"
        f"**Goal:** {plan.goal}\n\n"
        f"---\n\n"
    )

    blocks: list[str] = [header]
    for i, step in enumerate(plan.steps, start=1):
        tools = ", ".join(f"`{t}`" for t in step.required_tools) if step.required_tools else "—"
        deps = ", ".join(step.depends_on) if step.depends_on else "—"
        block = (
            f"## Step {i} — {step.name}\n\n"
            f"**Agent:** `{step.agent}` · **Tool(s):** {tools} · **Depends on:** {deps}\n\n"
            f"### Description\n\n{step.description}\n\n"
            f"{_format_briefing_markdown(step)}\n"
            f"---\n\n"
        )
        blocks.append(block)

    return "".join(blocks).rstrip() + "\n"


def write(plan: AttackPlan, project_name: str, session_id: str) -> Path:
    """
    Persist *plan* as a Markdown file and return the path.

    Creates ``config.plans_dir`` if needed.
    """
    plans_dir = config.plans_dir
    plans_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sid = session_id[:6] if len(session_id) >= 6 else session_id
    filename = f"{_slug_project_name(project_name)}_{ts}_{sid}.md"
    path = plans_dir / filename
    path.write_text(render_plan_markdown(plan, project_name, session_id), encoding="utf-8")
    return path
