"""Executor agent — runs a single tool and returns raw output.

Each Executor is short-lived: one instantiation per tool invocation.
Raw output is NEVER sent directly to the Orchestrator — it must pass through
the Analyst first.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from typing import Protocol


logger = logging.getLogger(__name__)


@dataclass
class ExecutorResult:
    """Result of a single tool execution."""

    task_id: str
    tool: str
    target: str
    exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    success: bool


class ToolNotFoundError(Exception):
    """Raised when the requested system tool is not installed."""


class ScopeViolationError(Exception):
    """Raised when execution is attempted against an out-of-scope target."""


class BaseExecutor:
    """
    Base class for all Executor agents.

    Subclasses implement `_build_command()` for tool-specific argument construction.
    All execution goes through `run()`, which enforces timeouts and captures output.
    """

    # Override in subclasses
    TOOL_NAME: str = ""
    TOOL_BINARY: str = ""
    DESTRUCTIVE: bool = False

    def __init__(
        self,
        task_id: str,
        target: str,
        extra_args: list[str] | None = None,
        timeout: int = 300,
    ) -> None:
        self.task_id = task_id
        self.target = target
        self.extra_args = extra_args or []
        self.timeout = timeout

    async def run(self) -> ExecutorResult:
        """
        Execute the tool and return the raw result.

        Does NOT interpret or analyze the output — that is the Analyst's job.
        """
        binary = self._resolve_binary()
        cmd = self._build_command(binary)

        logger.debug("[%s] Running: %s", self.task_id, " ".join(cmd))

        start = asyncio.get_event_loop().time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logger.warning("[%s] Tool timed out after %ss", self.task_id, self.timeout)
                stdout_b, stderr_b = b"", b"[timed out]"
                proc.returncode = -1

        except FileNotFoundError as exc:
            raise ToolNotFoundError(
                f"Tool '{self.TOOL_BINARY}' not found. "
                f"Install it or set the correct path in config."
            ) from exc

        elapsed = asyncio.get_event_loop().time() - start
        exit_code = proc.returncode or 0

        return ExecutorResult(
            task_id=self.task_id,
            tool=self.TOOL_NAME,
            target=self.target,
            exit_code=exit_code,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            elapsed_seconds=elapsed,
            success=exit_code == 0,
        )

    def _build_command(self, binary: str) -> list[str]:
        """Override to construct the tool-specific argument list."""
        raise NotImplementedError

    def _resolve_binary(self) -> str:
        """Resolve binary path, checking it exists on PATH."""
        binary = self.TOOL_BINARY or self.TOOL_NAME
        if not shutil.which(binary):
            raise ToolNotFoundError(
                f"'{binary}' not found on PATH. "
                f"Install it or configure the path in ~/.nemesis/config."
            )
        return binary


# ── Concrete executor stubs — full implementation in next milestone ────────────


class NmapExecutor(BaseExecutor):
    """Runs nmap against a target."""

    TOOL_NAME = "nmap"
    TOOL_BINARY = "nmap"
    DESTRUCTIVE = False

    def _build_command(self, binary: str) -> list[str]:
        return [binary, "-sV", "-sC", "-T4", self.target, *self.extra_args]


class WhoisExecutor(BaseExecutor):
    """Runs whois lookup."""

    TOOL_NAME = "whois"
    TOOL_BINARY = "whois"
    DESTRUCTIVE = False

    def _build_command(self, binary: str) -> list[str]:
        return [binary, self.target, *self.extra_args]


class GobusterExecutor(BaseExecutor):
    """Runs gobuster directory brute-force."""

    TOOL_NAME = "gobuster"
    TOOL_BINARY = "gobuster"
    DESTRUCTIVE = False

    DEFAULT_WORDLIST = "/usr/share/wordlists/dirb/common.txt"

    def _build_command(self, binary: str) -> list[str]:
        wordlist = next(
            (a for a in self.extra_args if a.startswith("-w")), None
        )
        base = [binary, "dir", "-u", self.target, "-q", "--no-progress"]
        if not wordlist:
            base += ["-w", self.DEFAULT_WORDLIST]
        return base + self.extra_args


class NiktoExecutor(BaseExecutor):
    """Runs nikto web vulnerability scanner."""

    TOOL_NAME = "nikto"
    TOOL_BINARY = "nikto"
    DESTRUCTIVE = False

    def _build_command(self, binary: str) -> list[str]:
        return [binary, "-h", self.target, "-Format", "txt", *self.extra_args]


class DigExecutor(BaseExecutor):
    """Runs DNS enumeration with dig."""

    TOOL_NAME = "dig"
    TOOL_BINARY = "dig"
    DESTRUCTIVE = False

    def _build_command(self, binary: str) -> list[str]:
        return [binary, "ANY", self.target, *self.extra_args]


# Registry: maps tool name → executor class
EXECUTOR_REGISTRY: dict[str, type[BaseExecutor]] = {
    "nmap": NmapExecutor,
    "whois": WhoisExecutor,
    "gobuster": GobusterExecutor,
    "nikto": NiktoExecutor,
    "dig": DigExecutor,
}


def get_executor(
    tool: str,
    task_id: str,
    target: str,
    extra_args: list[str] | None = None,
    timeout: int = 300,
) -> BaseExecutor:
    """Factory — returns the appropriate executor for a given tool name."""
    cls = EXECUTOR_REGISTRY.get(tool.lower())
    if cls is None:
        raise ValueError(
            f"Unknown tool '{tool}'. Available: {list(EXECUTOR_REGISTRY.keys())}"
        )
    return cls(task_id=task_id, target=target, extra_args=extra_args, timeout=timeout)
