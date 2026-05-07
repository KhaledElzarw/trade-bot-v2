# Tradebot

Local-first Binance Spot trading automation with SQLite persistence, dashboard
operations, and optional AI review. Built for cautious paper and Binance Spot
testnet-oriented workflows, not for casual live-risk experimentation.

Tradebot is organized for cautious modernization: setup, safety, tests, runtime
boundaries, and operator docs should stay clear before larger structural
refactors.

For deeper operating details, read [OPERATIONS.md](OPERATIONS.md).

## Contents

- [Safety TL;DR](#safety-tldr)
- [What Tradebot Does](#what-tradebot-does)
- [What Tradebot Does Not Do](#what-tradebot-does-not-do)
- [Quick Start](#quick-start)
- [Operator Quick Reference](#operator-quick-reference)
- [Docs by Goal](#docs-by-goal)
- [Architecture Overview](#architecture-overview)
- [Runtime Files and Logs](#runtime-files-and-logs)
- [Troubleshooting](#troubleshooting)
- [Repository Structure](#repository-structure)
- [Security](#security)

## Safety TL;DR

- Start with paper, Binance Spot testnet, and smoke-test workflows. A clean
  testnet run does not prove live trading is safe.
- Never grant Binance withdrawal permissions to bot API keys. Use the minimum
  permissions required for the operating mode.
- Treat the dashboard as a sensitive local control surface. If it is not bound
  to localhost only, set `TRADEBOT_DASHBOARD_TOKEN`; do not expose it with a
  blank/default token.
- Runtime files are sensitive: SQLite DBs, WAL/SHM files, logs, JSON/JSONL
  state, screenshots, and ZIP archives can contain account, strategy, or
  operational data.
- AI sidecar output is advisory unless the existing engine configuration
  explicitly consumes it. Do not treat AI output as a safety guarantee or a
  promise of profitable trading.

Trading automation is high risk. Live operation can be affected by market
volatility, exchange latency, partial fills, network failures, account
permissions, API errors, and operator mistakes. Never commit real secrets. If
real secrets were committed or shared in a ZIP, rotate them immediately. See
[SECURITY.md](SECURITY.md).

## What Tradebot Does

- Runs a local Python trading engine for paper and Binance Spot
  testnet-oriented workflows.
- Supports the current grid modes `scalpy` and `fatty`; unsupported grid modes
  fail closed through engine and dashboard validation.
- Uses `tradebot.sqlite3` through `sqlite_store.py` as the canonical local
  runtime store.
- Keeps selected JSON and JSONL runtime mirrors for compatibility, inspection,
  recovery, and migration workflows.
- Provides a local browser dashboard for monitoring and selected controls.
- Starts, stops, restarts, and checks grouped services through
  `dashboard_orchestrator.py`.
- Can run an optional AI sidecar through a local OpenAI-compatible endpoint and
  produce advisory reviews or signals.
- Provides a manual SQLite migration/backfill command through
  `migrate_to_sqlite.py`.

## What Tradebot Does Not Do

- It does not guarantee profitable trading, loss prevention, or live-trading
  safety.
- It does not require or support Binance withdrawal permissions for bot keys.
- It does not make a public, unauthenticated dashboard safe to expose.
- It does not make AI/model output authoritative. AI decisions, memory, and
  signals are sensitive runtime artifacts and remain advisory unless existing
  engine configuration explicitly consumes them.
- It does not support the removed legacy Baserow workflow or legacy external
  chat-bot control surface.
- It does not move runtime files out of the repository root yet; current paths
  are preserved for compatibility.

## Quick Start

These commands assume you are in the repository root.

### 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 2. Install runtime and development dependencies

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

### 3. Create local configuration

```bash
cp .env.example .env
```

Edit `.env` locally. Keep Binance credentials blank in tracked files and store
real values only in private `.env` files or deployment secret storage. Review
the Binance/testnet, persistence, dashboard, and local AI sidecar sections
before starting services.

The example config may use a network-facing dashboard host. For first local
runs, set `TRADEBOT_DASHBOARD_HOST=127.0.0.1`; only use a network-facing host
when remote access is intentional and `TRADEBOT_DASHBOARD_TOKEN` is set.

```text
TRADEBOT_DASHBOARD_HOST=127.0.0.1
TRADEBOT_DASHBOARD_TOKEN=
```

### 4. Run tests before services

```bash
python -m pytest -q
```

### 5. Start local services through the orchestrator

```bash
python dashboard_orchestrator.py start
python dashboard_orchestrator.py status
```

Default dashboard URL:

```text
http://localhost:8844/
```

Stop services explicitly when you are done:

```bash
python dashboard_orchestrator.py stop
```

## Operator Quick Reference

| Goal | Command |
| --- | --- |
| Run tests | `.venv/bin/python -m pytest -q` |
| Run coverage | `.venv/bin/python -m coverage run -m pytest -q`<br>`.venv/bin/python -m coverage report -m` |
| Compile Python files | `.venv/bin/python -m compileall -q .` |
| Start services | `.venv/bin/python dashboard_orchestrator.py start` |
| Stop services | `.venv/bin/python dashboard_orchestrator.py stop` |
| Check service status | `.venv/bin/python dashboard_orchestrator.py status` |
| Restart services | `.venv/bin/python dashboard_orchestrator.py restart` |
| Run a one-off AI review | `.venv/bin/python ai_playground.py` |
| Run SQLite migration/backfill | `.venv/bin/python migrate_to_sqlite.py` |

Coverage is informational only. The repository does not enforce a minimum
coverage percentage yet. Run direct components only when debugging:

```bash
python engine.py
python dashboard_server.py
python ai_sidecar.py
```

## Docs by Goal

| Goal | Read |
| --- | --- |
| Understand module boundaries and refactor risks | [docs/architecture.md](docs/architecture.md) |
| Handle SQLite, logs, JSON/JSONL mirrors, backups, and archives | [docs/runtime-artifacts.md](docs/runtime-artifacts.md) |
| Understand removed legacy paths and quarantine candidates | [docs/legacy-inventory.md](docs/legacy-inventory.md) |
| Operate, stop, recover, migrate, and troubleshoot services | [OPERATIONS.md](OPERATIONS.md) |
| Configure credentials and report security issues safely | [SECURITY.md](SECURITY.md) |

## Requirements

- Python 3.11 is recommended for CI parity.
- Python 3.10 is currently used by the local development environment.
- Git.
- Network access for dependency installation.
- Optional local services depending on what you run: Binance Spot testnet
  access, dashboard access, and an Ollama/OpenAI-compatible local AI endpoint.

## Environment Variables

Create `.env` from `.env.example` and keep `.env` untracked:

```bash
cp .env.example .env
```

`.env.example` must stay placeholder-only, with secret values left blank.
Review the sections in `.env.example` before running services:

- Binance/testnet configuration.
- Persistence/runtime storage.
- Dashboard configuration.
- Local AI sidecar configuration.

Legacy Baserow environment settings are intentionally absent because the
deprecated Baserow tooling has been removed.

`TRADEBOT_DASHBOARD_HOST=0.0.0.0` exposes the dashboard to the network. Set
`TRADEBOT_DASHBOARD_TOKEN` if the dashboard is not localhost-only.

## Architecture Overview

For a fuller architecture map and refactor boundaries, see
[docs/architecture.md](docs/architecture.md).

### Engine

`engine.py` owns the trading loop, runtime state handling, grid/accounting
state, optional AI signal consumption, and status/event outputs. It is the
highest-risk module because it is closest to strategy decisions and exchange
interaction.

Supported grid modes are `scalpy` and `fatty`; unsupported grid modes fail
closed through engine and dashboard configuration validation.

### SQLite Persistence

`sqlite_store.py` provides the canonical local SQLite runtime store. The
default local database is currently `tradebot.sqlite3`. WAL and SHM sidecar
files may be created by SQLite while the database is active.

JSON and JSONL files remain compatibility mirrors for selected runtime flows,
operator inspection, recovery, and migration work.

### Dashboard and Control Surface

`dashboard_server.py`, `dashboard_routes.py`, dashboard support modules, and
`dashboard/static/` provide local visibility and selected controls. Treat
dashboard mutation endpoints as sensitive and use token controls whenever the
dashboard is not localhost-only.

### AI Sidecar

`ai_sidecar.py` can produce local AI reviews and optional AI signal outputs
using an OpenAI-compatible local endpoint. AI output is advisory unless
existing engine configuration explicitly consumes it.

### External Messaging Runtime

External chat-bot control and notification runtimes are not part of the
supported application. Operational visibility and controls are owned by the
dashboard, logs, and local runtime state.

## Runtime Files and Logs

Runtime files are local artifacts and must not be committed. They may contain
account state, trade history, dashboard state, AI output, operational logs, or
local secrets.

Current examples include:

- `tradebot.sqlite3`
- `*.sqlite3-wal`
- `*.sqlite3-shm`
- `*.log`
- `*.pid`
- `*.nohup.out`
- `state.json`
- `runtime_state.json`
- `engine_status.json`
- `cumulative.json`
- `trades.jsonl`
- `ai_signal.json`
- `ai_decisions.jsonl`
- `ai_memory.json`
- `dashboard_history.json`

Legacy local artifacts from removed workflows may still exist in old
workspaces and remain ignored:

- `advisor.log`
- `state_trend.json`
- `engine_status_trend.json`
- `cumulative_trend.json`
- `trades_trend.jsonl`
- `engine_trend.log`

For the full source-vs-runtime policy, backup guidance, restore guidance, and
future runtime layout direction, see
[docs/runtime-artifacts.md](docs/runtime-artifacts.md).

## Troubleshooting

- If `python -m pytest -q` fails because `pytest` is missing, install
  development dependencies with `python -m pip install -r requirements-dev.txt`.
- If `python3 -m pip` is unavailable on a system Python, use the project virtual
  environment created with `python3 -m venv .venv`.
- If Binance authentication fails, confirm that credentials are present only in
  local `.env` files and that the base URL matches the intended environment.
- If the dashboard is unreachable, check `TRADEBOT_DASHBOARD_HOST`,
  `TRADEBOT_DASHBOARD_PORT`, firewall rules, and the orchestrator status.
- If the AI sidecar fails, confirm the local AI endpoint is reachable and the
  configured model exists.
- If generated runtime files appear in the working tree, check
  [docs/runtime-artifacts.md](docs/runtime-artifacts.md) before deciding whether
  to ignore, preserve, or clean them.

## Repository Structure

```text
.
|-- engine.py                      # Trading engine
|-- sqlite_store.py                # SQLite persistence helpers
|-- dashboard_server.py            # Dashboard server
|-- dashboard_routes.py            # Dashboard route helpers
|-- dashboard/                     # Dashboard static assets
|-- ai_sidecar.py                  # Optional AI sidecar
|-- ai_playground.py               # One-off AI review helper
|-- dashboard_orchestrator.py      # Local service orchestrator
|-- migrate_to_sqlite.py           # Manual SQLite migration/backfill helper
|-- requirements.txt               # Runtime dependencies
|-- requirements-dev.txt           # Development/test dependencies
|-- .env.example                   # Placeholder-only environment template
|-- SECURITY.md                    # Security and secret handling guidance
|-- OPERATIONS.md                  # Operational runbook
|-- docs/                          # Architecture and policy documentation
`-- tests/                         # Test suite
```

## Security

Read [SECURITY.md](SECURITY.md) before configuring credentials or sharing this
repository. Real secrets must never be committed. Runtime databases, logs, JSONL
trade logs, PID files, generated state files, AI artifacts, screenshots, and
ZIP archives can contain sensitive operational data.
