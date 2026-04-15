# Plan 4 — Searchsploit Executor (Auto CVE → Exploit Lookup)

## Objetivo

Integrar o `searchsploit` (interface CLI do Exploit-DB) ao NEMESIS. Quando o `AnalystAgent`
extrai findings com CVE IDs, o `SearchsploitExecutor` é acionado automaticamente para buscar
exploits disponíveis — transformando "CVE encontrado" em "exploit identificado" sem intervenção
manual.

---

## Contexto da Codebase

### Arquivos relevantes

```
nemesis/
  agents/
    executor.py          ← adicionar SearchsploitExecutor + registrar
    analyst.py           ← chamar searchsploit quando findings têm CVEs
    specialized/
      vulnerability.py   ← VulnerabilityAgent (ver como está)
      __init__.py        ← não precisa mudar (searchsploit não é um agent autônomo)
  db/models.py           ← Finding tem campo cve_ids: list[str]
```

### Como o `AnalystAgent` funciona hoje (`agents/analyst.py`)

O `AnalystAgent.process()` recebe um `ExecutorResult` e retorna `list[Finding]`. Cada `Finding`
pode ter `cve_ids: list[str]`.

Hoje, após extrair os findings, não há nenhuma etapa de enriquecimento. O ponto certo para
adicionar o searchsploit é **após** a extração dos candidatos e **antes** de retornar.

### Estrutura de `Finding` (`db/models.py`)

```python
class Finding(BaseModel):
    cve_ids: list[str] = Field(default_factory=list)
    remediation: str = ""
    description: str = ""
    # ... outros campos
```

### Saída esperada do `searchsploit --json <cve>`

```json
{
  "RESULTS_EXPLOIT": [
    {
      "Title": "OpenSSH 7.2p1 - (Authenticated) xauth Command Injection",
      "EDB-ID": "39569",
      "Date": "2016-03-11",
      "Type": "remote",
      "Platform": "linux",
      "Path": "/usr/share/exploitdb/exploits/linux/remote/39569.py"
    }
  ],
  "RESULTS_SHELLCODE": []
}
```

---

## Implementação

### Passo 1 — `SearchsploitExecutor` em `nemesis/agents/executor.py`

Adicionar após `AmassExecutor`:

```python
class SearchsploitExecutor(BaseExecutor):
    """Runs searchsploit to find known exploits for a CVE or keyword."""

    TOOL_NAME = "searchsploit"
    TOOL_BINARY = "searchsploit"
    DESTRUCTIVE = False

    def _build_command(self, binary: str) -> list[str]:
        # target aqui é o termo de busca (CVE ID ou keyword)
        # --json para output estruturado, --disable-colour para parsing limpo
        return [binary, "--json", "--disable-colour", self.target, *self.extra_args]
```

Registrar no `EXECUTOR_REGISTRY`:
```python
"searchsploit": SearchsploitExecutor,
```

**Nota:** O `target` do `SearchsploitExecutor` é usado como termo de busca, não como host de
rede. A validação de scope no `BaseSpecializedAgent` não se aplica aqui porque o searchsploit
não faz conexão com o alvo — é uma busca local na base do Exploit-DB. Por isso, o searchsploit
**não é usado como tool de um SpecializedAgent**, mas sim chamado internamente pelo `AnalystAgent`.

### Passo 2 — Função de enriquecimento em `nemesis/agents/analyst.py`

Adicionar import no topo:
```python
import json
```

Adicionar constante:
```python
_SEARCHSPLOIT_CAP = 5  # max exploits por CVE para não poluir o finding
```

Adicionar método `_enrich_with_exploits` na classe `AnalystAgent`:

```python
async def _enrich_with_exploits(self, findings: list[Finding]) -> None:
    """
    For each finding that has CVE IDs, query searchsploit locally and
    append any found exploit references to the finding's description and remediation.

    Mutates findings in-place. Safe to call even if searchsploit is not installed
    (ToolNotFoundError is silently ignored).
    """
    import shutil
    if not shutil.which("searchsploit"):
        logger.debug(
            "searchsploit not found on PATH — skipping exploit enrichment",
            extra={"event": "analyst.searchsploit_not_found"},
        )
        return

    for finding in findings:
        if not finding.cve_ids:
            continue

        exploit_refs: list[str] = []
        for cve in finding.cve_ids[:3]:  # limit to first 3 CVEs per finding
            try:
                executor = get_executor("searchsploit", "enrich", cve)
                result = await executor.run()
            except (ToolNotFoundError, ValueError):
                continue

            if not result.stdout.strip():
                continue

            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                continue

            exploits = data.get("RESULTS_EXPLOIT", [])
            for exp in exploits[:_SEARCHSPLOIT_CAP]:
                title = exp.get("Title", "")
                edb_id = exp.get("EDB-ID", "")
                exp_type = exp.get("Type", "")
                if title and edb_id:
                    exploit_refs.append(
                        f"EDB-{edb_id} [{exp_type}]: {title}"
                    )

        if exploit_refs:
            refs_text = "\n".join(f"  • {r}" for r in exploit_refs)
            finding.description += f"\n\nKnown exploits:\n{refs_text}"
            finding.remediation = (
                f"Patch immediately — public exploits exist. {finding.remediation}"
                if finding.remediation
                else "Patch immediately — public exploits exist."
            )
            logger.info(
                "Finding enriched with exploit references",
                extra={
                    "event": "analyst.exploit_refs_added",
                    "finding_title": finding.title,
                    "exploit_count": len(exploit_refs),
                    "cve_ids": finding.cve_ids,
                },
            )
```

**Importante:** O `get_executor` precisa ser importado. Ele já é importado no arquivo como:
```python
from nemesis.agents.executor import ExecutorResult
```
Atualizar para incluir `get_executor` e `ToolNotFoundError`:
```python
from nemesis.agents.executor import ExecutorResult, ToolNotFoundError, get_executor
```

### Passo 3 — Chamar `_enrich_with_exploits` no pipeline do `AnalystAgent`

No método `process()` do `AnalystAgent`, adicionar a chamada de enriquecimento **após** construir
a lista de findings e **antes** do `return`:

```python
async def process(self, result: ExecutorResult) -> list[Finding]:
    # ... código existente até aqui ...

    findings: list[Finding] = []
    dismissed = 0
    for candidate in candidates:
        confidence = float(candidate.get("confidence", 0.5))
        if confidence < _AUTO_DISMISS_BELOW:
            dismissed += 1
            # ... log existente ...
            continue

        finding = self._build_finding(candidate, result)
        self._correlate(finding)
        findings.append(finding)

    # ← ADICIONAR AQUI: enriquecer com exploits conhecidos
    await self._enrich_with_exploits(findings)

    logger.info(
        "Analyst findings extracted",
        # ... log existente ...
    )
    return findings
```

---

## Comportamento esperado

**Antes do plan4:**
```
[HIGH] OpenSSH 7.2p1 open — 192.168.1.10:22
  CVEs: CVE-2016-0777
  Remediation: Update to latest version.
```

**Após o plan4:**
```
[HIGH] OpenSSH 7.2p1 open — 192.168.1.10:22
  CVEs: CVE-2016-0777
  Description: ...
    Known exploits:
      • EDB-39569 [remote]: OpenSSH 7.2p1 - (Authenticated) xauth Command Injection
      • EDB-40136 [remote]: OpenSSH 7.2p1 - Username Enumeration
  Remediation: Patch immediately — public exploits exist. Update to latest version.
```

---

## Validação

1. `uv run ruff check nemesis/agents/executor.py nemesis/agents/analyst.py`
2. `uv run ruff format` nos arquivos alterados
3. Verificar que `get_executor("searchsploit", "test", "CVE-2021-44228")` não levanta `ValueError`
4. Se `searchsploit` instalado: `searchsploit --json --disable-colour CVE-2021-44228` deve
   retornar JSON válido com resultados do Log4Shell
5. Verificar que se `searchsploit` não está no PATH, o `process()` continua funcionando
   normalmente (sem crash)

## Dependências do sistema

- `searchsploit` faz parte do `exploitdb` package
- Instalar: `sudo apt install exploitdb` ou `brew install exploitdb`
- Atualizar base: `searchsploit -u`
