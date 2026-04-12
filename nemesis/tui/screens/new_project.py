"""NewProjectScreen — 3-step wizard for creating a new engagement."""

from __future__ import annotations

import logging
import re
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TextArea

logger = logging.getLogger(__name__)

# Loose validators for target formats
_IP_PATTERN = re.compile(
    r"^(\d{1,3}\.){3}\d{1,3}(/\d{1,2})?$"  # IPv4 or CIDR
    r"|^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"  # domain
)


def _parse_targets(raw: str) -> list[str]:
    """Split comma/newline separated targets and strip whitespace."""
    parts = re.split(r"[,\n]+", raw)
    return [p.strip() for p in parts if p.strip()]


def _validate_targets(targets: list[str]) -> list[str]:
    """Return list of invalid targets (empty if all valid)."""
    bad = []
    for t in targets:
        if not _IP_PATTERN.match(t):
            bad.append(t)
    return bad


class NewProjectScreen(ModalScreen[dict | None]):
    """
    Modal wizard to configure a new pentest engagement.

    Returns a dict with keys: name, targets, context
    or None if the user cancelled.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    NewProjectScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.85);
    }

    #wizard-box {
        background: #0f0f1a;
        border: tall #1a1a3a;
        width: 72;
        max-height: 90vh;
        height: auto;
        padding: 2 3;
    }

    #wizard-title {
        text-align: center;
        color: #00d4ff;
        text-style: bold;
        margin-bottom: 1;
    }

    #step-indicator {
        text-align: center;
        color: #555570;
        margin-bottom: 2;
    }

    .field-label {
        color: #007a9e;
        text-style: bold;
        margin-top: 1;
        margin-bottom: 0;
    }

    .field-hint {
        color: #555570;
        margin-bottom: 1;
    }

    .field-input {
        background: #141428;
        color: #c8c8d8;
        border: tall #1a1a3a;
        padding: 0 1;
        width: 100%;
    }

    .field-input:focus {
        border: tall #00d4ff;
    }

    #context-area {
        background: #141428;
        color: #c8c8d8;
        border: tall #1a1a3a;
        height: 5;
        width: 100%;
    }

    #context-area:focus {
        border: tall #00d4ff;
    }

    #error-msg {
        color: #ff2040;
        margin-top: 1;
        height: 1;
    }

    #confirm-box {
        background: #141428;
        border: tall #007a9e;
        padding: 1 2;
        margin-top: 1;
    }

    .confirm-label {
        color: #007a9e;
        text-style: bold;
    }

    .confirm-value {
        color: #c8c8d8;
        margin-left: 1;
    }

    #btn-row {
        margin-top: 2;
        align: right middle;
        height: 3;
    }

    #btn-back {
        background: #1a1a3a;
        color: #555570;
        border: none;
        margin-right: 1;
        padding: 0 3;
    }

    #btn-back:hover {
        color: #c8c8d8;
    }

    #btn-next {
        background: #007a9e;
        color: #0a0a0a;
        border: none;
        text-style: bold;
        padding: 0 3;
    }

    #btn-next:hover {
        background: #00d4ff;
    }

    #btn-cancel {
        background: #1a1a3a;
        color: #555570;
        border: none;
        padding: 0 3;
        margin-right: 1;
    }

    #btn-cancel:hover {
        color: #ff2040;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._step = 1
        self._data: dict[str, object] = {}

    # ── Step rendering ─────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Container(id="wizard-box"):
            yield Static("◈ NEW ENGAGEMENT", id="wizard-title")
            yield Static("", id="step-indicator")
            yield Container(id="step-content")
            yield Static("", id="error-msg")
            with Horizontal(id="btn-row"):
                yield Button("cancel", id="btn-cancel", variant="default")
                yield Button("back", id="btn-back", variant="default")
                yield Button("next →", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._render_step()

    def _render_step(self) -> None:
        self.query_one("#step-indicator", Static).update(self._step_indicator_text())
        self.query_one("#error-msg", Static).update("")

        content = self.query_one("#step-content", Container)
        content.remove_children()

        if self._step == 1:
            self._render_step1(content)
        elif self._step == 2:
            self._render_step2(content)
        elif self._step == 3:
            self._render_step3(content)

        # Button labels
        btn_next = self.query_one("#btn-next", Button)
        btn_back = self.query_one("#btn-back", Button)
        btn_next.label = "start →" if self._step == 3 else "next →"
        btn_back.display = self._step > 1

        # Ensure the first input of the current step receives focus after the
        # next render cycle — the Input widgets are mounted dynamically so we
        # must wait for them to be reflected in the focusable-widget list.
        self.call_after_refresh(self._focus_active_input)

    def _focus_active_input(self) -> None:
        """Focus the first text input of the currently rendered wizard step."""
        input_id = {1: "#input-targets", 2: "#input-out-of-scope"}.get(self._step)
        if input_id is None:
            return
        try:
            self.query_one(input_id, Input).focus()
        except Exception:
            pass

    def _step_indicator_text(self) -> Text:
        steps = ["TARGET", "SCOPE/CONTEXT", "CONFIRM"]
        text = Text()
        for i, label in enumerate(steps, 1):
            if i < self._step:
                text.append(f" ✓ {label} ", style="#007a9e")
            elif i == self._step:
                text.append(f" ● {label} ", style="bold #00d4ff")
            else:
                text.append(f" ○ {label} ", style="#1a1a3a")
            if i < len(steps):
                text.append(" ─ ", style="#1a1a3a")
        return text

    def _render_step1(self, parent: Container) -> None:
        parent.mount(Static("Target", classes="field-label"))
        parent.mount(
            Static(
                "IP address, hostname, domain, or CIDR range.\n"
                "Separate multiple targets with commas.",
                classes="field-hint",
            )
        )
        saved = str(self._data.get("targets_raw", ""))
        inp = Input(value=saved, placeholder="192.168.1.0/24, app.target.com", id="input-targets")
        inp.add_class("field-input")
        parent.mount(inp)

        parent.mount(Static("Project name", classes="field-label"))
        parent.mount(Static("A short label for this engagement.", classes="field-hint"))
        saved_name = str(self._data.get("name", ""))
        name_inp = Input(value=saved_name, placeholder="client-xpto-2025", id="input-name")
        name_inp.add_class("field-input")
        parent.mount(name_inp)

    def _render_step2(self, parent: Container) -> None:
        parent.mount(Static("Out of scope", classes="field-label"))
        parent.mount(
            Static(
                "Optional — IPs, CIDRs, or domains explicitly excluded from testing.\n"
                "Separate multiple entries with commas.",
                classes="field-hint",
            )
        )
        saved_oos = str(self._data.get("out_of_scope_raw", ""))
        oos_inp = Input(
            value=saved_oos,
            placeholder="admin.target.com, 10.0.0.1",
            id="input-out-of-scope",
        )
        oos_inp.add_class("field-input")
        parent.mount(oos_inp)

        parent.mount(Static("Engagement context", classes="field-label"))
        parent.mount(
            Static(
                "Optional — but highly recommended.\n"
                "Type of company, objectives, restrictions, rules of engagement.\n"
                "This shapes how NEMESIS prioritizes and analyzes findings.",
                classes="field-hint",
            )
        )
        saved = str(self._data.get("context", ""))
        area = TextArea(saved, id="context-area")
        parent.mount(area)

    def _render_step3(self, parent: Container) -> None:
        parent.mount(Static("Review & confirm", classes="field-label"))

        box = Container(id="confirm-box")
        parent.mount(box)

        name = str(self._data.get("name", ""))
        targets = self._data.get("targets", [])
        out_of_scope = self._data.get("out_of_scope", [])
        context = str(self._data.get("context", "")).strip()

        summary = Text()
        summary.append("  NAME      ", style="bold #007a9e")
        summary.append(f"{name}\n", style="#c8c8d8")
        summary.append("  TARGETS   ", style="bold #007a9e")
        summary.append(f"{', '.join(targets)}\n", style="#c8c8d8")  # type: ignore[arg-type]
        summary.append("  EXCL.     ", style="bold #007a9e")
        if out_of_scope:
            summary.append(f"{', '.join(out_of_scope)}\n", style="#c8c8d8")  # type: ignore[arg-type]
        else:
            summary.append("(none)\n", style="italic #555570")
        summary.append("  CONTEXT   ", style="bold #007a9e")
        if context:
            lines = context.splitlines()
            summary.append(f"{lines[0]}\n", style="#c8c8d8")
            for line in lines[1:]:
                summary.append(f"            {line}\n", style="#c8c8d8")
        else:
            summary.append("(none)\n", style="italic #555570")

        box.mount(Static(summary))

    # ── Navigation ─────────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-cancel":
            self.dismiss(None)
        elif btn_id == "btn-back":
            self._step -= 1
            self._render_step()
        elif btn_id == "btn-next":
            self._advance()

    def _advance(self) -> None:
        if self._step == 1:
            if not self._validate_step1():
                return
            self._step = 2
        elif self._step == 2:
            self._collect_step2()
            self._step = 3
        elif self._step == 3:
            self._finish()
            return
        self._render_step()

    def _validate_step1(self) -> bool:
        try:
            targets_raw = self.query_one("#input-targets", Input).value.strip()
            name = self.query_one("#input-name", Input).value.strip()
        except Exception:
            self._show_error("Could not read inputs.")
            return False

        if not targets_raw:
            self._show_error("Please enter at least one target.")
            return False

        if not name:
            self._show_error("Please enter a project name.")
            return False

        targets = _parse_targets(targets_raw)
        bad = _validate_targets(targets)
        if bad:
            self._show_error(f"Unrecognized target format: {', '.join(bad)}")
            return False

        self._data["targets_raw"] = targets_raw
        self._data["targets"] = targets
        self._data["name"] = name
        return True

    def _collect_step2(self) -> None:
        try:
            oos_raw = self.query_one("#input-out-of-scope", Input).value.strip()
        except Exception:
            oos_raw = ""
        try:
            context = self.query_one("#context-area", TextArea).text.strip()
        except Exception:
            context = ""
        self._data["out_of_scope_raw"] = oos_raw
        self._data["out_of_scope"] = _parse_targets(oos_raw) if oos_raw else []
        self._data["context"] = context

    def _finish(self) -> None:
        targets: list[str] = list(self._data.get("targets", []))  # type: ignore[arg-type]
        logger.info(
            "New project wizard completed",
            extra={
                "event": "tui.new_project_wizard_confirmed",
                "target_count": len(targets),
                "has_out_of_scope": bool(self._data.get("out_of_scope")),
                "has_context": bool(str(self._data.get("context", "")).strip()),
            },
        )
        self.dismiss(
            {
                "name": str(self._data.get("name", "")),
                "targets": targets,
                "out_of_scope": list(self._data.get("out_of_scope", [])),  # type: ignore[arg-type]
                "context": str(self._data.get("context", "")),
            }
        )

    def _show_error(self, msg: str) -> None:
        self.query_one("#error-msg", Static).update(f"  ⚠ {msg}")

    def action_cancel(self) -> None:
        logger.debug(
            "New project wizard cancelled",
            extra={"event": "tui.new_project_wizard_cancelled", "step": self._step},
        )
        self.dismiss(None)
