# Plan 1 — Nuclei Executor + NucleiAgent

## Objetivo

Integrar o `nuclei` ao NEMESIS como uma ferramenta de primeira classe: um executor que roda
templates de vulnerabilidades e um specialized agent dedicado a ele. Isso adiciona detecção
automática de CVEs reais, misconfigurations e exposições via os 10.000+ templates do
ProjectDiscovery.

---

## Contexto da Codebase

### Estrutura relevante

```
nemesis/
  agents/
    executor.py          ← adicionar NucleiExecutor aqui
    specialized/
      base.py            ← BaseSpecializedAgent (herdar daqui)
      recon.py           ← exemplo de especializado para copiar
      __init__.py        ← registrar NucleiAgent no AGENT_REGISTRY
  agents/planner.py      ← adicionar "nuclei" nos required_tools permitidos
```

### Como executors funcionam (`agents/executor.py`)

Cada executor é uma subclasse de `BaseExecutor` com:

- `TOOL_NAME: str` — nome lógico (usado no registry e nos findings)
- `TOOL_BINARY: str` — binário no PATH
- `DESTRUCTIVE: bool` — se requer confirmação do usuário
- `_build_command(binary: str) -> list[str]` — monta os args do subprocess

O factory `get_executor(tool, task_id, target, extra_args)` olha o `EXECUTOR_REGISTRY` (dict no
final do arquivo) e retorna a instância certa.

O executor usa `asyncio.create_subprocess_exec` com `shell=False`. Output é capturado via
`run_streaming()` que emite linha-a-linha para a TUI via callback `on_line`.

### Como specialized agents funcionam (`agents/specialized/base.py`)

Cada specialized agent herda de `BaseSpecializedAgent` e define:

- `AGENT_NAME: str` — chave no `AGENT_REGISTRY`
- `SYSTEM_PROMPT: str` — persona LLM para este agente
- `ALLOWED_TOOLS: list[str]` — ferramentas permitidas para o LLM escolher
- `_fallback_action(step, target) -> AgentResponse` — ação padrão se LLM falhar

O método `execute(step)` já está implementado na base:

1. Pede ao LLM qual tool + args usar (dentro de `step.required_tools`)
2. Valida scope
3. Roda o executor
4. Passa output pelo `AnalystAgent`
5. Adiciona findings ao `ProjectContext`
6. Retorna `AgentResponse`

### Como o PlannerAgent usa ferramentas (`agents/planner.py`)

O system prompt do PlannerAgent tem uma linha que lista as ferramentas permitidas:

```
- required_tools must be a subset of: ["nmap", "whois", "dig", "gobuster", "nikto"].
```

E os agentes permitidos:

```
- agent must be one of: "recon_agent", "scanning_agent", "enumeration_agent", "vulnerability_agent".
```

Ambas as listas precisam ser atualizadas.

---

## Implementação

### Passo 1 — `NucleiExecutor` em `nemesis/agents/executor.py`

Adicionar a classe após `AmassExecutor` e registrar no `EXECUTOR_REGISTRY`:

```python
class NucleiExecutor(BaseExecutor):
    """Runs nuclei template-based vulnerability scanner."""

    TOOL_NAME = "nuclei"
    TOOL_BINARY = "nuclei"
    DESTRUCTIVE = False

    # Severity levels to include (can be overridden via extra_args)
    DEFAULT_SEVERITY = "medium,high,critical"

    def _build_command(self, binary: str) -> list[str]:
        # -u target, -severity filter, -silent (machine-readable), -json output
        cmd = [
            binary,
            "-u", self.target,
            "-severity", self.DEFAULT_SEVERITY,
            "-silent",
            "-no-color",
        ]
        return cmd + self.extra_args
```

No `EXECUTOR_REGISTRY`, adicionar:

```python
"nuclei": NucleiExecutor,
```

### Passo 2 — `NucleiAgent` em `nemesis/agents/specialized/nuclei.py`

Criar arquivo novo:

```python
"""NucleiAgent — template-based CVE and misconfiguration scanner."""

from __future__ import annotations

from nemesis.agents.specialized.base import BaseSpecializedAgent
from nemesis.db.models import AgentResponse, PlanStep

_NUCLEI_SYSTEM = """\
You are a vulnerability assessment specialist agent within NEMESIS, an authorized \
penetration testing platform.
Your job: run template-based vulnerability scans to detect CVEs, misconfigurations, \
exposed panels, and known exploits using nuclei.
Focus on medium, high, and critical severity templates.
You MUST only use tools from the allowed list provided to you.
Always respond with valid JSON only — no markdown, no explanation outside the JSON.\
"""

ALLOWED_TOOLS: list[str] = ["nuclei"]


class NucleiAgent(BaseSpecializedAgent):
    """
    Template-based vulnerability scanner agent.

    Uses nuclei to run thousands of CVE and misconfiguration templates
    against the target and extract structured findings.
    """

    AGENT_NAME = "nuclei_agent"
    SYSTEM_PROMPT = _NUCLEI_SYSTEM
    ALLOWED_TOOLS = ALLOWED_TOOLS

    def _fallback_action(self, step: PlanStep, target: str) -> AgentResponse:
        return AgentResponse(
            thought=f"LLM unavailable — running default nuclei scan on {target}",
            action="run_tool",
            tool="nuclei",
            args={},
            result="",
            next_step=None,
        )
```

### Passo 3 — Registrar `NucleiAgent` em `nemesis/agents/specialized/__init__.py`

Importar `NucleiAgent` e adicionar ao `AGENT_REGISTRY`:

```python
from nemesis.agents.specialized.nuclei import NucleiAgent

AGENT_REGISTRY: dict[str, type[BaseSpecializedAgent]] = {
    "recon_agent": ReconAgent,
    "scanning_agent": ScanningAgent,
    "enumeration_agent": EnumerationAgent,
    "vulnerability_agent": VulnerabilityAgent,
    "nuclei_agent": NucleiAgent,          # ← adicionar
}
```

Adicionar `NucleiAgent` ao `__all__` também.

### Passo 4 — Atualizar `PlannerAgent` em `nemesis/agents/planner.py`

No `_SYSTEM_PROMPT`, atualizar as duas linhas de constraint:

```
- required_tools must be a subset of: ["nmap", "whois", "dig", "gobuster", "nikto", "nuclei"].
- agent must be one of: "recon_agent", "scanning_agent", "enumeration_agent", "vulnerability_agent", "nuclei_agent".
```

### Passo 5 — Melhorar o `AnalystAgent` para output do nuclei (`nemesis/agents/analyst.py`)

O nuclei pode rodar com `-json` para saída estruturada, mas como estamos usando `-silent` sem
`-json`, o output é texto. Adicionar um regex fallback para nuclei no `_regex_fallback`:

```python
def _regex_fallback(result: ExecutorResult) -> list[dict[str, object]]:
    if result.tool == "nmap":
        return _parse_nmap_ports(result.stdout)
    if result.tool == "amass":
        return _parse_amass_output(result.stdout)
    if result.tool == "nuclei":
        return _parse_nuclei_output(result.stdout)  # ← adicionar
    return []
```

Adicionar a função `_parse_nuclei_output`:

```python
# Nuclei output format: [severity] [template-id] [matched-url]
_NUCLEI_LINE_RE = re.compile(
    r"\[(?P<severity>critical|high|medium|low|info)\]\s+"
    r"\[(?P<template>[^\]]+)\]\s+"
    r"(?P<url>\S+)",
    re.IGNORECASE,
)

def _parse_nuclei_output(stdout: str) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for match in _NUCLEI_LINE_RE.finditer(stdout):
        severity = match.group("severity").lower()
        template = match.group("template")
        url = match.group("url")

        # Confidence based on severity
        confidence_map = {"critical": 0.9, "high": 0.8, "medium": 0.7, "low": 0.55, "info": 0.5}
        confidence = confidence_map.get(severity, 0.6)

        candidates.append({
            "title": f"Nuclei: {template}",
            "description": f"nuclei template '{template}' matched on {url}.",
            "severity": severity,
            "confidence": confidence,
            "port": "",
            "service": "http",
            "cve_ids": [template] if template.upper().startswith("CVE-") else [],
            "remediation": f"Review and remediate '{template}' finding on {url}.",
        })
    return candidates
```

---

## Validação

Após implementar, verificar:

1. `uv run ruff check nemesis/agents/executor.py nemesis/agents/specialized/nuclei.py nemesis/agents/specialized/__init__.py nemesis/agents/analyst.py nemesis/agents/planner.py`
2. `uv run ruff format` nos arquivos alterados
3. Verificar que `get_executor("nuclei", ...)` não levanta `ValueError`
4. Verificar que `get_agent("nuclei_agent")` não levanta `ValueError`
5. Se `nuclei` estiver instalado: `nuclei -u https://example.com -severity medium,high,critical -silent -no-color` deve rodar sem erros

## Dependências do sistema

- `nuclei` deve estar instalado: `go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest`
- Ou via package manager: `brew install nuclei`
- Templates: `nuclei -update-templates` para baixar o catálogo completo

