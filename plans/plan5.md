# Plan 5 — ffuf Executor + FfufAgent (substituir gobuster)

## Objetivo

Adicionar `ffuf` como executor e specialized agent de web fuzzing. O `ffuf` é significativamente
mais rápido que o `gobuster`, suporta fuzzing de headers/parâmetros/virtual hosts além de
diretórios, e tem output JSON nativo que facilita extração de findings. O `gobuster` deve
permanecer funcional (não remover), mas o `PlannerAgent` deve preferir `ffuf` por padrão.

---

## Contexto da Codebase

### Arquivos relevantes

```
nemesis/
  agents/
    executor.py                  ← adicionar FfufExecutor
    analyst.py                   ← adicionar regex fallback para ffuf
    specialized/
      enumeration.py             ← EnumerationAgent atual (usa gobuster/nikto)
      ffuf.py                    ← CRIAR: FfufAgent
      __init__.py                ← registrar FfufAgent
  agents/planner.py              ← adicionar ffuf nas ferramentas e ffuf_agent nos agentes
```

### `EnumerationAgent` atual (`agents/specialized/enumeration.py`)

Ver conteúdo real do arquivo antes de modificar. Provavelmente usa `gobuster` e `nikto` como
`ALLOWED_TOOLS`. O `FfufAgent` será um agente separado — não substituir o `EnumerationAgent`.

### `GobusterExecutor` atual (`agents/executor.py`)

```python
class GobusterExecutor(BaseExecutor):
    TOOL_NAME = "gobuster"
    TOOL_BINARY = "gobuster"
    DEFAULT_WORDLIST = "/usr/share/wordlists/dirb/common.txt"

    def _build_command(self, binary: str) -> list[str]:
        wordlist = next((a for a in self.extra_args if a.startswith("-w")), None)
        base = [binary, "dir", "-u", self.target, "-q", "--no-progress"]
        if not wordlist:
            base += ["-w", self.DEFAULT_WORDLIST]
        return base + self.extra_args
```

---

## Implementação

### Passo 1 — `FfufExecutor` em `nemesis/agents/executor.py`

Adicionar após `NiktoExecutor`:

```python
class FfufExecutor(BaseExecutor):
    """Runs ffuf for fast web content discovery and fuzzing."""

    TOOL_NAME = "ffuf"
    TOOL_BINARY = "ffuf"
    DESTRUCTIVE = False

    # Default wordlists in order of preference (first one that exists is used)
    _WORDLIST_CANDIDATES: list[str] = [
        "/usr/share/seclists/Discovery/Web-Content/raft-medium-directories.txt",
        "/usr/share/seclists/Discovery/Web-Content/common.txt",
        "/usr/share/wordlists/dirb/common.txt",
        "/usr/share/wordlists/dirbuster/directory-list-2.3-small.txt",
    ]

    def _resolve_wordlist(self) -> str:
        """Return the first wordlist that exists on disk."""
        import os
        for path in self._WORDLIST_CANDIDATES:
            if os.path.exists(path):
                return path
        # Last resort: return the last candidate and let ffuf fail with a clear error
        return self._WORDLIST_CANDIDATES[-1]

    def _build_command(self, binary: str) -> list[str]:
        # Check if a wordlist was explicitly passed via extra_args
        has_wordlist = any(a == "-w" for a in self.extra_args)

        # -u URL/FUZZ, -w wordlist, -mc all (match all status codes for filtering),
        # -ac (auto-calibrate to filter false positives), -of json (structured output),
        # -o /dev/stdout to stream JSON
        # -s silent mode (no banner), -v verbose matches
        target_url = self.target
        if not target_url.startswith(("http://", "https://")):
            target_url = f"http://{target_url}"

        cmd = [
            binary,
            "-u", f"{target_url}/FUZZ",
            "-mc", "all",
            "-ac",
            "-of", "json",
            "-o", "/dev/stdout",
            "-s",
        ]

        if not has_wordlist:
            cmd += ["-w", self._resolve_wordlist()]

        return cmd + self.extra_args
```

Registrar no `EXECUTOR_REGISTRY`:

```python
"ffuf": FfufExecutor,
```

### Passo 2 — `FfufAgent` em `nemesis/agents/specialized/ffuf.py`

Criar arquivo novo:

```python
"""FfufAgent — fast web content discovery and fuzzing specialist."""

from __future__ import annotations

from nemesis.agents.specialized.base import BaseSpecializedAgent
from nemesis.db.models import AgentResponse, PlanStep

_FFUF_SYSTEM = """\
You are a web enumeration specialist agent within NEMESIS, an authorized \
penetration testing platform.
Your job: discover hidden web content, admin panels, API endpoints, and sensitive files \
using ffuf for fast fuzzing.
Focus on finding paths that could expose sensitive functionality or data.
You MUST only use tools from the allowed list provided to you.
Always respond with valid JSON only — no markdown, no explanation outside the JSON.\
"""

ALLOWED_TOOLS: list[str] = ["ffuf"]


class FfufAgent(BaseSpecializedAgent):
    """
    Web fuzzing specialist agent.

    Uses ffuf for fast directory/file discovery. Auto-calibrates to filter
    false positives and outputs structured JSON for reliable parsing.
    """

    AGENT_NAME = "ffuf_agent"
    SYSTEM_PROMPT = _FFUF_SYSTEM
    ALLOWED_TOOLS = ALLOWED_TOOLS

    def _fallback_action(self, step: PlanStep, target: str) -> AgentResponse:
        return AgentResponse(
            thought=f"LLM unavailable — running default ffuf directory scan on {target}",
            action="run_tool",
            tool="ffuf",
            args={},
            result="",
            next_step=None,
        )
```

