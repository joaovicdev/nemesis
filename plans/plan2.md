# Plan 2 — Expor LLM Config (Multi-Model Support)

## Objetivo

Permitir que o usuário configure o modelo LLM e a URL base via variáveis de ambiente e/ou
arquivo de configuração. O `LLMClient` já usa LiteLLM que suporta GPT-4o, Claude, Gemini,
Ollama e qualquer provider OpenAI-compatible — basta expor a configuração.

---

## Contexto da Codebase

### Arquivos relevantes

```
nemesis/
  agents/
    llm_client.py        ← LLMConfig, LLMClient (modificar aqui)
  core/
    config.py            ← verificar se existe config de app (pode ser vazio)
  tui/
    app.py               ← NemesisApp cria LLMClient() sem args (modificar aqui)
    screens/
      main.py            ← StatusBar já mostra o modelo ("ollama / llama3.1:8b")
  tui/widgets/
    status_bar.py        ← atualizar para mostrar o modelo real configurado
```

### Como o `LLMClient` funciona hoje (`agents/llm_client.py`)

```python
_DEFAULT_MODEL = "ollama/llama3.1:8b"
_DEFAULT_BASE_URL = "http://localhost:11434"

@dataclass
class LLMConfig:
    model: str = _DEFAULT_MODEL
    base_url: str = _DEFAULT_BASE_URL
    temperature: float = 0.3
    max_tokens: int = 2048
    timeout: int = 60
    extra_headers: dict[str, str] = field(default_factory=dict)

class LLMClient:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self._config = config or LLMConfig()
```

### Como o `NemesisApp` instancia o cliente (`tui/app.py`)

```python
class NemesisApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.db: Database = Database()
        self.llm_client: LLMClient = LLMClient()   # ← sem config, usa defaults
```

### Como a StatusBar mostra o modelo (`tui/screens/main.py`)

```python
def on_mount(self) -> None:
    status = self.query_one("#status-bar", StatusBar)
    status.update_model("ollama / llama3.1:8b")   # ← hardcoded!
```

---

## Variáveis de Ambiente a Suportar


| Variável              | Default                  | Exemplo                                                                       |
| --------------------- | ------------------------ | ----------------------------------------------------------------------------- |
| `NEMESIS_MODEL`       | `ollama/llama3.1:8b`     | `openai/gpt-4o`, `anthropic/claude-3-5-sonnet-20241022`, `ollama/qwen2.5:72b` |
| `NEMESIS_BASE_URL`    | `http://localhost:11434` | `https://api.openai.com/v1`                                                   |
| `NEMESIS_API_KEY`     | `""`                     | `sk-...` (para providers que exigem)                                          |
| `NEMESIS_TEMPERATURE` | `0.3`                    | `0.1`                                                                         |
| `NEMESIS_MAX_TOKENS`  | `2048`                   | `4096`                                                                        |
| `NEMESIS_TIMEOUT`     | `60`                     | `120`                                                                         |


O LiteLLM já lida com `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` etc. como env vars padrão.
A `NEMESIS_API_KEY` é usada via `extra_headers` como fallback genérico quando necessário.

---

## Implementação

### Passo 1 — Função `load_llm_config_from_env` em `nemesis/agents/llm_client.py`

Adicionar após a definição de `LLMConfig`, antes de `LLMError`:

```python
import os

def load_llm_config_from_env() -> LLMConfig:
    """
    Build an LLMConfig from environment variables, falling back to defaults.

    Supported variables:
        NEMESIS_MODEL       — LiteLLM model string (e.g. "ollama/llama3.1:8b")
        NEMESIS_BASE_URL    — API base URL (for local or self-hosted providers)
        NEMESIS_API_KEY     — API key passed as Bearer token in extra_headers
        NEMESIS_TEMPERATURE — float 0.0–1.0
        NEMESIS_MAX_TOKENS  — int
        NEMESIS_TIMEOUT     — int seconds
    """
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
```

Também adicionar ao `LLMClient` um property para expor o model name (para a StatusBar):

```python
class LLMClient:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self._config = config or LLMConfig()

    @property
    def model_name(self) -> str:
        """Human-readable model identifier for display in UI."""
        return self._config.model
```

### Passo 2 — Atualizar `NemesisApp` para usar a config do ambiente (`tui/app.py`)

```python
from nemesis.agents.llm_client import LLMClient, load_llm_config_from_env

class NemesisApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.db: Database = Database()
        self.llm_client: LLMClient = LLMClient(load_llm_config_from_env())
```

### Passo 3 — Atualizar `MainScreen` para mostrar o modelo real (`tui/screens/main.py`)

No método `on_mount`, substituir o hardcoded:

```python
# Antes:
status.update_model("ollama / llama3.1:8b")

# Depois:
llm_client: LLMClient = self.app.llm_client  # type: ignore[attr-defined]
status.update_model(llm_client.model_name)
```

Adicionar o import no topo do arquivo:

```python
from nemesis.agents.llm_client import LLMClient
```

### Passo 4 — Logar a configuração carregada em `nemesis/main.py`

No `run()`, após `_configure_logging()` e antes de lançar o app, logar o modelo que será usado:

```python
def run() -> None:
    _configure_logging()
    _log_llm_config()
    try:
        from nemesis.tui.app import NemesisApp
        app = NemesisApp()
        app.run()
    except KeyboardInterrupt:
        sys.exit(0)

def _log_llm_config() -> None:
    """Log which LLM model will be used so the user knows at startup."""
    import logging
    from nemesis.agents.llm_client import load_llm_config_from_env
    cfg = load_llm_config_from_env()
    logging.getLogger(__name__).info(
        "LLM config loaded",
        extra={
            "event": "app.llm_config_loaded",
            "model": cfg.model,
            "base_url": cfg.base_url,
            "timeout": cfg.timeout,
        },
    )
```

---

## Uso pelo operador

### Ollama local com modelo maior

```bash
NEMESIS_MODEL=ollama/qwen2.5:72b nemesis
```

### OpenAI GPT-4o

```bash
NEMESIS_MODEL=openai/gpt-4o \
NEMESIS_BASE_URL=https://api.openai.com/v1 \
OPENAI_API_KEY=sk-... \
nemesis
```

### Anthropic Claude

```bash
NEMESIS_MODEL=anthropic/claude-3-5-sonnet-20241022 \
ANTHROPIC_API_KEY=sk-ant-... \
nemesis
```

### Provider OpenAI-compatible (LM Studio, vLLM, etc.)

```bash
NEMESIS_MODEL=openai/mistral-nemo \
NEMESIS_BASE_URL=http://localhost:1234/v1 \
nemesis
```

---

## Validação

1. `uv run ruff check nemesis/agents/llm_client.py nemesis/tui/app.py nemesis/tui/screens/main.py nemesis/main.py`
2. `uv run ruff format` nos arquivos alterados
3. Verificar que `load_llm_config_from_env()` retorna defaults quando nenhuma env var está definida
4. Verificar que `NEMESIS_MODEL=ollama/llama3.1:8b nemesis` ainda funciona normalmente
5. Verificar que a StatusBar mostra o model name real (não mais hardcoded)

