![NEMESIS](./banner.png)

<div align="center">

[![Python](https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![LiteLLM](https://img.shields.io/badge/AI-LiteLLM-cyan?style=flat-square)](https://github.com/BerriAI/litellm)
[![Ollama](https://img.shields.io/badge/default%20backend-Ollama-blue?style=flat-square)](https://ollama.com)
[![Status](https://img.shields.io/badge/status-alpha-red?style=flat-square)]()

**AI-assisted penetration testing co-pilot. Not an autonomous hacker — your expert analyst, always at your side.**

</div>

---

## What is NEMESIS?

NEMESIS is a terminal-based AI co-pilot for penetration testers. It is powered by **LiteLLM** — an abstraction layer that supports any AI provider. By default it runs a local model via Ollama: no cloud, no API keys, no data leaving your machine. Future versions will optionally support OpenAI, Anthropic, Groq, and any OpenAI-compatible endpoint.

Unlike fully autonomous tools, NEMESIS follows the **assisted pentest** philosophy: **you drive, the AI assists**. You make the strategic decisions; NEMESIS handles memory, analysis, false-positive filtering, and finding correlation across your entire engagement.

Think of it as Cursor, but for penetration testing.

```
You:     "found this nmap output, what stands out?"
NEMESIS: "Apache 2.4.49 on port 80 — CVE-2021-41773 path traversal (CRITICAL).
          Want me to verify with a PoC before we continue enumerating?"
You:     "yes"
NEMESIS: [runs verification] "Confirmed. LFI works. Document and move on, or exploit now?"
```

---

## Key Features

- **Project-based memory** — every scan, finding, and decision is stored per engagement. Resume days later and the AI knows exactly where you left off.
- **AI attack planner** — after initial recon, NEMESIS proposes a phased attack plan based on what it found. You approve, modify, or ignore each step.
- **Finding correlation** — the AI links related findings across scans: "this exposed `/admin` path combined with the outdated Apache version forms a direct RCE vector."
- **False-positive filtering** — raw tool output goes through the Analyst agent before reaching you. Noise is filtered; uncertain findings are flagged for your review.
- **3 control modes** — Auto (AI drives), Step (approve each move), Manual (you command, AI analyzes).
- **Destructive action gates** — exploits, brute force, and any potentially disruptive action require your explicit confirmation, which is logged for audit trails.
- **100% local by default** — powered by LiteLLM with Ollama as the default backend. Your targets, findings, and client data never leave your machine unless you explicitly configure an external provider.
- **Cyberpunk TUI** — full terminal UI with panels, real-time streaming, and a chat interface that feels like a colleague, not a form.

---

## How NEMESIS differs from other tools


| Feature                 | METATRON              | NEMESIS                                  |
| ----------------------- | --------------------- | ---------------------------------------- |
| Mental model            | Isolated scans        | Full engagement project                  |
| AI role                 | Analyzes output       | Plans, assists, correlates               |
| Memory                  | None between scans    | Cumulative per project                   |
| False positive handling | None                  | Analyst agent filters all raw output     |
| Finding correlation     | None                  | Cross-scan correlation with attack paths |
| Control                 | Fully automatic       | Auto / Step / Manual modes               |
| Storage                 | MariaDB (external)    | SQLite (embedded, portable)              |
| AI layer                | Fixed local model     | LiteLLM (provider-agnostic abstraction)  |
| Default AI backend      | Fixed Qwen via Ollama | Ollama local (swappable via config)      |
| Platforms               | Parrot OS only        | Kali, Parrot, Ubuntu, macOS              |
| Interface               | Text menus            | Full reactive TUI with chat              |


---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   PENTESTER (TUI)                       │
│     "scan this" / "what next?" / "explain this"        │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│              ORCHESTRATOR (main agent)                  │
│  - Holds full project context                           │
│  - Plans attack phases                                  │
│  - Decides which executors to spawn                     │
│  - Answers strategic questions                          │
└──────┬──────────────────────────────────────┬───────────┘
       │ spawns                                │ receives
       │                                       │ findings
┌──────▼──────────────┐         ┌─────────────▼──────────┐
│  EXECUTORS (N)      │         │  ANALYST               │
│  - One per tool run │──raw──▶ │  - Filters noise       │
│  - Short-lived      │         │  - Scores confidence   │
│  - Run in parallel  │         │  - Correlates findings │
└─────────────────────┘         └────────────────────────┘
                                          │
                                ┌─────────▼──────────────┐
                                │  SQLite (project DB)   │
                                │  projects, findings,   │
                                │  sessions, attack plan │
                                └────────────────────────┘
```

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [Ollama](https://ollama.com) — default local model backend (required for local use)
- Linux (Kali, Parrot, Ubuntu) or macOS

NEMESIS uses **LiteLLM** as its AI layer, which means the model backend is configurable.
Ollama is the default for local, offline, air-gapped use. See [v0.8 in the roadmap](ROADMAP.md)
for future support of OpenAI, Anthropic, Groq, and custom endpoints.

**Recommended Ollama model:**

```bash
ollama pull llama3.1:8b
```

For systems with limited RAM:

```bash
ollama pull llama3.2:3b
```

**System tools** (install based on what you plan to use):

```bash
# Debian/Ubuntu/Kali/Parrot
sudo apt install nmap whois dnsutils curl nikto gobuster

# macOS (Homebrew)
brew install nmap whois gobuster
```

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/your-username/nemesis.git
cd nemesis

# 2. Install dependencies with uv
uv sync

# 3. Start Ollama (default local AI backend) and pull a model
ollama serve &
ollama pull llama3.1:8b

# 4. Launch NEMESIS
uv run nemesis
```

---

## Quick Start

```
$ uv run nemesis

[NEMESIS boots with splash screen]

[nemesis] Welcome. No active project.
          Start a new engagement? (n) or load existing? (l)

> n

[nemesis] Target? (IP, domain, CIDR — or comma-separated list)

> 192.168.1.0/24, app.target.com

[nemesis] Got it. Any context about this engagement? (optional)
          e.g. client type, objectives, restrictions, rules of engagement

> e-commerce company, focus on web app, no destructive tests

[nemesis] Understood. Prioritizing web surface for e-commerce.
          Will avoid actions that could cause downtime.
          Payment flows and authentication will get extra attention.

          Ready. What do you want to do?

> run initial recon on app.target.com

[nemesis] Starting recon: nmap -sV -sC, whois, DNS enum...
          [████████████░░░░] running...
```

---

## Usage

### Control Modes

Set your preferred control mode at any time:

```
> mode auto    — AI executes full plans autonomously
> mode step    — AI proposes each action, you approve (default)
> mode manual  — you direct every command, AI only analyzes
```

### Useful Commands

```
> new project          — start a new engagement
> load project         — switch to an existing project
> status               — show current project, phase, and findings
> findings             — list all validated findings
> plan                 — show the current attack plan
> report               — generate PDF/HTML report
> help                 — show all available commands
```

### Chat Interface

Just talk to NEMESIS in natural language:

```
> what's the most critical finding so far?
> run gobuster with a medium wordlist
> is this nikto output a false positive? [paste output]
> what attack paths can we chain from these findings?
> generate the executive summary section
```

---

## Project Structure

```
nemesis/
├── nemesis/
│   ├── main.py              # entry point
│   ├── tui/                 # terminal UI (Textual)
│   │   ├── app.py
│   │   ├── theme.tcss
│   │   ├── screens/         # splash, main, new_project
│   │   └── widgets/         # chat, context, task_list, status_bar
│   ├── core/                # domain models and config
│   ├── agents/              # orchestrator, analyst, executor
│   ├── db/                  # SQLite async persistence
│   └── tools/               # tool wrappers (nmap, gobuster, etc.)
├── tests/
├── pyproject.toml
└── README.md
```

---

## Roadmap

- Foundation: TUI, project model, SQLite persistence
- Orchestrator agent with LiteLLM/Ollama integration
- Executor agents: nmap, whois, dig, curl, gobuster, nikto
- Analyst agent: false-positive filtering, confidence scoring
- Finding correlation and attack path construction
- Report generation (PDF + HTML)
- Additional tools: sqlmap, nuclei, theHarvester, ffuf
- External LLM providers (OpenAI, Anthropic) as opt-in
- Plugin system for custom tools

---

## Contributing

Contributions are welcome. Please open an issue before submitting large PRs to discuss the approach.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Follow the coding standards in `.cursor/rules/`
4. Run linting: `uv run ruff check && uv run ruff format`
5. Run tests: `uv run pytest`
6. Submit a PR with a clear description

---

## Disclaimer

**NEMESIS is intended exclusively for authorized penetration testing and security research.**

- Only use NEMESIS against systems you own or have **explicit written authorization** to test.
- Unauthorized scanning, enumeration, or exploitation of computer systems is **illegal** in most jurisdictions and can result in criminal prosecution.
- The authors are not responsible for any misuse of this tool.
- Always obtain proper written authorization (e.g., a Rules of Engagement document) before testing any system.

---

## License

MIT License — see [LICENSE](LICENSE) for details.