"""MainScreen — primary layout: header, left panel, chat, status bar."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Label, Static
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer

from nemesis.tui.widgets.chat_panel import ChatPanel
from nemesis.tui.widgets.context_panel import ContextPanel
from nemesis.tui.widgets.status_bar import StatusBar
from nemesis.tui.widgets.task_list import TaskList


_HEADER_LOGO = (
    "  ◈ [bold #00d4ff]NEMESIS[/]"
    "  [#1a1a3a]│[/]"
    "  [#555570]THE ADVERSARY[/]"
    "  [#1a1a3a]│[/]"
    "  [#555570]AI-Assisted Pentest Co-pilot[/]"
)


class MainScreen(Screen[None]):
    """The primary interface screen after boot."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+n", "new_project", "New Project"),
        Binding("ctrl+r", "report", "Report", show=False),
        Binding("f2", "toggle_panel", "Toggle Panel", show=False),
    ]

    DEFAULT_CSS = """
    MainScreen {
        background: #0a0a0a;
        layout: vertical;
    }

    #main-header {
        background: #0a0a0a;
        border-bottom: tall #1a1a3a;
        height: 3;
        padding: 0 2;
        align: left middle;
        color: #00d4ff;
    }

    #main-body {
        layout: horizontal;
        height: 1fr;
    }

    #left-panel {
        background: #0f0f1a;
        border-right: tall #1a1a3a;
        width: 30%;
        min-width: 28;
        max-width: 40;
        layout: vertical;
        overflow-y: auto;
        scrollbar-color: #1a1a3a #0f0f1a;
        scrollbar-size: 1 1;
    }

    #right-panel {
        background: #0a0a0a;
        width: 1fr;
        layout: vertical;
    }

    #header-right {
        width: 1fr;
        text-align: right;
        color: #555570;
        align: right middle;
        padding-right: 1;
    }

    #header-bindings {
        color: #1a1a3a;
        width: auto;
    }
    """

    def compose(self) -> ComposeResult:
        # Header
        with Horizontal(id="main-header"):
            yield Static(_HEADER_LOGO, markup=True)
            yield Static(
                "  [#1a1a3a]ctrl+n[/] [#555570]new[/]"
                "  [#1a1a3a]ctrl+r[/] [#555570]report[/]"
                "  [#1a1a3a]ctrl+c[/] [#555570]quit[/]",
                id="header-bindings",
                markup=True,
            )

        # Main body
        with Horizontal(id="main-body"):
            # Left sidebar
            with Vertical(id="left-panel"):
                yield ContextPanel(id="context-panel")
                yield TaskList(id="task-list")

            # Right: chat
            with Vertical(id="right-panel"):
                yield ChatPanel(id="chat-panel")

        # Footer status bar
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        # Set initial status bar values
        status = self.query_one("#status-bar", StatusBar)
        status.update_model("litellm / not connected")
        status.update_project("no project")
        status.update_phase("—")
        status.update_mode("step")

    def on_chat_panel_user_message(self, event: ChatPanel.UserMessage) -> None:
        """Route user messages to the orchestrator (placeholder)."""
        self._handle_user_message(event.text)

    def _handle_user_message(self, text: str) -> None:
        """Placeholder — will be wired to the Orchestrator in a future milestone."""
        chat = self.query_one("#chat-panel", ChatPanel)
        lower = text.lower().strip()

        # Built-in TUI commands
        if lower in ("new project", "new", "n"):
            self.action_new_project()
            return

        if lower.startswith("mode "):
            mode = lower.split(" ", 1)[1].strip()
            if mode in ("auto", "step", "manual"):
                self.query_one("#status-bar", StatusBar).update_mode(mode)
                chat.append_system(f"mode set to [bold]{mode.upper()}[/].")
            else:
                chat.append_system("unknown mode. use: auto, step, or manual.")
            return

        if lower in ("help", "?"):
            chat.append_nemesis(
                "Available commands:\n"
                "  new project     — start a new engagement\n"
                "  mode auto|step|manual — change control mode\n"
                "  status          — show project status\n"
                "  findings        — list all findings\n"
                "  plan            — show attack plan\n"
                "  report          — generate report\n"
                "\nOr just talk to me in natural language."
            )
            return

        if lower in ("status",):
            ctx = self.query_one("#context-panel", ContextPanel)
            if ctx.project is None:
                chat.append_system("no active project. use 'new project' to start.")
            else:
                p = ctx.project
                chat.append_nemesis(
                    f"Project: {p.name}\n"
                    f"Targets: {', '.join(p.targets)}\n"
                    f"Phase: {p.phase}\n"
                    f"Findings: {p.findings_critical} critical, "
                    f"{p.findings_high} high, "
                    f"{p.findings_medium} medium, "
                    f"{p.findings_low} low"
                )
            return

        # Anything else — forward to orchestrator (not yet implemented)
        chat.append_system(
            "Orchestrator not yet connected. "
            "AI responses will be available in the next milestone."
        )

    def action_new_project(self) -> None:
        from nemesis.tui.screens.new_project import NewProjectScreen

        self.app.push_screen(NewProjectScreen(), self._on_project_created)

    def _on_project_created(self, project_data: dict | None) -> None:
        if project_data is None:
            return

        from nemesis.tui.widgets.context_panel import ProjectSummary

        summary = ProjectSummary(
            name=project_data["name"],
            targets=project_data["targets"],
            phase="RECON",
            mode="step",
        )

        self.query_one("#context-panel", ContextPanel).set_project(summary)

        status = self.query_one("#status-bar", StatusBar)
        status.update_project(project_data["name"])
        status.update_phase("RECON")

        chat = self.query_one("#chat-panel", ChatPanel)
        targets_str = ", ".join(project_data["targets"])
        chat.append_system(f"Project '{project_data['name']}' loaded. Targets: {targets_str}")

        context_hint = project_data.get("context", "")
        if context_hint:
            chat.append_nemesis(
                f"Got it. Context noted: {context_hint}\n\n"
                "I'll adapt my analysis and recommendations accordingly. "
                "What do you want to start with?"
            )
        else:
            chat.append_nemesis(
                "Project ready. Where do you want to start?\n"
                "You can say 'run initial recon', 'show plan', or just ask me anything."
            )

    def action_report(self) -> None:
        chat = self.query_one("#chat-panel", ChatPanel)
        chat.append_system("report generation not yet implemented.")
