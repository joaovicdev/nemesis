"""LiteLLM async client wrapper for NEMESIS — default backend: Ollama."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import litellm
from pydantic import ValidationError

from nemesis.db.models import AgentResponse

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "ollama/llama3.1:8b"
_DEFAULT_BASE_URL = "http://localhost:11434"

litellm.suppress_debug_info = True
litellm.aiohttp_transport = False  # use httpx instead of aiohttp to avoid unclosed-session warnings


@dataclass
class LLMConfig:
    """Configuration for the LLM backend."""

    model: str = _DEFAULT_MODEL
    base_url: str = _DEFAULT_BASE_URL
    temperature: float = 0.3
    max_tokens: int = 2048
    timeout: int = 60
    extra_headers: dict[str, str] = field(default_factory=dict)


_DOTENV_LOADED = False


def _optional_dotenv_paths() -> tuple[Path, ...]:
    """
    Candidate `.env` locations (optional; files may be absent).

    Order: current working directory first, then repository root when running from a
    source checkout (directory containing `pyproject.toml` next to the `nemesis` package).
    Values already set in the process environment are never overwritten (see
    `_ensure_optional_dotenv_loaded`).
    """
    cwd = Path.cwd()
    agents_dir = Path(__file__).resolve().parent
    nemesis_pkg = agents_dir.parent
    repo_root = nemesis_pkg.parent
    paths: list[Path] = [cwd / ".env"]
    if (repo_root / "pyproject.toml").is_file():
        paths.append(repo_root / ".env")
    # Preserve order, drop duplicates
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return tuple(unique)


def _ensure_optional_dotenv_loaded() -> None:
    """Load optional project `.env` files into the environment (does not override existing)."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for path in _optional_dotenv_paths():
        if path.is_file():
            load_dotenv(path, override=False)


def load_llm_config_from_env() -> LLMConfig:
    """
    Build an LLMConfig from environment variables, falling back to defaults.

    If a `.env` file exists in the current working directory or (for a dev checkout)
    next to `pyproject.toml`, it is loaded first. Variables already set in the process
    environment take precedence over `.env`.

    Supported variables:
        NEMESIS_MODEL       — LiteLLM model string (e.g. "ollama/llama3.1:8b")
        NEMESIS_BASE_URL    — API base URL (for local or self-hosted providers)
        NEMESIS_API_KEY     — API key passed as Bearer token in extra_headers
        NEMESIS_TEMPERATURE — float 0.0–1.0
        NEMESIS_MAX_TOKENS  — int
        NEMESIS_TIMEOUT     — int seconds
    """
    _ensure_optional_dotenv_loaded()
    model = os.environ.get("NEMESIS_MODEL", _DEFAULT_MODEL).strip()
    base_url = os.environ.get("NEMESIS_BASE_URL", _DEFAULT_BASE_URL).strip()
    api_key = os.environ.get("NEMESIS_API_KEY", "").strip()

    try:
        temperature = float(os.environ.get("NEMESIS_TEMPERATURE", "0.3"))
    except ValueError:
        temperature = 0.3

    try:
        max_tokens = int(os.environ.get("NEMESIS_MAX_TOKENS", "2048"))
    except ValueError:
        max_tokens = 2048

    try:
        timeout = int(os.environ.get("NEMESIS_TIMEOUT", "60"))
    except ValueError:
        timeout = 60

    extra_headers: dict[str, str] = {}
    if api_key:
        extra_headers["Authorization"] = f"Bearer {api_key}"

    return LLMConfig(
        model=model,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_headers=extra_headers,
    )


class LLMError(Exception):
    """Raised when an LLM call fails unrecoverably."""


