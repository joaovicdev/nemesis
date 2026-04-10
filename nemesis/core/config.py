"""Application configuration — loaded from env vars or ~/.nemesis/config.toml."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class NemesisConfig(BaseSettings):
    """
    Runtime configuration for NEMESIS.

    Values are resolved in this priority order:
    1. Environment variables prefixed with NEMESIS_
    2. ~/.nemesis/config.toml (if exists)
    3. Defaults defined here
    """

    model_config = SettingsConfigDict(
        env_prefix="NEMESIS_",
        env_file=str(Path.home() / ".nemesis" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── AI / LLM ──────────────────────────────────────────────────────────
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Ollama server URL",
    )
    model: str = Field(
        default="llama3.1:8b",
        description="Default Ollama model to use",
    )
    model_temperature: float = Field(
        default=0.4,
        ge=0.0,
        le=2.0,
        description="LLM temperature (lower = more deterministic)",
    )
    model_context_window: int = Field(
        default=8192,
        description="Maximum context tokens to send to the model",
    )

    # ── Storage ───────────────────────────────────────────────────────────
    data_dir: Path = Field(
        default=Path.home() / ".nemesis",
        description="Directory for database and project data",
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "nemesis.db"

    # ── Tool paths (override if tools are in non-standard locations) ──────
    nmap_path: str = Field(default="nmap")
    gobuster_path: str = Field(default="gobuster")
    nikto_path: str = Field(default="nikto")
    whois_path: str = Field(default="whois")
    dig_path: str = Field(default="dig")
    curl_path: str = Field(default="curl")

    # ── Behaviour ─────────────────────────────────────────────────────────
    default_mode: str = Field(
        default="step",
        description="Default control mode: auto | step | manual",
    )
    max_parallel_executors: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max concurrent executor agents",
    )
    confirm_destructive: bool = Field(
        default=True,
        description="Always require explicit confirmation for destructive actions",
    )


# Singleton — import and use this throughout the app
config = NemesisConfig()
