"""LiteLLM async client wrapper for NEMESIS — default backend: Ollama."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

import litellm

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "ollama/llama3.1:8b"
_DEFAULT_BASE_URL = "http://localhost:11434"

litellm.suppress_debug_info = True


@dataclass
class LLMConfig:
    """Configuration for the LLM backend."""

    model: str = _DEFAULT_MODEL
    base_url: str = _DEFAULT_BASE_URL
    temperature: float = 0.3
    max_tokens: int = 2048
    timeout: int = 60
    extra_headers: dict[str, str] = field(default_factory=dict)


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
