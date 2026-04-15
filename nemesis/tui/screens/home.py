"""HomeScreen — entry point shown after the splash boot sequence."""

from __future__ import annotations

import logging
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Static

from nemesis.db.models import Project, Session

logger = logging.getLogger(__name__)

_HEADER = "  [bold #00d4ff]◈ NEMESIS[/]  [#1a1a3a]│[/]  [#555570]ENGAGEMENTS[/]"

_EMPTY_MSG = "  No saved projects found.\n\n  Press [bold #00d4ff]n[/] to start a new engagement."


class _ProjectRow(Static):
    """Single selectable project row."""

    DEFAULT_CSS = """
    _ProjectRow {
        background: #0f0f1a;
        padding: 1 2;
        border-bottom: tall #1a1a3a;
        color: #c8c8d8;
    }

    _ProjectRow:hover {
        background: #141428;
    }

    _ProjectRow.selected {
        background: #141428;
        border-left: tall #00d4ff;
    }
    """

    def __init__(self, project: Project, selected: bool = False) -> None:
        super().__init__()
        self.project = project
        if selected:
            self.add_class("selected")

    def render(self) -> Text:
        p = self.project
        text = Text()

        is_selected = "selected" in self.classes
        bullet = "●" if is_selected else "○"
        bullet_style = "#00d4ff" if is_selected else "#333350"

        text.append(f" {bullet} ", style=f"bold {bullet_style}")
        text.append(f"{p.name}\n", style="bold #c8c8d8")

        targets_str = ", ".join(p.targets[:3])
        if len(p.targets) > 3:
            targets_str += f" +{len(p.targets) - 3} more"
        text.append(f"   {targets_str}\n", style="#555570")

        status_color = "#00ff7f" if p.status.value == "active" else "#555570"
        text.append(f"   {p.status.value}", style=status_color)
        text.append(f"  ·  {p.updated_at.strftime('%Y-%m-%d')}", style="#333350")

        return text

    def on_click(self) -> None:
        self.screen.post_message(HomeScreen.ProjectClicked(self.project))


