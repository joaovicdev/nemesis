# NEMESIS — Roadmap

This document is the single source of truth for where NEMESIS is going and why.
It is written for contributors — not as a marketing document, but as a technical
guide to understand the vision, the design decisions behind each milestone, and
what "done" looks like for each piece of work.

**Core philosophy (read this first):**
NEMESIS is not an autonomous pentesting bot. It is an **AI-assisted co-pilot** —
the pentester drives every engagement, and the AI assists with memory, analysis,
correlation, and execution. Every design decision should be evaluated against this
principle. If a feature removes control from the user, it needs a very good reason
to exist.

---

## Status overview


| Milestone               | Version | Status   |
| ----------------------- | ------- | -------- |
| Foundation              | v0.1    | complete |
| LLM Brain               | v0.2    | next     |
| Tool Execution Pipeline | v0.3    | pending  |
| Analyst Intelligence    | v0.4    | pending  |
| Attack Planning         | v0.5    | pending  |
| Report Generation       | v0.6    | pending  |
| Extended Tooling        | v0.7    | pending  |
| External LLM Providers  | v0.8    | pending  |
| Plugin System           | v0.9    | pending  |
| Stable Release          | v1.0    | pending  |


---

## v0.1 — Foundation

**Status: complete**

### Goal

Establish the project skeleton that all future milestones build on top of. This
milestone is about getting the architecture right — not about features. Every
structural decision made here is load-bearing for the rest of the project.

### What was built

- **TUI** — full Textual-based interface with splash screen, main layout (left
panel + chat), new project wizard, context panel, task list, and status bar.
Cyberpunk aesthetic: black background, cyan neon accent, red for danger.
- **Project model** — a pentest engagement is a first-class `Project` entity with
scope (targets), context (free-text about the client), phase tracking, and
control mode. This is the mental model shift from scan-per-run tools: the whole
engagement lives in one place, persists between sessions, and accumulates context.
- **SQLite persistence** — all projects, sessions, findings, chat history, and
tasks stored locally via `aiosqlite`. No external database dependency. The DB
lives at `~/.nemesis/nemesis.db`.
- **Agent scaffolding** — `Orchestrator`, `Analyst`, and `Executor` defined as
typed async interfaces with full docstrings, ready to be wired to a real LLM.
The interfaces are complete; the implementations are placeholders.
- **Cursor rules** — three `.cursor/rules/` files enforcing architecture layer
separation, Python coding standards, and security constraints across the
codebase.

### Key design decisions (do not change without discussion)

- `tui/` never imports from `agents/` directly. All agent communication goes
through `core/`. This keeps the UI layer testable and decoupled from AI logic.
- The `Finding` model has a mandatory lifecycle: `RAW → UNVERIFIED → VALIDATED (or DISMISSED) → REPORTED`. Nothing skips stages. This enforces the
false-positive pipeline before anything reaches a report.
- `ProjectContext.assert_in_scope()` is the single enforcement point for scope
validation. Every executor must call it before running. There is no other
place where scope is checked.
- LiteLLM was chosen as the AI abstraction layer so that swapping providers
(Ollama → OpenAI → Anthropic) requires changing only a config string, not
agent code. This decision pays off fully in v0.8.

---

## v0.2 — LLM Brain

**Status: next**

### Goal

Wire the `Orchestrator` to a real local LLM via LiteLLM + Ollama. After this
milestone, the user can have a real AI conversation about their engagement —
asking questions, getting analysis, and receiving suggestions — all running
100% locally with no API keys required.

This is the MVP that makes NEMESIS actually useful for the first time.

### Context for contributors

LiteLLM is used as the abstraction layer between NEMESIS and the underlying
model provider. The code in `agents/` calls `litellm.acompletion()` — it does
not call Ollama's HTTP API directly. This is intentional: swapping to a
different provider in v0.8 will require zero changes to agent logic.

The model is configured in `core/config.py` via `NemesisConfig.model`
(default: `llama3.1:8b`). Users can override with `NEMESIS_MODEL=llama3.2:3b`
for lower-RAM machines.

**Context window management is critical.** A full engagement can span dozens of
scans and hundreds of findings. We cannot dump everything into every prompt.
`ProjectContext.build_llm_context_summary()` already exists for this purpose —
it produces a compact structured summary of the project state (targets, phase,
finding counts, critical finding titles, engagement context). Every LLM call
must inject this summary, never raw findings lists.

### Deliverables

