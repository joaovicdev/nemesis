"""LoadProjectScreen — modal project picker for switching engagements."""

from __future__ import annotations

import logging
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from nemesis.db.models import Project

logger = logging.getLogger(__name__)

_NO_PROJECTS_MSG = "No saved projects found.\n\nPress [bold]ctrl+n[/] to start a new engagement."


class _ProjectRow(Static):
    """A single selectable project row."""

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

        bullet = "●" if "selected" in self.classes else "○"
        bullet_style = "#00d4ff" if "selected" in self.classes else "#1a1a3a"
        text.append(f" {bullet} ", style=f"bold {bullet_style}")
        text.append(f"{p.name}\n", style="bold #c8c8d8")

        targets_str = ", ".join(p.targets[:3])
        if len(p.targets) > 3:
            targets_str += f" +{len(p.targets) - 3} more"
        text.append(f"   {targets_str}\n", style="#555570")

        if p.out_of_scope:
            oos_str = ", ".join(p.out_of_scope[:2])
            if len(p.out_of_scope) > 2:
                oos_str += f" +{len(p.out_of_scope) - 2} excl."
            text.append(f"   excl: {oos_str}\n", style="#444460")

        status_color = "#00ff7f" if p.status.value == "active" else "#555570"
        text.append(f"   {p.status.value}", style=status_color)
        text.append(f"  ·  {p.updated_at.strftime('%Y-%m-%d')}\n", style="#333350")

        return text

    def on_click(self) -> None:
        self.post_message(LoadProjectScreen.ProjectSelected(self.project))


class LoadProjectScreen(ModalScreen["Project | None"]):
    """
    Modal project picker.

    Returns the selected Project or None if the user cancelled.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "confirm", "Load", show=False),
    ]

    DEFAULT_CSS = """
    LoadProjectScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.85);
    }

    #picker-box {
        background: #0f0f1a;
        border: tall #1a1a3a;
        width: 72;
        max-height: 80vh;
        height: auto;
    }

    #picker-title {
        text-align: center;
        color: #00d4ff;
        text-style: bold;
        background: #0a0a14;
        border-bottom: tall #1a1a3a;
        height: 3;
        padding: 0 2;
        content-align: center middle;
    }

    #project-list {
        height: auto;
        max-height: 60vh;
        overflow-y: auto;
        scrollbar-color: #1a1a3a #0f0f1a;
        scrollbar-size: 1 1;
    }

    #empty-msg {
        color: #555570;
        padding: 2 3;
        text-align: center;
    }

    #picker-footer {
        background: #0a0a14;
        border-top: tall #1a1a3a;
        height: 3;
        padding: 0 2;
        align: right middle;
    }

    #btn-load {
        background: #007a9e;
        color: #0a0a0a;
        border: none;
        text-style: bold;
        padding: 0 3;
        margin-left: 1;
    }

    #btn-load:hover {
        background: #00d4ff;
    }

    #btn-load:disabled {
        background: #1a1a3a;
        color: #333350;
    }

    #btn-cancel {
        background: #1a1a3a;
        color: #555570;
        border: none;
        padding: 0 3;
    }

    #btn-cancel:hover {
        color: #ff2040;
    }

    #footer-hint {
        color: #333350;
        width: 1fr;
    }
    """

    class ProjectSelected(Message):
        """Posted when a project row is clicked."""

        def __init__(self, project: Project) -> None:
            super().__init__()
            self.project = project

    def __init__(self) -> None:
        super().__init__()
        self._projects: list[Project] = []
        self._selected_idx: int = 0

    def compose(self) -> ComposeResult:
        with Container(id="picker-box"):
            yield Static("◈ LOAD PROJECT", id="picker-title")
            yield ScrollableContainer(id="project-list")
            with Horizontal(id="picker-footer"):
                yield Static("↑↓ navigate  enter load  esc cancel", id="footer-hint")
                yield Button("cancel", id="btn-cancel", variant="default")
                yield Button("load →", id="btn-load", variant="primary", disabled=True)

    async def on_mount(self) -> None:
        await self._load_projects()

    async def _load_projects(self) -> None:
        try:
            app = self.app
            if hasattr(app, "db"):
                self._projects = await app.db.list_projects()  # type: ignore[union-attr]
        except Exception:
            self._projects = []

        list_container = self.query_one("#project-list", ScrollableContainer)
        list_container.remove_children()

        if not self._projects:
            list_container.mount(Static(_NO_PROJECTS_MSG, id="empty-msg", markup=True))
            return

        for i, project in enumerate(self._projects):
            row = _ProjectRow(project, selected=(i == 0))
            list_container.mount(row)

        self._selected_idx = 0
        self.query_one("#btn-load", Button).disabled = False

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

    def action_confirm(self) -> None:
        if not self._projects:
            return
        selected = self._projects[self._selected_idx]
        logger.info(
            "Load project picker confirmed",
            extra={
                "event": "tui.load_project_confirmed",
                "project_id": selected.id,
            },
        )
        self.dismiss(selected)

    def action_cancel(self) -> None:
        logger.debug(
            "Load project picker cancelled",
            extra={"event": "tui.load_project_cancelled"},
        )
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-load":
            self.action_confirm()
        elif event.button.id == "btn-cancel":
            self.action_cancel()

    def on_load_project_screen_project_selected(
        self, event: LoadProjectScreen.ProjectSelected
    ) -> None:
        idx = next(
            (i for i, p in enumerate(self._projects) if p.id == event.project.id),
            self._selected_idx,
        )
        self._update_selection(idx)