class HomeScreen(Screen[None]):
    """Full-screen project picker and entry point for the application."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "open_project", "Open", show=False),
        Binding("n", "new_project", "New Engagement"),
        Binding("q", "quit_app", "Quit"),
    ]

    DEFAULT_CSS = """
    HomeScreen {
        background: #0a0a14;
        layout: vertical;
    }

    #home-header {
        background: #0a0a14;
        border-bottom: tall #1a1a3a;
        height: 3;
        padding: 0 2;
        align: left middle;
        color: #00d4ff;
    }

    #project-list {
        height: 1fr;
        overflow-y: auto;
        scrollbar-color: #1a1a3a #0a0a14;
        scrollbar-size: 1 1;
    }

    #empty-msg {
        color: #555570;
        padding: 3 4;
    }

    #home-footer {
        background: #0a0a14;
        border-top: tall #1a1a3a;
        height: 3;
        padding: 0 2;
        align: left middle;
        color: #333350;
    }
    """

    class ProjectClicked(Message):
        """Posted when a project row is clicked directly."""

        def __init__(self, project: Project) -> None:
            super().__init__()
            self.project = project

    def __init__(self) -> None:
        super().__init__()
        self._projects: list[Project] = []
        self._selected_idx: int = 0

    def compose(self) -> ComposeResult:
        yield Static(_HEADER, id="home-header", markup=True)
        yield ScrollableContainer(id="project-list")
        yield Static(
            "  [#333350]↑↓[/] [#555570]navigate[/]"
            "   [#333350]enter[/] [#555570]open[/]"
            "   [#333350]n[/] [#555570]new engagement[/]"
            "   [#333350]q[/] [#555570]quit[/]",
            id="home-footer",
            markup=True,
        )

    async def on_mount(self) -> None:
        await self._refresh_projects()

    async def _refresh_projects(self) -> None:
        try:
            self._projects = await self.app.db.list_projects()  # type: ignore[attr-defined]
        except Exception:
            logger.exception("Failed to load project list")
            self._projects = []

        list_container = self.query_one("#project-list", ScrollableContainer)
        await list_container.remove_children()

        if not self._projects:
            await list_container.mount(Static(_EMPTY_MSG, id="empty-msg", markup=True))
            self._selected_idx = 0
            return

        self._selected_idx = min(self._selected_idx, len(self._projects) - 1)
        for i, project in enumerate(self._projects):
            await list_container.mount(_ProjectRow(project, selected=(i == self._selected_idx)))

    def _get_rows(self) -> list[_ProjectRow]:
        return list(self.query(_ProjectRow))

    def _update_selection(self, new_idx: int) -> None:
        rows = self._get_rows()
        if not rows:
            return
        new_idx = max(0, min(new_idx, len(rows) - 1))
        for i, row in enumerate(rows):
            if i == new_idx:
                row.add_class("selected")
            else:
                row.remove_class("selected")
        self._selected_idx = new_idx
        rows[new_idx].scroll_visible()

    def action_move_up(self) -> None:
        self._update_selection(self._selected_idx - 1)

    def action_move_down(self) -> None:
        self._update_selection(self._selected_idx + 1)

    # Navigation that calls switch_screen must use app.run_worker, not
    # self.run_worker: screen-bound workers are cancelled when HomeScreen
    # unmounts, which aborts the screen transition and can tear down the app.

    def action_open_project(self) -> None:
        if not self._projects:
            return
        project = self._projects[self._selected_idx]
        self.app.run_worker(self._load_project_and_open(project), exclusive=True)

    def action_new_project(self) -> None:
        from nemesis.tui.screens.new_project import NewProjectScreen

        self.app.push_screen(NewProjectScreen(), self._on_project_created)

    def action_quit_app(self) -> None:
        self.app.exit()

    def on_home_screen_project_clicked(self, event: HomeScreen.ProjectClicked) -> None:
        idx = next(
            (i for i, p in enumerate(self._projects) if p.id == event.project.id),
            self._selected_idx,
        )
        self._update_selection(idx)

    def _on_project_created(self, data: dict | None) -> None:
        if data is None:
            return
        self.app.run_worker(self._create_and_open(data), exclusive=True)

    async def _create_and_open(self, data: dict) -> None:
        db = self.app.db  # type: ignore[attr-defined]
        try:
            project = Project(
                name=data["name"],
                targets=data["targets"],
                out_of_scope=data.get("out_of_scope", []),
                context=data.get("context", ""),
            )
            project = await db.create_project(project)
            session = Session(project_id=project.id)
            session = await db.create_session(session)
        except Exception:
            logger.exception("Failed to create project")
            return

        logger.info(
            "New project created from home screen",
            extra={"event": "tui.home_project_created", "project_id": project.id},
        )
        await self._open_main(project, session)

    async def _load_project_and_open(self, project: Project) -> None:
        db = self.app.db  # type: ignore[attr-defined]
        try:
            session = await db.get_latest_session(project.id)
            if session is None:
                session = Session(project_id=project.id)
                session = await db.create_session(session)
        except Exception:
            logger.exception("Failed to load session for project")
            return

        logger.info(
            "Project opened from home screen",
            extra={"event": "tui.home_project_opened", "project_id": project.id},
        )
        await self._open_main(project, session)

    async def _open_main(self, project: Project, session: Session) -> None:
        from nemesis.tui.screens.main import MainScreen

        await self.app.switch_screen(MainScreen(project=project, session=session))

    # Allow ctrl+n and ctrl+l from app-level bindings to also work on this screen
    def action_new_project_global(self) -> None:
        self.action_new_project()

    def action_load_project_global(self) -> None:
        from nemesis.tui.screens.load_project import LoadProjectScreen

        self.app.push_screen(LoadProjectScreen(), self._on_project_loaded)

    def _on_project_loaded(self, project: Project | None) -> None:
        if project is None:
            return
        self.app.run_worker(self._load_project_and_open(project), exclusive=True)