- `nemesis/agents/llm_client.py` — async wrapper around
`litellm.acompletion()` with retry logic, timeout handling, and streaming
support. Returns typed `LLMResponse` objects, never raw dicts. This is the
only place in the codebase that calls LiteLLM directly.
- Wire `Orchestrator._llm_response()` to make real LiteLLM calls with:
  - A system prompt defining the NEMESIS persona, role, and constraints
  - Injected `ProjectContext.build_llm_context_summary()` on every call
  - Recent chat history (last N messages, configurable via `NemesisConfig`)
- Streaming responses — the Orchestrator emits partial tokens to the TUI
as they arrive so the user sees the response being typed in real time.
The `ChatPanel` already has a `RichLog` that supports streaming appends.
- Ollama health check on startup — if Ollama is not running or the
configured model is not pulled, show a clear actionable error in the TUI
with the exact fix command, not a Python traceback.
- `nemesis/tui/screens/model_setup.py` — first-run setup screen shown when
Ollama is unreachable, guiding the user through installation and model pull.
- Wire `MainScreen._handle_user_message()` to the `Orchestrator` via an
async task, replacing the current placeholder that returns a static message.
- Update the `StatusBar` model indicator to show the actual connected model
name and a green/red connection status indicator.
- Unit tests: mock LiteLLM in tests so CI does not require a running Ollama.

### Files to modify

- `nemesis/agents/orchestrator.py` — implement `_llm_response()`
- `nemesis/tui/screens/main.py` — wire `_handle_user_message()` to orchestrator
- `nemesis/tui/widgets/status_bar.py` — live model status
- `nemesis/core/config.py` — validate model config on load

### Notes for contributors

- The system prompt defines the AI's behavior. It must establish: role (security
researcher assistant, not an autonomous attacker), constraints (never suggest
illegal actions, always respect the project scope), output format (structured
JSON when returning findings, conversational text otherwise), and the injected
project context block.
- Temperature should be low (0.3–0.4) for analysis and extraction tasks, and
slightly higher (0.5–0.6) for free-form conversational responses. The
`LLMClient` should accept a `temperature` override per call type.
- Always use `litellm.acompletion()` (async). Never use `litellm.completion()`
(sync), as it blocks the event loop and freezes the TUI.

---

## v0.3 — Tool Execution Pipeline

**Status: pending** | Depends on: v0.2

### Goal

Make the executor agents actually run real system tools and return real output.
After this milestone, the user can type "run nmap on 192.168.1.1" and watch the
scan happen in real time inside the TUI — output streaming to the chat panel
and the task list updating live as the tool progresses.

### Context for contributors

`BaseExecutor` in `nemesis/agents/executor.py` already defines the interface
and subprocess scaffolding. The concrete subclasses (`NmapExecutor`,
`GobusterExecutor`, etc.) have `_build_command()` implemented. This milestone
is about wiring them end-to-end into the UI and making the Orchestrator spawn,
track, and cancel them correctly.

**Parallel execution**: the Orchestrator can spawn multiple executors
concurrently using `asyncio.gather()`. For example, nmap and whois on the same
target can run simultaneously. `NemesisConfig.max_parallel_executors` caps this
(default: 3).

**Output streaming**: tool output should appear in the chat panel as it is
produced, not only after the process exits. Use `asyncio.create_subprocess_exec`
with `stdout=PIPE` and read line-by-line with `await proc.stdout.readline()`.

### Deliverables

- Implement real streaming output in `BaseExecutor.run()` — line-by-line
async reads, emitting each line via a callback to the TUI.
- Wire `Orchestrator._execute_tool()` to spawn executors as `asyncio.Task`
objects tracked in `_running_executors`.
- `TaskList` widget updates in real time as executor tasks change status
(pending → running → done / failed).
- Tool output displayed in `ChatPanel` during execution as a collapsible
raw output block that can be expanded/collapsed after the tool finishes.
- `Ctrl+K` keybinding to kill the currently running executor task.
- Scope validation called in `Orchestrator._handle_run_request()` before
any executor is spawned — rejects out-of-scope targets with a clear message.
- Natural language tool request parsing: "run gobuster with medium wordlist
on app.target.com" parsed correctly by the Orchestrator (via LLM).
- Tool availability check on startup: scan which registered tools are
installed and show a warning in the status bar for missing ones.
- Full output saved to `~/.nemesis/projects/<project_id>/evidence/<task_id>.txt`
for audit trail. The DB stores only a 2000-char excerpt.
- Tests for each executor with mocked `asyncio.create_subprocess_exec`.

