# Plan 3 — CIDR Scope Validation

## Objetivo

Corrigir a validação de escopo para suportar ranges de IP no formato CIDR (ex: `192.168.1.0/24`,
`10.0.0.0/8`). Hoje, qualquer IP individual é rejeitado como out-of-scope quando o projeto define
um range, tornando pentest de redes internas completamente inutilizável.

---

## Contexto da Codebase

### Arquivo principal a modificar

```
nemesis/core/project.py   ← ProjectContext.is_in_scope()
```

### Comportamento atual (`core/project.py`)

```python
def is_in_scope(self, target: str) -> bool:
    target = target.strip().lower()

    # Bloco out-of-scope: exact match + subdomain suffix
    for oos in self.project.out_of_scope:
        oos = oos.strip().lower()
        if target == oos or target.endswith(f".{oos}"):
            return False

    # Bloco in-scope: exact match + subdomain suffix APENAS
    for scope_target in self.project.targets:
        scope_target = scope_target.strip().lower()
        if target == scope_target:
            return True
        if target.endswith(f".{scope_target}"):
            return True

    return False
```

**Problema:** Se `project.targets = ["192.168.1.0/24"]` e o executor tenta rodar contra
`192.168.1.10`, a função retorna `False` porque `"192.168.1.10"` não é igual a
`"192.168.1.0/24"` nem termina com `.192.168.1.0/24`.

### Modelo de dados (`db/models.py`)

```python
class Project(BaseModel):
    targets: list[str]        # pode conter IPs, CIDRs, hostnames, domínios
    out_of_scope: list[str]   # mesma coisa
```

A codebase não impõe tipo nos targets — podem ser misturados. Ex:

```python
targets = ["192.168.1.0/24", "target.corp.local", "10.10.10.5"]
out_of_scope = ["192.168.1.1", "10.10.10.0/28"]
```

---

## Implementação

### Passo 1 — Adicionar import de `ipaddress` em `nemesis/core/project.py`

O módulo `ipaddress` é stdlib do Python 3 — sem dependências externas.

No bloco de imports do arquivo, adicionar:

```python
import ipaddress
```

### Passo 2 — Criar funções auxiliares de matching

Adicionar as funções abaixo no módulo, antes da classe `ProjectContext`:

```python
def _target_matches(candidate: str, scope_entry: str) -> bool:
    """
    Check whether *candidate* falls within *scope_entry*.

    Handles four cases:
      1. Exact string match (hostname, IP, domain)
      2. Subdomain suffix match  (*.example.com)
      3. IP address within a CIDR range
      4. CIDR range within another CIDR range (subset)
    """
    candidate = candidate.strip().lower()
    scope_entry = scope_entry.strip().lower()

    # Case 1: exact match
    if candidate == scope_entry:
        return True

    # Case 2: subdomain suffix
    if candidate.endswith(f".{scope_entry}"):
        return True

    # Cases 3 & 4: CIDR / IP matching
    try:
        scope_net = ipaddress.ip_network(scope_entry, strict=False)
    except ValueError:
        # scope_entry is not a valid IP/CIDR — already handled by cases 1 & 2
        return False

    try:
        # Case 3: candidate is a plain IP address
        candidate_addr = ipaddress.ip_address(candidate)
        return candidate_addr in scope_net
    except ValueError:
        pass

    try:
        # Case 4: candidate is itself a CIDR — check if it's a subnet
        candidate_net = ipaddress.ip_network(candidate, strict=False)
        return candidate_net.subnet_of(scope_net)
    except (ValueError, TypeError):
        pass

    return False
```

### Passo 3 — Reescrever `is_in_scope` para usar a helper