### Passo 3 — Registrar `FfufAgent` em `nemesis/agents/specialized/__init__.py`

```python
from nemesis.agents.specialized.ffuf import FfufAgent

AGENT_REGISTRY: dict[str, type[BaseSpecializedAgent]] = {
    "recon_agent": ReconAgent,
    "scanning_agent": ScanningAgent,
    "enumeration_agent": EnumerationAgent,
    "vulnerability_agent": VulnerabilityAgent,
    "ffuf_agent": FfufAgent,               # ← adicionar
}
```

Adicionar `FfufAgent` ao `__all__`.

### Passo 4 — Regex fallback para ffuf em `nemesis/agents/analyst.py`

O ffuf com `-of json -o /dev/stdout` produz JSON no stdout. Adicionar parsing específico:

```python
# ffuf JSON output: results array with "url", "status", "length", "words" fields
def _parse_ffuf_output(stdout: str) -> list[dict[str, object]]:
    """Parse ffuf JSON output into finding candidates."""
    candidates: list[dict[str, object]] = []

    # ffuf outputs one JSON object with a "results" array
    try:
        # Find the JSON object in stdout (may have noise before it)
        import json
        import re as _re
        json_match = _re.search(r'\{[\s\S]+\}', stdout)
        if not json_match:
            return []
        data = json.loads(json_match.group())
        results = data.get("results", [])
    except (json.JSONDecodeError, AttributeError):
        return []

    for item in results:
        url = item.get("url", "")
        status = item.get("status", 0)
        length = item.get("length", 0)
        words = item.get("words", 0)

        if not url:
            continue

        # Determine severity by status code and path characteristics
        path = url.split("/")[-1].lower() if "/" in url else url.lower()
        sensitive_keywords = {
            "admin", "login", "wp-admin", "phpmyadmin", "config", "backup",
            "api", ".git", ".env", "console", "manager", "dashboard",
        }
        is_sensitive = any(kw in path for kw in sensitive_keywords)
        severity = "medium" if is_sensitive else "info"
        confidence = 0.75 if is_sensitive else 0.55

        candidates.append({
            "title": f"Web path discovered: {url}",
            "description": (
                f"ffuf found accessible path '{url}' "
                f"(HTTP {status}, {length} bytes, {words} words)."
            ),
            "severity": severity,
            "confidence": confidence,
            "port": "80" if "http://" in url and "https://" not in url else "443",
            "service": "http",
            "cve_ids": [],
            "remediation": (
                f"Review '{url}' for sensitive data exposure or unauthorized access."
                if is_sensitive
                else f"Verify that '{url}' should be publicly accessible."
            ),
        })

    return candidates
```

No `_regex_fallback`:

```python
def _regex_fallback(result: ExecutorResult) -> list[dict[str, object]]:
    if result.tool == "nmap":
        return _parse_nmap_ports(result.stdout)
    if result.tool == "amass":
        return _parse_amass_output(result.stdout)
    if result.tool == "ffuf":
        return _parse_ffuf_output(result.stdout)  # ← adicionar
    return []
```

O `import json` e `import re as _re` dentro da função funcionam, mas é preferível mover
`import json` para o topo do arquivo (já deve existir no `llm_client.py`, verificar se já
foi importado em `analyst.py` pelo plan4 — se sim, não duplicar).

### Passo 5 — Atualizar `PlannerAgent` em `nemesis/agents/planner.py`

No `_SYSTEM_PROMPT`, atualizar as listas:

```
- required_tools must be a subset of: ["nmap", "whois", "dig", "gobuster", "nikto", "ffuf"].
- agent must be one of: "recon_agent", "scanning_agent", "enumeration_agent", "vulnerability_agent", "ffuf_agent".
```

**Nota:** Se o plan1 (nuclei) já foi aplicado, a lista pode ter `"nuclei"` e `"nuclei_agent"` —
adicionar `ffuf` e `ffuf_agent` à lista existente sem remover os outros.

No `_DEFAULT_PLAN_STEPS`, atualizar o step de web enumeration para usar ffuf:

```python
{
    "id": "step-003",
    "name": "Web Directory Fuzzing",
    "description": "Fast discovery of hidden web paths, admin panels, and API endpoints.",
    "required_tools": ["ffuf"],
    "depends_on": ["step-002"],
    "agent": "ffuf_agent",
    "args": {},
},
```

---

## Validação

1. `uv run ruff check nemesis/agents/executor.py nemesis/agents/specialized/ffuf.py nemesis/agents/specialized/__init__.py nemesis/agents/analyst.py nemesis/agents/planner.py`
2. `uv run ruff format` nos arquivos alterados
3. Verificar que `get_executor("ffuf", ...)` não levanta `ValueError`
4. Verificar que `get_agent("ffuf_agent")` não levanta `ValueError`
5. Se `ffuf` instalado: `ffuf -u http://example.com/FUZZ -w /usr/share/wordlists/dirb/common.txt -mc all -ac -of json -o /dev/stdout -s` deve rodar
6. Verificar que `gobuster` ainda funciona (não foi removido)

## Dependências do sistema

- `ffuf`: `go install github.com/ffuf/ffuf/v2@latest` ou `brew install ffuf`
- Wordlists recomendadas: `sudo apt install seclists` ou `brew install seclists`
  - Path no macOS via brew: `/opt/homebrew/share/seclists/`
  - Atualizar `_WORDLIST_CANDIDATES` se necessário para incluir o path do macOS