class LLMClient:
    """
    Thin async wrapper around LiteLLM.

    Supports Ollama (default) and any other LiteLLM-compatible provider.
    All methods are coroutines — safe to use inside Textual workers and asyncio tasks.
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self._config = config or LLMConfig()

    @property
    def model_name(self) -> str:
        """Human-readable model identifier for display in UI."""
        return self._config.model

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """
        Send a chat completion request and return the full response text.

        Args:
            messages: OpenAI-style message list, e.g. [{"role": "user", "content": "..."}]
            temperature: Override config temperature for this call.
            max_tokens: Override config max_tokens for this call.

        Returns:
            The assistant's reply as a plain string.

        Raises:
            LLMError: On network failure, model not found, or empty response.
        """
        logger.debug(
            "LLM call starting",
            extra={
                "event": "llm.call_started",
                "model": self._config.model,
                "message_count": len(messages),
            },
        )
        t0 = time.monotonic()
        try:
            response = await litellm.acompletion(
                model=self._config.model,
                messages=messages,
                temperature=temperature if temperature is not None else self._config.temperature,
                max_tokens=max_tokens or self._config.max_tokens,
                api_base=self._config.base_url,
                timeout=self._config.timeout,
                extra_headers=self._config.extra_headers or None,
            )
            content = response.choices[0].message.content  # type: ignore[index]
            if not content:
                raise LLMError("LLM returned an empty response.")
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            logger.info(
                "LLM call completed",
                extra={
                    "event": "llm.call_completed",
                    "model": self._config.model,
                    "elapsed_ms": elapsed_ms,
                },
            )
            return content.strip()
        except LLMError:
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            logger.warning(
                "LLM call returned empty response",
                extra={
                    "event": "llm.call_failed",
                    "model": self._config.model,
                    "error_type": "LLMError",
                    "elapsed_ms": elapsed_ms,
                },
            )
            raise
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            logger.warning(
                "LLM call failed",
                extra={
                    "event": "llm.call_failed",
                    "model": self._config.model,
                    "error_type": type(exc).__name__,
                    "elapsed_ms": elapsed_ms,
                },
            )
            raise LLMError(f"LLM call failed: {exc}") from exc

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
    ) -> dict:
        """
        Send a chat request optimized for structured JSON output.

        Automatically retries JSON extraction from the response if the model
        wraps the JSON in markdown code fences.

        Returns:
            Parsed dict from the model's JSON response.

        Raises:
            LLMError: If the response cannot be parsed as JSON after cleanup.
        """
        raw = await self.chat(messages, temperature=temperature, max_tokens=4096)
        return _parse_json_response(raw)

    async def chat_agent_response(
        self,
        messages: list[dict[str, str]],
    ) -> AgentResponse:
        """
        Like chat_json() but validates the response against the AgentResponse schema.

        Falls back to a safe default AgentResponse if validation fails, so callers
        never need to handle a raw dict or a ValidationError.
        """
        raw = await self.chat_json(messages)
        try:
            return AgentResponse.model_validate(raw)
        except ValidationError as exc:
            logger.warning(
                "AgentResponse validation failed — using safe default",
                extra={"event": "llm.agent_response_invalid", "errors": str(exc)},
            )
            return AgentResponse(
                thought="LLM returned invalid schema — using defaults",
                action="fallback",
                tool=None,
                args={},
                result="",
                next_step=None,
            )


def _parse_json_response(raw: str) -> dict:
    """
    Extract and parse a JSON object from an LLM response.

    Handles models that wrap JSON in markdown fences (```json ... ```).
    """
    text = raw.strip()

    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fence_match:
        text = fence_match.group(1).strip()

    # Try direct parse first
    try:
        result = json.loads(text)
        if not isinstance(result, dict):
            raise LLMError(f"Expected a JSON object, got {type(result).__name__}.")
        return result
    except json.JSONDecodeError:
        pass

    # Last resort: find the first {...} block
    brace_match = re.search(r"\{[\s\S]+\}", text)
    if brace_match:
        try:
            result = json.loads(brace_match.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    raise LLMError(f"Could not parse JSON from LLM response: {raw[:200]!r}")
