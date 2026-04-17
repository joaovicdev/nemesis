"""Executor agent — runs a single tool and returns raw output.

Each Executor is short-lived: one instantiation per tool invocation.
Raw output is NEVER sent directly to the Orchestrator — it must pass through
the Analyst first.

Command lines are built exclusively from ToolDefinition (kali_tools.yml) plus
target and extra_args — no per-tool executor subclasses.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass

from nemesis.core.config import config
from nemesis.core.wordlists import resolve_ffuf_wordlist, resolve_gobuster_dir_wordlist
from nemesis.tools.base import TOOL_REGISTRY, ToolDefinition

logger = logging.getLogger(__name__)

_NUCLEI_DEFAULT_SEVERITY = "medium,high,critical"


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


def _target_url(target: str) -> str:
    t = target.strip()
    if t.startswith(("http://", "https://")):
        return t
    return f"http://{t}"


def _expand_arg_placeholders(token: str, target: str) -> str:
    """Replace placeholders inside a single argv token."""
    out = token
    out = out.replace("{target}", target)
    out = out.replace("{target_url}", _target_url(target))
    if "{wordlist_ffuf}" in out:
        out = out.replace(
            "{wordlist_ffuf}",
            resolve_ffuf_wordlist(None, config.default_ffuf_wordlist),
        )
    if "{wordlist_gobuster}" in out:
        out = out.replace("{wordlist_gobuster}", resolve_gobuster_dir_wordlist())
    return out


def _expand_default_args(default_args: list[str], target: str) -> list[str]:
    return [_expand_arg_placeholders(a, target) for a in default_args]


def _ffuf_extra_has_wordlist(extra_args: list[str]) -> bool:
    for i, a in enumerate(extra_args):
        if a == "-w" or a == "--wordlist":
            return True
        if a.startswith("-w") and len(a) > 2:
            return True
        if a == "-w" and i + 1 < len(extra_args):
            return True
    return False


def _gobuster_extra_has_wordlist(extra_args: list[str]) -> bool:
    for i, a in enumerate(extra_args):
        if a in ("-w", "--wordlist"):
            return True
        if a.startswith("-w") and len(a) > 2 and not a.startswith("-wo"):
            return True
        if a == "-w" and i + 1 < len(extra_args):
            return True
    return False


def build_argv(defn: ToolDefinition, target: str, extra_args: list[str]) -> list[str]:
    """
    Build argv for subprocess from manifest definition, target, and extra args.

    Raises:
        ValueError: Unknown invocation_profile.
        FileNotFoundError: Wordlist resolution failed (ffuf / gobuster profiles).
    """
    binary = defn.binary
    profile = (defn.invocation_profile or "").strip()

    if profile == "nmap_default_kali":
        return [binary, "-sV", "-sC", "-T4", target, *extra_args]

    if profile == "ffuf_kali":
        target_url = _target_url(target)
        cmd = [
            binary,
            "-u",
            f"{target_url}/FUZZ",
            "-mc",
            "all",
            "-ac",
            "-of",
            "json",
            "-o",
            "/dev/stdout",
            "-s",
        ]
        if not _ffuf_extra_has_wordlist(extra_args):
            cmd += ["-w", resolve_ffuf_wordlist(None, config.default_ffuf_wordlist)]
        return cmd + extra_args

    if profile == "gobuster_dir_kali":
        base = [binary, "dir", "-u", _target_url(target), "-q", "--no-progress"]
        if not _gobuster_extra_has_wordlist(extra_args):
            base += ["-w", resolve_gobuster_dir_wordlist()]
        return base + extra_args

    if profile == "nuclei_default_kali":
        cmd = [
            binary,
            "-u",
            _target_url(target),
            "-severity",
            _NUCLEI_DEFAULT_SEVERITY,
            "-silent",
            "-no-color",
        ]
        return cmd + extra_args

    if profile:
        raise ValueError(f"Unknown invocation_profile '{profile}' for tool '{defn.name}'")

    expanded = _expand_default_args(defn.default_args, target)
    return [binary, *expanded, *extra_args]


class ManifestExecutor:
    """Runs an external binary using argv from ToolDefinition (manifest-only)."""

    def __init__(
        self,
        definition: ToolDefinition,
        task_id: str,
        target: str,
        extra_args: list[str] | None = None,
        timeout: int = 300,
    ) -> None:
        self._defn = definition
        self.task_id = task_id
        self.target = target
        self.extra_args = extra_args or []
        self.timeout = timeout

    @property
    def tool_name(self) -> str:
        return self._defn.name

    @property
    def destructive(self) -> bool:
        return self._defn.destructive

    def _build_command(self, _binary: str) -> list[str]:
        return build_argv(self._defn, self.target, self.extra_args)

    async def run(self) -> ExecutorResult:
        """
        Execute the tool and return the raw result.

        Does NOT interpret or analyze the output — that is the Analyst's job.
        """
        binary = self._resolve_binary()
        try:
            cmd = self._build_command(binary)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        logger.info(
            "Tool execution started",
            extra={
                "event": "executor.tool_started",
                "tool": self._defn.name,
                "task_id": self.task_id,
            },
        )

        t0 = time.monotonic()
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
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                elapsed_ms = round((time.monotonic() - t0) * 1000)
                logger.warning(
                    "Tool timed out",
                    extra={
                        "event": "executor.tool_timeout",
                        "tool": self._defn.name,
                        "task_id": self.task_id,
                        "timeout_s": self.timeout,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                stdout_b, stderr_b = b"", b"[timed out]"
                proc.returncode = -1

        except FileNotFoundError as exc:
            raise ToolNotFoundError(
                f"Tool '{self._defn.binary}' not found. "
                f"Install it or set the correct path in config."
            ) from exc

        elapsed = time.monotonic() - t0
        exit_code = proc.returncode or 0
        stdout_str = stdout_b.decode("utf-8", errors="replace")
        logger.info(
            "Tool execution completed",
            extra={
                "event": "executor.tool_completed",
                "tool": self._defn.name,
                "task_id": self.task_id,
                "exit_code": exit_code,
                "elapsed_ms": round(elapsed * 1000),
                "stdout_bytes": len(stdout_b),
                "stderr_bytes": len(stderr_b),
                "success": exit_code == 0,
            },
        )

        return ExecutorResult(
            task_id=self.task_id,
            tool=self._defn.name,
            target=self.target,
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_b.decode("utf-8", errors="replace"),
            elapsed_seconds=elapsed,
            success=exit_code == 0,
        )

    async def run_streaming(
        self,
        on_line: Callable[[str], None],
    ) -> ExecutorResult:
        """Run the tool and fire on_line() for each stdout line in real-time."""
        binary = self._resolve_binary()
        try:
            cmd = self._build_command(binary)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        logger.info(
            "Tool streaming started",
            extra={
                "event": "executor.tool_started",
                "tool": self._defn.name,
                "task_id": self.task_id,
                "mode": "streaming",
            },
        )

        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ToolNotFoundError(
                f"Tool '{self._defn.binary}' not found. "
                f"Install it or set the correct path in config."
            ) from exc

        stdout_lines: list[str] = []
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            stdout_lines.append(line)
            on_line(line)

        _, stderr_b = await proc.communicate()
        elapsed = time.monotonic() - t0
        exit_code = proc.returncode or 0
        stdout_joined = "\n".join(stdout_lines)
        logger.info(
            "Tool streaming completed",
            extra={
                "event": "executor.tool_completed",
                "tool": self._defn.name,
                "task_id": self.task_id,
                "exit_code": exit_code,
                "elapsed_ms": round(elapsed * 1000),
                "stdout_bytes": sum(len(line) for line in stdout_lines),
                "stderr_bytes": len(stderr_b),
                "success": exit_code == 0,
                "mode": "streaming",
            },
        )
        return ExecutorResult(
            task_id=self.task_id,
            tool=self._defn.name,
            target=self.target,
            exit_code=exit_code,
            stdout=stdout_joined,
            stderr=stderr_b.decode("utf-8", errors="replace"),
            elapsed_seconds=elapsed,
            success=exit_code == 0,
        )

    def _resolve_binary(self) -> str:
        """Resolve binary path, checking it exists on PATH."""
        binary = self._defn.binary
        if not shutil.which(binary):
            raise ToolNotFoundError(
                f"'{binary}' not found on PATH. "
                f"Install it or configure the path in ~/.nemesis/config."
            )
        return binary


def get_executor(
    tool: str,
    task_id: str,
    target: str,
    extra_args: list[str] | None = None,
    timeout: int = 300,
) -> ManifestExecutor:
    """Return a manifest-backed executor for an installed tool name."""
    key = tool.lower().strip()
    defn = TOOL_REGISTRY.get(key)
    if defn is None:
        available = sorted(TOOL_REGISTRY.keys())
        raise ValueError(f"Unknown tool '{tool}'. Available: {available}")
    return ManifestExecutor(
        defn,
        task_id=task_id,
        target=target,
        extra_args=extra_args,
        timeout=timeout,
    )


# Back-compat alias for code expecting BaseExecutor typing
BaseExecutor = ManifestExecutor
