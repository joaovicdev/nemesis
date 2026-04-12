"""TargetInputScreen — progressive 3-step onboarding wizard shown after splash."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Input, Static

from nemesis.db.models import Project, Session

logger = logging.getLogger(__name__)

_QUESTIONS = [
    (
        "What will be the target of the attack?",
        "Enter IPs, CIDRs, or hostnames — comma-separated",
        "e.g. 10.0.0.0/24, example.com",
    ),
    (
        "What is outside the scope?",
        "Optional — press enter to skip",
        "e.g. admin.example.com, 10.0.0.1",
    ),
    (
        "Project / client context",
        "Optional — company type, objectives, constraints, rules of engagement",
        "e.g. SaaS startup, black-box, no DoS allowed",
    ),
]


class TargetInputScreen(Screen[None]):
    """Sequential onboarding wizard: target → out-of-scope → context."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "skip_all", "Skip setup", show=False),
    ]

    DEFAULT_CSS = """
    TargetInputScreen {
        align: center middle;
        background: #0a0a0a;
    }

    #ti-header {
        text-align: center;
        color: #00d4ff;
        text-style: bold;
        margin-bottom: 2;
        width: 1fr;
    }

    #ti-box {
        width: 72;
        height: auto;
        border: tall #1a1a3a;
        background: #0f0f1a;
        padding: 2 3;
    }

    #ti-history {
        height: auto;
    }

    .ti-confirmed {
        color: #007a9e;
        margin-bottom: 1;
    }

    #ti-current {
        height: auto;
        margin-top: 1;
    }

    .ti-question {
        color: #e0e0ff;
        text-style: bold;
        margin-bottom: 0;
    }

    .ti-hint {
        color: #555570;
        margin-bottom: 1;
    }

    .ti-error {
        color: #ff4444;
        height: 1;
        margin-bottom: 1;
    }

    #ti-active-input {
        width: 1fr;
        border: tall #1a1a3a;
        background: #141428;
        color: #00d4ff;
        padding: 0 1;
        margin-bottom: 1;
    }

    #ti-active-input:focus {
        border: tall #00d4ff;
    }

    #ti-controls {
        text-align: center;
        color: #1a1a3a;
        margin-top: 1;
        width: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._step = 1
        self._data: dict[str, object] = {}

    def compose(self) -> ComposeResult:
        yield Static("◈  NEMESIS", id="ti-header")
        with Vertical(id="ti-box"):
            yield Vertical(id="ti-history")
            yield Vertical(id="ti-current")
            yield Static(
                "[ enter ] confirm   [ esc ] skip setup",
                id="ti-controls",
            )

    def on_mount(self) -> None:
        asyncio.create_task(self._render_current_step())

    # ── Step rendering ──────────────────────────────────────────────────────

    async def _render_current_step(self) -> None:
        """Swap the contents of #ti-current for the active step."""
        idx = self._step - 1
        question, hint, placeholder = _QUESTIONS[idx]
        current = self.query_one("#ti-current", Vertical)
        await current.remove_children()
        await current.mount(
            Static(question, classes="ti-question"),
            Static(hint, classes="ti-hint"),
            Static("", classes="ti-error", id="ti-error"),
            Input(placeholder=placeholder, id="ti-active-input"),
        )

    def _focus_active_input(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#ti-active-input", Input).focus()

    # ── Input handling ──────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "ti-active-input":
            return
        asyncio.create_task(self._advance(event.value))

    async def _advance(self, raw: str) -> None:
        value = raw.strip()

        if self._step == 1:
            if not value:
                self._show_error("Please enter at least one target.")
                return
            targets = [t.strip() for t in value.split(",") if t.strip()]
            self._data["targets"] = targets
            logger.info(
                "Targets entered in onboarding wizard",
                extra={
                    "event": "tui.target_added",
                    "target_count": len(targets),
                },
            )
            await self._append_history(
                "Target",
                ", ".join(targets),
            )
            self._step = 2

        elif self._step == 2:
            oos = [t.strip() for t in value.split(",") if t.strip()] if value else []
            self._data["out_of_scope"] = oos
            logger.debug(
                "Out-of-scope step completed",
                extra={
                    "event": "tui.out_of_scope_set",
                    "count": len(oos),
                },
            )
            await self._append_history(
                "Excl.  ",
                ", ".join(oos) if oos else "(skipped)",
            )
            self._step = 3

        elif self._step == 3:
            self._data["context"] = value
            asyncio.create_task(self._persist_and_go())
            return

        await self._render_current_step()
        self._focus_active_input()

    def _show_error(self, msg: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#ti-error", Static).update(f"⚠  {msg}")

    async def _append_history(self, label: str, display: str) -> None:
        history = self.query_one("#ti-history", Vertical)
        await history.mount(
            Static(
                f"  [bold #007a9e]✓ {label}:[/]  [#c8c8d8]{display}[/]",
                markup=True,
                classes="ti-confirmed",
            )
        )

    # ── Persistence + transition ────────────────────────────────────────────

    async def _persist_and_go(self) -> None:
        targets: list[str] = list(self._data.get("targets", []))  # type: ignore[arg-type]
        out_of_scope: list[str] = list(self._data.get("out_of_scope", []))  # type: ignore[arg-type]
        context: str = str(self._data.get("context", ""))

        try:
            db = self.app.db  # type: ignore[attr-defined]
            project = Project(
                name=targets[0],
                targets=targets,
                out_of_scope=out_of_scope,
                context=context,
            )
            session = Session(project_id=project.id)
            await db.create_project(project)
            await db.create_session(session)
        except Exception:
            logger.exception(
                "Failed to persist project from onboarding wizard",
                extra={"event": "tui.onboarding_persist_failed"},
            )
            self._show_error("Could not save project to database.")
            return

        logger.info(
            "Onboarding wizard completed — project persisted",
            extra={
                "event": "tui.project_created",
                "project_id": project.id,
                "target_count": len(targets),
            },
        )

        from nemesis.tui.screens.main import MainScreen

        self.app.switch_screen(MainScreen(project=project, session=session))

    # ── Skip ────────────────────────────────────────────────────────────────

    def action_skip_all(self) -> None:
        logger.debug(
            "Onboarding wizard skipped",
            extra={"event": "tui.onboarding_skipped", "step": self._step},
        )
        from nemesis.tui.screens.main import MainScreen

        self.app.switch_screen(MainScreen())