### Initial tool set (stubs already exist in executor.py)


| Tool     | Phase       | Binary     | Notes                             |
| -------- | ----------- | ---------- | --------------------------------- |
| nmap     | Recon       | `nmap`     | `-sV -sC -T4` by default          |
| whois    | Recon       | `whois`    | Domain + IP registration info     |
| dig      | Recon       | `dig`      | DNS records: A, MX, NS, TXT       |
| gobuster | Enumeration | `gobuster` | `dir` mode, configurable wordlist |
| nikto    | Enumeration | `nikto`    | Web vulnerability scanner         |


### Notes for contributors

- Never use `shell=True` in subprocess calls. Always pass an explicit
`list[str]` to avoid shell injection from user-provided target strings.
This is enforced by the `security.mdc` Cursor rule.
- Tool binaries are resolved through `shutil.which()`. If not found, raise
`ToolNotFoundError` with the `install_hint` from `TOOL_REGISTRY`.
- Raw output is stored in `Finding.raw_evidence` (capped at 2000 chars).
The full output goes to the evidence file. Both are created even if the
Analyst finds no actionable findings — preserve the raw evidence always.

---

## v0.4 — Analyst Intelligence

**Status: pending** | Depends on: v0.3

### Goal

Make the `Analyst` agent actually do its job: take raw tool output, extract
structured findings using the LLM, score confidence, filter noise, and correlate
findings across scans. After this milestone, the user sees only meaningful,
deduplicated findings — not a wall of raw scanner output dumped into the chat.

This is the milestone that most directly addresses the core weaknesses we
identified in similar tools: no false-positive filtering and no memory
between scans.

### Context for contributors

The Analyst sits between every Executor and the Orchestrator. Raw output
**never** reaches the Orchestrator directly — this is enforced in the
`architecture.mdc` Cursor rule and must never be violated. The flow is:

```
Executor → raw output → Analyst → structured Finding (UNVERIFIED) → Orchestrator
```

The Analyst uses the LLM with a low-temperature structured output prompt
(requesting JSON) to extract finding candidates from raw tool output. It then
applies confidence scoring and lifecycle filtering before the Orchestrator ever
sees a finding.

**Confidence scoring** combines LLM assessment and rule-based adjustments:

- nikto frequently flags generic issues on any web server — these receive a
confidence penalty unless independently verified by a second tool.
- A finding below 0.25 confidence is auto-dismissed (`status=DISMISSED`)
and never surfaced to the user unless they explicitly ask.
- A finding between 0.25 and 0.60 is flagged `UNVERIFIED` and the user is
asked "should I verify this?" before it advances in the lifecycle.
- A finding above 0.60 is promoted to `UNVERIFIED` automatically and
presented to the Orchestrator for the user's final decision.

**Correlation** identifies relationships between findings across the project:

- Same target + overlapping CVE IDs → linked findings
- Service version + known CVE for that version → linked to advisory
- Exposed directory path + authentication bypass finding → attack chain candidate

### Deliverables

