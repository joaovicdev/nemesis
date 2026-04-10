"""ChatPanel widget — message history and user input."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, RichLog, Static


class MessageRole(str, Enum):
    USER = "user"
    NEMESIS = "nemesis"
    SYSTEM = "system"


@dataclass
class ChatMessage:
    """A single message in the chat history."""

    role: MessageRole
    content: str
    streaming: bool = False


# Role rendering config: (prefix, prefix_color, text_color)
_ROLE_STYLE: dict[MessageRole, tuple[str, str, str]] = {
    MessageRole.NEMESIS: ("[nemesis]", "#007a9e", "#00d4ff"),
    MessageRole.USER:    ("[you]    ", "#333355", "#c8c8d8"),
    MessageRole.SYSTEM:  ("[system] ", "#333333", "#555570"),
}


class ChatPanel(Widget):
    """Main chat interface: scrollable message history + input bar."""

    class UserMessage(Message):
        """Posted when the user submits a message."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    BINDINGS = [
        Binding("ctrl+l", "clear_chat", "Clear", show=False),
    ]

    DEFAULT_CSS = """
    ChatPanel {
        background: #0a0a0a;
        layout: vertical;
    }

    #chat-log {
        background: #0a0a0a;
        padding: 1 2;
        border: none;
        scrollbar-color: #1a1a3a #0a0a0a;
        scrollbar-size: 1 1;
    }

    #input-row {
        background: #0f0f1a;
        border-top: tall #1a1a3a;
        height: 3;
        layout: horizontal;
        align: left middle;
        padding: 0 2;
    }

    #input-prompt {
        color: #00d4ff;
        text-style: bold;
        width: auto;
        margin-right: 1;
    }

    #user-input {
        background: #0f0f1a;
        color: #c8c8d8;
        border: none;
        padding: 0;
        width: 1fr;
    }

    #user-input:focus {
        border: none;
        outline: none;
    }

    #thinking-indicator {
        background: #0a0a0a;
        color: #007a9e;
        height: 1;
        padding: 0 2;
    }
    """

    is_thinking: reactive[bool] = reactive(False)

    def __init__(
        self,
        on_submit: Callable[[str], None] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._on_submit = on_submit
        self._thinking_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", wrap=True, markup=True, highlight=False)
        yield Static("", id="thinking-indicator")
        yield Widget(
            Static("> ", id="input-prompt"),
            Input(placeholder="ask nemesis anything...", id="user-input"),
            id="input-row",
        )

    def on_mount(self) -> None:
        self._append_system(
            "NEMESIS online. No active project — use [bold #00d4ff]ctrl+n[/] to start one."
        )
        self.query_one("#user-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        self._append_message(ChatMessage(role=MessageRole.USER, content=text))
        self.post_message(self.UserMessage(text))

    def watch_is_thinking(self, value: bool) -> None:
        indicator = self.query_one("#thinking-indicator", Static)
        if value:
            self.run_worker(self._animate_thinking(), exclusive=True, name="thinking")
        else:
            if self._thinking_task:
                self._thinking_task.cancel()
            indicator.update("")

    async def _animate_thinking(self) -> None:
        indicator = self.query_one("#thinking-indicator", Static)
        frames = ["⠋", "⠙", "⠸", "⠴", "⠦", "⠇"]
        i = 0
        while True:
            indicator.update(f"  [#007a9e]{frames[i % len(frames)]} thinking...[/]")
            i += 1
            await asyncio.sleep(0.12)

    def _append_message(self, message: ChatMessage) -> None:
        log = self.query_one("#chat-log", RichLog)
        prefix, prefix_color, text_color = _ROLE_STYLE[message.role]
        line = Text()
        line.append(f"{prefix} ", style=f"bold {prefix_color}")
        line.append(message.content, style=text_color)
        log.write(line)

    def _append_system(self, content: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(f"  {content}", style="italic #555570"))

    def append_nemesis(self, content: str) -> None:
        """Add a NEMESIS response to the chat log."""
        self._append_message(ChatMessage(role=MessageRole.NEMESIS, content=content))

    def append_user(self, content: str) -> None:
        """Add a user message to the chat log (used for programmatic injection)."""
        self._append_message(ChatMessage(role=MessageRole.USER, content=content))

    def append_system(self, content: str) -> None:
        """Add a system/status message to the chat log."""
        self._append_system(content)

    def set_thinking(self, thinking: bool) -> None:
        self.is_thinking = thinking

    def action_clear_chat(self) -> None:
        self.query_one("#chat-log", RichLog).clear()
        self._append_system("chat cleared.")