```python
def is_in_scope(self, target: str) -> bool:
    """
    Check whether a target string is within the project scope.

    Supports:
      - Exact hostname / IP match
      - Subdomain suffix match
      - IP address within a CIDR range (e.g. 192.168.1.5 in 192.168.1.0/24)
      - CIDR subnet within a CIDR range

    Out-of-scope entries are checked first and take priority over in-scope.
    """
    target = target.strip().lower()

    for oos in self.project.out_of_scope:
        if _target_matches(target, oos):
            logger.debug(
                "Scope check: out of scope",
                extra={
                    "event": "project.scope_checked",
                    "result": "out_of_scope",
                    "target": target,
                    "matched_oos": oos,
                    "project_id": self.project.id,
                },
            )
            return False

    for scope_entry in self.project.targets:
        if _target_matches(target, scope_entry):
            logger.debug(
                "Scope check: in scope",
                extra={
                    "event": "project.scope_checked",
                    "result": "in_scope",
                    "target": target,
                    "matched_entry": scope_entry,
                    "project_id": self.project.id,
                },
            )
            return True

    logger.debug(
        "Scope check: not in targets",
        extra={
            "event": "project.scope_checked",
            "result": "out_of_scope",
            "target": target,
            "project_id": self.project.id,
        },
    )
    return False
```

### Passo 4 — Atualizar `assert_in_scope` para mensagem mais rica

```python
def assert_in_scope(self, target: str) -> None:
    """Raise ValueError if target is out of scope."""
    if not self.is_in_scope(target):
        raise ValueError(
            f"Target '{target}' is outside the project scope. "
            f"Configured targets: {self.project.targets}. "
            f"Out-of-scope: {self.project.out_of_scope}."
        )
```

---

## Testes a escrever (`tests/`)

Criar `tests/test_scope_validation.py` com os seguintes cenários:

```python
"""Tests for ProjectContext scope validation."""
from __future__ import annotations

import pytest
from nemesis.core.project import ProjectContext, _target_matches
from nemesis.db.models import Project, Session


def _make_ctx(targets: list[str], out_of_scope: list[str] | None = None) -> ProjectContext:
    project = Project(name="test", targets=targets, out_of_scope=out_of_scope or [])
    session = Session(project_id=project.id)
    return ProjectContext(project=project, session=session)


# _target_matches unit tests
@pytest.mark.parametrize("candidate,scope,expected", [
    # exact match
    ("192.168.1.1", "192.168.1.1", True),
    ("example.com", "example.com", True),
    # subdomain
    ("sub.example.com", "example.com", True),
    ("other.com", "example.com", False),
    # IP in CIDR
    ("192.168.1.10", "192.168.1.0/24", True),
    ("192.168.2.1", "192.168.1.0/24", False),
    ("10.0.0.1", "10.0.0.0/8", True),
    # CIDR subnet of CIDR
    ("192.168.1.0/28", "192.168.1.0/24", True),
    ("192.168.2.0/24", "192.168.1.0/24", False),
    # edge cases
    ("192.168.1.0/24", "192.168.1.0/24", True),  # exact CIDR match
])
def test_target_matches(candidate: str, scope: str, expected: bool) -> None:
    assert _target_matches(candidate, scope) == expected


# is_in_scope integration tests
def test_ip_in_cidr_scope() -> None:
    ctx = _make_ctx(["192.168.1.0/24"])
    assert ctx.is_in_scope("192.168.1.50") is True
    assert ctx.is_in_scope("192.168.2.1") is False


def test_out_of_scope_takes_priority() -> None:
    ctx = _make_ctx(["192.168.1.0/24"], out_of_scope=["192.168.1.1"])
    assert ctx.is_in_scope("192.168.1.1") is False
    assert ctx.is_in_scope("192.168.1.2") is True


def test_mixed_targets() -> None:
    ctx = _make_ctx(["192.168.1.0/24", "target.corp.local"])
    assert ctx.is_in_scope("192.168.1.100") is True
    assert ctx.is_in_scope("sub.target.corp.local") is True
    assert ctx.is_in_scope("other.com") is False


def test_cidr_out_of_scope() -> None:
    ctx = _make_ctx(["10.0.0.0/8"], out_of_scope=["10.0.0.0/24"])
    assert ctx.is_in_scope("10.0.0.5") is False    # inside excluded subnet
    assert ctx.is_in_scope("10.1.0.5") is True     # outside excluded subnet
```

---

## Validação

1. `uv run ruff check nemesis/core/project.py`
2. `uv run ruff format nemesis/core/project.py`
3. `uv run pytest tests/test_scope_validation.py -v` — todos devem passar
4. Verificar que nenhum teste existente quebrou: `uv run pytest tests/ -v`

