"""Wordlist resolution helpers (Kali-first defaults).

NEMESIS runs primarily on Kali Linux. This module centralizes wordlist defaults so
executors and TUI can provide consistent, user-visible choices.
"""

from __future__ import annotations

from pathlib import Path

KALI_DEFAULT_SENTINEL = "kali_default"

# Candidates ordered by preference for Kali Linux.
#
# Notes:
# - `/usr/share/seclists/...` is commonly available on Kali when `seclists` is installed.
# - `dirb` and `dirbuster` lists are common fallbacks.
FFUF_WORDLIST_CANDIDATES_KALI: list[str] = [
    "/usr/share/seclists/Discovery/Web-Content/raft-medium-directories.txt",
    "/usr/share/seclists/Discovery/Web-Content/common.txt",
    "/usr/share/wordlists/dirb/common.txt",
    "/usr/share/wordlists/dirbuster/directory-list-2.3-small.txt",
]

# gobuster dir — same Kali-first preference order as legacy executor
GOBUSTER_WORDLIST_CANDIDATES_KALI: list[str] = [
    "/usr/share/wordlists/dirb/common.txt",
    "/usr/share/seclists/Discovery/Web-Content/common.txt",
]


def first_existing(candidates: list[str]) -> str | None:
    """Return the first candidate path that exists on disk."""
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def resolve_ffuf_wordlist(preference: str | None, config_default: str | None) -> str:
    """
    Resolve an ffuf wordlist path.

    Resolution order:
    1) If preference is a valid, existing path: use it.
    2) If config_default is a valid, existing path: use it.
    3) If preference is None/empty or `kali_default`: use the first existing Kali candidate.

    Raises:
        FileNotFoundError: If no suitable wordlist can be found/resolved.
    """
    pref = (preference or "").strip()
    cfg = (config_default or "").strip()

    if pref and pref != KALI_DEFAULT_SENTINEL:
        if Path(pref).exists():
            return pref
        raise FileNotFoundError(f"Wordlist not found: {pref}")

    if cfg:
        if Path(cfg).exists():
            return cfg
        raise FileNotFoundError(f"Configured ffuf wordlist not found: {cfg}")

    resolved = first_existing(FFUF_WORDLIST_CANDIDATES_KALI)
    if resolved:
        return resolved

    raise FileNotFoundError(
        "No ffuf wordlist found. "
        "Install seclists/dirb wordlists or configure NEMESIS_DEFAULT_FFUF_WORDLIST."
    )


def resolve_gobuster_dir_wordlist() -> str:
    """
    Return the first existing path suitable for `gobuster dir -w`.

    Raises:
        FileNotFoundError: If no candidate exists.
    """
    resolved = first_existing(GOBUSTER_WORDLIST_CANDIDATES_KALI)
    if resolved:
        return resolved
    raise FileNotFoundError(
        "No gobuster wordlist found. Install dirb/seclists wordlists or extend "
        "GOBUSTER_WORDLIST_CANDIDATES_KALI."
    )


def suggest_ffuf_wordlist_display(preference: str | None, config_default: str | None) -> str:
    """
    Return a user-facing, non-throwing suggestion string for the wordlist choice.
    """
    try:
        return resolve_ffuf_wordlist(preference, config_default)
    except FileNotFoundError:
        return f"(none found; set {KALI_DEFAULT_SENTINEL} or NEMESIS_DEFAULT_FFUF_WORDLIST)"