- Implement `AnalystAgent._extract_candidates()` with a real LiteLLM call
using a structured JSON output prompt. The prompt includes: tool name,
target, and raw output. Returns a Pydantic-validated list of candidate dicts.
- Confidence scoring per finding — LLM assigns 0.0–1.0, Analyst applies
rule-based adjustments per tool type (e.g. nikto gets a penalty, nmap CVE
matches get a bonus).
- Auto-dismiss pipeline: findings below threshold stored as `DISMISSED`
and excluded from default chat output and reports.
- LLM-powered correlation pass: after processing all findings in a session,
a second LLM call with all validated findings identifies attack chains and
cross-finding relationships. Results stored in `Finding.related_finding_ids`.
- Attack path detection: when two or more findings combine into an
exploitable chain, emit an `AttackPath` record with escalated severity.
Example: "Apache 2.4.49 path traversal + exposed `/etc/passwd` = confirmed
LFI → potential RCE."
- Verification requests: for `UNVERIFIED` medium+ confidence findings,
the Analyst emits a confirmation prompt to the Orchestrator ("want me to
verify this with a targeted check?") rather than silently accepting or
dismissing.
- `nemesis/agents/analyst_prompts.py` — separate module for all Analyst
LLM prompt templates. Prompts must not live inside business logic methods.
- Tests: mock LLM responses and verify the scoring + filtering pipeline
behaves correctly for known tool output samples (one fixture file per tool).

### Notes for contributors

- The Analyst LLM call uses a lower temperature than the Orchestrator (0.1–0.2)
because we want consistent, deterministic JSON extraction — not creative prose.
- The JSON output schema for finding extraction must be validated with Pydantic
before constructing a `Finding`. Treat all LLM JSON output as untrusted input.
- Keep the Analyst stateless per call. All project context it needs is passed
in explicitly via `ProjectContext`. This is essential for testability.

---

## v0.5 — Attack Planning

**Status: pending** | Depends on: v0.4

### Goal

Give the Orchestrator the ability to generate a structured, phased attack plan
at the start of an engagement and adapt it as new findings emerge. The three
control modes (Auto, Step, Manual) become fully operational in this milestone.

This is where NEMESIS starts feeling like a real co-pilot: the AI proposes
next steps with justifications, and the pentester decides whether to follow them.

### Context for contributors

The attack plan is a sequence of `AttackTask` objects organized into phases:

```
RECON → ENUMERATION → EXPLOITATION → POST_EXPLOITATION → REPORTING
```

The plan is generated by the Orchestrator immediately after project creation.
The LLM receives the scope, engagement context, and any initial recon results,
and returns a prioritized task list with per-step justifications.

**Control mode behavior:**

- `AUTO` — Orchestrator executes each step immediately after the previous
completes, pausing only for destructive actions. Streams progress updates
to the chat panel continuously.
- `STEP` (default) — after each completed task, the Orchestrator proposes the
next step and waits for explicit user approval. The user can approve, modify
the command, or skip the step entirely.
- `MANUAL` — the Orchestrator does nothing unprompted. It still analyzes all
results when they arrive, but never initiates execution autonomously.

**Plan adaptation**: after each finding, the Orchestrator re-evaluates priority.
If a CRITICAL finding is discovered mid-enumeration, the plan may be reordered
to prioritize exploitation of that vector before continuing the original sequence.

### Deliverables

- `Orchestrator.generate_plan()` — LLM call that takes project scope +
context and returns a structured `AttackPlan` with phases and ordered tasks.
Called automatically when a new project is created.
- Plan persisted in SQLite via the `tasks` table. Fully survives session
restarts — the plan loads on the next `nemesis` invocation.
- `TaskList` widget renders the plan with phase grouping and per-task status
icons (pending / running / done / failed / skipped).
- Step mode flow: after each completed task, Orchestrator emits a
"next step: [X] — approve? (y/n/edit)" prompt to the chat panel and waits.
- Auto mode: Orchestrator runs the plan end-to-end with streaming progress.
User can type "pause" at any time to stop after the current task completes.
- Plan adaptation: after a HIGH or CRITICAL finding, Orchestrator proposes
reordering ("Found critical Apache CVE — suggest moving exploitation test
ahead of remaining recon. Proceed?").
- `Orchestrator.explain_step()` — user can ask "why are you suggesting this?"
for any plan step and receive a plain-language justification from the LLM.
- Destructive action gate fully wired: any `destructive=True` task always
pauses for explicit user confirmation regardless of control mode. The
confirmation is logged with timestamp, action description, and user response.

### Notes for contributors

- The plan generation prompt must include the engagement context provided by
the user at project creation. A plan for an e-commerce web application is
fundamentally different from one for an internal network assessment. The LLM
must receive this context to generate a relevant plan.
- Plans are suggestions, not scripts. Users must be able to add, remove, or
reorder tasks at any time via chat ("skip nikto", "add sqlmap to the plan",
"move gobuster to after nmap").
- Phase transitions must be explicit: the Orchestrator announces "moving to
ENUMERATION phase", updates the phase in the DB, and updates the TUI status bar.

---

## v0.6 — Report Generation

**Status: pending** | Depends on: v0.5

### Goal

Generate professional pentest reports from the accumulated project data —
findings, evidence, attack paths, and session history — with minimal effort
from the pentester. The output should be usable as a client deliverable with
only minor editing.

### Context for contributors

Reports are generated exclusively from `VALIDATED` findings. `DISMISSED` and
`UNVERIFIED` findings never appear in client output. This is by design — the
false-positive pipeline in v0.4 ensures only confirmed findings reach the report.
The entire finding lifecycle was designed with this output constraint in mind.

The report has two sections targeting different audiences:

1. **Executive Summary** — plain language, suitable for a non-technical
  decision-maker (CTO, risk manager). Describes what was found, the business
   risk, and prioritized remediation. Generated by the LLM.
2. **Technical Detail** — full finding descriptions, affected components, CVE
  references, reproduction steps, evidence excerpts, and remediation guidance.
   Structured and precise, for the engineering team.

### Deliverables

- `nemesis/reporting/` package with:
  - `generator.py` — assembles `ReportContext` from SQLite data, calls LLM
  for executive summary prose, renders templates
  - `models.py` — `ReportContext`, `ReportFinding`, `AttackPath` Pydantic models
  - `templates/` — Jinja2 templates (see below)
- Jinja2 templates:
  - `executive_summary.html.j2` — client-facing summary with risk overview
  - `technical_report.html.j2` — full technical detail with all findings
  - `finding_card.html.j2` — reusable per-finding component with severity badge,
  CVE references, reproduction steps, and remediation
  - `base.html.j2` — shared layout with CSS variables for easy branding
- PDF export via WeasyPrint rendered from the HTML output.
- HTML export — standalone file with embedded CSS, no external asset dependencies.
- CVSS v3.1 scoring: the LLM assigns a base score per finding; the user can
override it in the TUI before generating the report.
- "generate report" command triggers generation with a progress indicator
in the chat panel, then displays the output file path when done.
- Reports saved to `~/.nemesis/projects/<project_id>/reports/<timestamp>/`.

### Notes for contributors

- The executive summary LLM prompt must explicitly instruct the model to avoid
technical jargon: no CVE IDs, no port numbers, no protocol names. The audience
is a business stakeholder, not a developer.
- Severity labels in the report must use the defined standard
(CRITICAL / HIGH / MEDIUM / LOW / INFO) and no others. The LLM must be
constrained to this list in the prompt.
- Templates should be easy to customize without touching Python. A contributor
should be able to change branding, fonts, and colors by editing only the
Jinja2 templates and CSS variables in `base.html.j2`.

---

## v0.7 — Extended Tooling

**Status: pending** | Depends on: v0.3

### Goal

Expand the tool registry from 5 basic tools to a comprehensive set covering all
major pentest phases. After this milestone, NEMESIS supports a full web
application assessment — including vulnerability scanning, credential testing,
and SSL/TLS analysis — without leaving the TUI.

### Context for contributors

All new tools follow the same pattern as existing executors:

1. Create a subclass of `BaseExecutor` in `nemesis/agents/executor.py`
2. Implement `_build_command()` with sensible defaults for the most common use case
3. Register in `EXECUTOR_REGISTRY` and add a `ToolDefinition` to `TOOL_REGISTRY`
4. Add tool-specific output parsing hints to `analyst_prompts.py`
5. Add a test fixture with real sample output and verify Analyst extraction works

Each tool must have a `DESTRUCTIVE` flag set correctly. When in doubt, err on
the side of marking a tool as destructive — it is easy to downgrade later.

### Planned tools by phase

**Recon / OSINT**


| Tool         | Binary         | Purpose                                                | Destructive |
| ------------ | -------------- | ------------------------------------------------------ | ----------- |
| masscan      | `masscan`      | Fast port scan for large CIDRs (requires root)         | No          |
| subfinder    | `subfinder`    | Passive subdomain enumeration                          | No          |
| theHarvester | `theHarvester` | Email, subdomain, and employee OSINT                   | No          |
| dnsx         | `dnsx`         | Bulk DNS resolution and record enumeration             | No          |
| httpx        | `httpx`        | HTTP probe — status codes, titles, tech fingerprinting | No          |


**Web Enumeration**


| Tool           | Binary        | Purpose                                           | Destructive |
| -------------- | ------------- | ------------------------------------------------- | ----------- |
| ffuf           | `ffuf`        | Fast web fuzzer (directories, parameters, vhosts) | No          |
| feroxbuster    | `feroxbuster` | Recursive directory brute-force                   | No          |
| wafw00f        | `wafw00f`     | WAF detection and fingerprinting                  | No          |
| wappalyzer-cli | `wappalyzer`  | Technology stack detection                        | No          |


**Vulnerability Scanning**


| Tool    | Binary       | Purpose                                      | Destructive |
| ------- | ------------ | -------------------------------------------- | ----------- |
| nuclei  | `nuclei`     | Template-based vulnerability scanner         | No          |
| sqlmap  | `sqlmap`     | SQL injection detection and exploitation     | **Yes**     |
| dalfox  | `dalfox`     | XSS scanning and exploitation                | **Yes**     |
| wapiti  | `wapiti`     | Web application vulnerability scanner        | No          |
| testssl | `testssl.sh` | SSL/TLS configuration and cipher analysis    | No          |
| sslscan | `sslscan`    | SSL/TLS protocol and certificate enumeration | No          |


**Credential Testing** (all destructive — account lockout risk)


| Tool   | Binary   | Purpose                                          | Destructive |
| ------ | -------- | ------------------------------------------------ | ----------- |
| hydra  | `hydra`  | Online brute-force (SSH, HTTP, FTP, SMB, etc.)   | **Yes**     |
| medusa | `medusa` | Parallel brute-force with broad protocol support | **Yes**     |


**Post-Exploitation Helpers**


| Tool    | Script        | Purpose                                  | Destructive |
| ------- | ------------- | ---------------------------------------- | ----------- |
| LinPEAS | `linpeas.sh`  | Linux privilege escalation enumeration   | No          |
| WinPEAS | `winpeas.exe` | Windows privilege escalation enumeration | No          |


**Cloud**


| Tool       | Binary  | Purpose                                         | Destructive |
| ---------- | ------- | ----------------------------------------------- | ----------- |
| ScoutSuite | `scout` | Multi-cloud security auditing (AWS, Azure, GCP) | No          |
| aws-cli    | `aws`   | AWS resource and permission enumeration         | No          |


### Notes for contributors

- Any tool that can modify target system state (sqlmap with `--os-shell`,
hydra, dalfox in exploit mode) must have `destructive=True`. No exceptions.
The destructive confirmation gate from v0.5 handles user prompting automatically.
- Tools requiring root (masscan, some nmap scan types) should detect the missing
privilege at runtime and emit a clear error with the fix command — not a cryptic
`permission denied` traceback.
- For high-output tools (nuclei can produce thousands of template matches), the
Analyst prompt must have a specific extraction and deduplication strategy —
not a generic "summarize this output" instruction.

---

## v0.8 — External LLM Providers

**Status: pending** | Depends on: v0.2

### Goal

Allow pentesters to optionally connect NEMESIS to an external LLM provider of
their choice — OpenAI, Anthropic, Groq, Azure OpenAI, or any OpenAI-compatible
endpoint. Ollama remains the default and requires no configuration. External
providers are always opt-in and require explicit user action to enable.

This milestone is entirely additive. It must not change any behavior for users
who do not configure an external provider.

### Why this matters

Local models (Ollama) are the right default for most pentesters: no data leaves
the machine, no API costs, and the tool works fully offline during engagements.
However, there are real scenarios where a larger, more capable cloud model adds
value: complex report writing, deep vulnerability correlation, or environments
where internet access is acceptable and the larger context window of a frontier
model is worth the trade-off. This milestone enables those use cases without
compromising the local-first default.

### Architecture (already set up in v0.2)

LiteLLM was chosen from day one as the abstraction layer precisely for this
milestone. The `LLMClient` wrapper calls `litellm.acompletion(model=..., messages=...)`. Switching providers requires only changing the `model` string
in config — zero changes to agent logic:

```toml
# ~/.nemesis/config.toml

# Ollama (default — no API key, fully local)
model = "ollama/llama3.1:8b"

# OpenAI
model = "gpt-4o"
api_key = "sk-..."            # or set NEMESIS_API_KEY env var

# Anthropic
model = "claude-3-5-sonnet-20241022"
api_key = "sk-ant-..."

# Groq (fast inference, affordable)
model = "groq/llama-3.1-70b-versatile"
api_key = "gsk_..."

# Azure OpenAI
model = "azure/gpt-4o"
api_key = "..."
custom_base_url = "https://your-resource.openai.azure.com/"

# Any OpenAI-compatible endpoint (vLLM, LM Studio, LocalAI, Ollama remote)
model = "openai/my-custom-model"
custom_base_url = "http://localhost:8080/v1"
```

### Deliverables

- `nemesis/tui/screens/provider_setup.py` — interactive setup wizard with
five steps:
  1. Select provider (Ollama / OpenAI / Anthropic / Groq / Azure / Custom)
  2. Enter API key (stored only in `~/.nemesis/.env`, never in the DB or logs)
  3. Select or manually enter model name
  4. **Privacy acknowledgment screen** — explicit warning that engagement data
    (target IPs, scan output, finding descriptions, client context) will be
     transmitted to the selected provider's servers. The user must type
     `I understand` to proceed. This screen cannot be dismissed without
     acknowledging. This is not optional and must not be easy to bypass.
  5. Live connection test with the selected model before saving config
- `NemesisConfig` additions:
  - `provider: str` — selected provider identifier
  - `api_key: str` — loaded from `~/.nemesis/.env` or env var only, never
  persisted in the SQLite DB or printed in logs
  - `custom_base_url: str | None` — for self-hosted endpoints
- `LLMClient` updated to pass the correct provider prefix and credentials
to LiteLLM using the `provider/model-name` format.
- Status bar: when a cloud provider is active, display `[CLOUD AI]` in red
instead of the default `[LOCAL AI]` in cyan. This persistent indicator
must be visible at all times — it must not disappear or be dismissed while
a cloud provider is configured.
- `nemesis/core/privacy.py` — utility that lists exactly which data
categories are included in LLM calls (target IPs, scan output, finding
descriptions, engagement context text). Users consult this before enabling
a cloud provider to understand what leaves their machine.
- Fallback behavior: if the external provider is unreachable, NEMESIS falls
back to Ollama if a local instance is available, rather than crashing.
- Documentation: README section with configuration examples for each
supported provider, minimum recommended model sizes, and privacy implications.

### Supported providers at v0.8 launch


| Provider         | Example models                                 | API key required | Data stays local  |
| ---------------- | ---------------------------------------------- | ---------------- | ----------------- |
| Ollama (default) | `llama3.1:8b`, `llama3.2:3b`, `qwen2.5:14b`    | No               | Yes               |
| OpenAI           | `gpt-4o`, `gpt-4o-mini`                        | Yes              | No                |
| Anthropic        | `claude-3-5-sonnet-20241022`, `claude-3-haiku` | Yes              | No                |
| Groq             | `llama-3.1-70b-versatile`, `mixtral-8x7b`      | Yes              | No                |
| Azure OpenAI     | `azure/gpt-4o`                                 | Yes + endpoint   | Depends on tenant |
| Custom endpoint  | any OpenAI-compatible model                    | Optional         | Depends on host   |


### Notes for contributors

- API keys must never appear in logs, error messages, DB records, chat history,
or TUI output under any circumstances. The `security.mdc` Cursor rule enforces
this. If you find a code path where a key could be leaked, treat it as a
security bug and report it.
- The privacy acknowledgment screen is not optional and must not be trivially
bypassable. This exists to protect users from accidentally sending sensitive
client engagement data to external services.
- LiteLLM handles rate limiting, retries, and provider-specific quirks for most
providers — do not re-implement these. Use LiteLLM's built-in `num_retries`
and `timeout` parameters.
- The `[CLOUD AI]` status bar badge must remain visible at all times while a
cloud provider is active. It must not be dismissible or hidden.

---

## v0.9 — Plugin System

**Status: pending** | Depends on: v0.7

### Goal

Allow contributors and advanced users to add custom tools to NEMESIS without
modifying the core codebase. A plugin is a Python package that registers one
or more executors, Analyst parsing hints, and optionally TUI widgets.

### Context for contributors

The plugin system uses Python's standard entry points mechanism. A plugin
declares itself in its `pyproject.toml`:

```toml
[project.entry-points."nemesis.tools"]
my_tool = "my_nemesis_plugin.executor:MyToolExecutor"
```

NEMESIS discovers registered executors at startup by scanning the
`nemesis.tools` entry point group. This means:

- Zero modification to NEMESIS core code to add a tool
- Plugins are installed with `pip install` or `uv add` like any Python package
- Plugins can be versioned, published to PyPI, and maintained independently

### Deliverables

- `nemesis/plugins/loader.py` — entry point scanner that discovers and
registers plugin executors into `EXECUTOR_REGISTRY` and `TOOL_REGISTRY`
at startup. Logs which plugins were loaded successfully and which failed.
- Plugin interface specification. A valid plugin executor must:
  - Subclass `BaseExecutor`
  - Declare `TOOL_NAME: str`, `TOOL_BINARY: str`, `DESTRUCTIVE: bool`
  - Provide a `TOOL_DEFINITION: ToolDefinition` class attribute
  - Implement `_build_command(binary: str) -> list[str]`
- `nemesis/plugins/validator.py` — validates plugin executors at load time.
Checks: correct subclass, required attributes present, `shell=False` in
command building, `DESTRUCTIVE` flag declared. Plugins that fail validation
are skipped with a warning — they never cause a startup crash.
- `nemesis/tui/screens/plugin_manager.py` — TUI screen to view installed
plugins, their tool definitions, and enable or disable individual tools.
- Reference plugin repository (separate GitHub repo) as a minimal working
example for contributors building their own plugins.
- "Writing a NEMESIS Plugin" guide covering: executor implementation,
registering Analyst parsing hints, publishing to PyPI.

### Notes for contributors

- Plugin executors are subject to the same security constraints as built-in
tools: no `shell=True`, no scope bypass, destructive tools must declare it.
The validator enforces what it can detect statically.
- The plugin loader must be resilient. A broken or malicious plugin must never
prevent NEMESIS from starting. Catch all exceptions during plugin loading,
log them at WARNING level, and continue startup.

---

## v1.0 — Stable Release

**Status: pending** | Depends on: all previous milestones

### Goal

Reach a stable, well-documented, well-tested release that external contributors
and production users can rely on. This milestone is about quality and durability,
not new features.

### Deliverables

- Test coverage ≥ 80% on `nemesis/agents/`, `nemesis/core/`, and `nemesis/db/`.
- Integration tests for the full flow: create project → run tool → analyst
processes output → finding stored → report generated.
- Linting and type checking clean: `ruff check`, `ruff format --check`,
`mypy --strict` all pass with zero errors.
- `CONTRIBUTING.md` with: local setup instructions, architecture overview
diagram, code style guide, PR checklist, and definition of done per milestone.
- `docs/` with:
  - Architecture deep-dive (agent flow diagrams, DB schema, TUI layout)
  - Configuration reference (all `NemesisConfig` fields with examples)
  - Tool reference (all built-in tools, default flags, output format descriptions)
  - Plugin development guide
  - FAQ for common setup issues (Ollama not running, tool not found, etc.)
- GitHub Actions CI: lint + type check + unit tests + integration tests on
every push and PR.
- `setup.sh` — one-shot install script for Kali/Parrot/Ubuntu that installs
system tool dependencies and Python packages via `uv`.
- Security review: all subprocess calls audited for injection surfaces, all
LLM prompt inputs reviewed for injection risk, all file I/O paths validated.
- `CHANGELOG.md` covering all changes from v0.1 to v1.0.
- GitHub release with signed artifacts and SHA256 checksums.

---

## Long-term ideas (post-v1.0)

These are not committed to any specific milestone. They are tracked here so
contributors understand the longer-term direction and can design current
features with future extension in mind.

**Collaboration mode**
Shared project database (SQLite → PostgreSQL) allowing multiple pentesters to
work on the same engagement simultaneously. Requires a server component and an
authentication layer. Out of scope until v1.0 is stable.

**API server mode**
Expose NEMESIS as a local HTTP API so it can be integrated with external tools:
Burp Suite extension, VS Code plugin, custom automation scripts. Would use the
same agent layer as the TUI, with HTTP endpoints replacing the chat interface.

**Burp Suite integration**
Import Burp Suite findings (from XML export) directly into the project finding
database. Allows using Burp for proxy interception and NEMESIS for correlation
and reporting.

**Metasploit integration**
Connect to the Metasploit RPC API to suggest and — with explicit user
confirmation — execute modules against validated findings. Always `destructive=True`,
always requires confirmation, always logged.

**Offline CVE database**
Local mirror of NVD/CVE data so the Analyst can resolve CVE IDs, CVSS scores,
and remediation references without internet access during air-gapped engagements.

**Finding templates library**
Community-maintained library of standardized finding descriptions and
remediation recommendations for well-known vulnerability classes. Reduces LLM
writing load and ensures consistent report quality across different contributors.

**Web UI**
Optional browser-based interface for users who prefer a GUI over a terminal.
Built on top of API server mode. Out of scope until the core is stable and the
API server exists.

---

## Contributing

Before picking up any item in this roadmap:

1. **Read `.cursor/rules/`** — these three files encode the design constraints
  that keep the codebase consistent. They are enforced by the AI assistant
   and should be treated as binding conventions.
2. **Open an issue** to claim the work and discuss your approach before writing
  code. This avoids duplicate effort and ensures alignment with the milestone
   design intent.
3. **Read the milestone's "Notes for contributors"** — it documents decisions
  made deliberately that should not be reversed without discussion.
4. **Every new feature needs tests.** Every new agent method needs a docstring.
  Every new config option needs a description field in `NemesisConfig`.
5. **Run the full check suite before submitting a PR:**

```bash
uv run ruff check nemesis/ tests/
uv run ruff format --check nemesis/ tests/
uv run mypy nemesis/
uv run pytest
```

