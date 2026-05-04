# tradebot

## Project overview

`tradebot` is a Python trading-bot workspace for Binance Spot testnet-style
workflows, local SQLite-backed runtime storage, dashboard monitoring, Telegram
controls, and optional local AI review.

This repository is organized for cautious modernization. The current focus is
making setup, safety, testing, and runtime boundaries clear before larger
structural refactors. For deeper operating details, read [OPERATIONS.md](OPERATIONS.md).

## Safety warning

Trading automation is high risk. Testnet, paper, and dry-run workflows reduce
risk, but they do not prove that live trading is safe. Live operation can be
affected by market volatility, exchange latency, partial fills, network
failures, account permissions, API errors, and operator mistakes.

Never commit real secrets. Keep Binance API keys, Telegram bot tokens,
dashboard tokens, `.env` files, databases, logs, JSONL trade logs, and runtime
state files out of Git. If real secrets were committed or shared in a ZIP,
rotate them immediately. See [SECURITY.md](SECURITY.md).

## Architecture overview

For a fuller architecture map and refactor boundaries, see
[docs/architecture.md](docs/architecture.md).

### Engine

`engine.py` owns the trading loop, runtime state handling, grid/accounting
state, optional AI signal consumption, and status/event outputs. It is the
highest-risk module because it is closest to strategy decisions and exchange
interaction.

### SQLite persistence

`sqlite_store.py` provides the canonical local SQLite runtime store. JSON and
JSONL files remain compatibility mirrors for selected runtime flows.

### Dashboard/control surface

`dashboard_server.py`, `dashboard_routes.py`, dashboard support modules, and
`dashboard/static/` provide local visibility and selected controls. Treat
dashboard mutation endpoints as sensitive and use token controls whenever the
dashboard is not localhost-only.

### AI sidecar

`ai_sidecar.py` can produce local AI reviews and optional AI signal outputs
using an OpenAI-compatible local endpoint. AI output is advisory unless existing
engine configuration explicitly consumes it.

### Telegram control bot

`control_bot.py` provides Telegram operator controls and status summaries for
approved users. Telegram tokens and admin identifiers belong in local
environment files or deployment secret storage only.

## Requirements

- Python 3.11 is recommended for CI parity.
- Python 3.10 is currently used by the local development environment.
- Git.
- Network access for dependency installation.
- Optional local services depending on what you run: Binance testnet access,
  Telegram bot access, dashboard access, and an Ollama/OpenAI-compatible local
  AI endpoint.

## Setup

### Create venv

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### Install runtime deps

```bash
python -m pip install -r requirements.txt
```

### Install dev deps

```bash
python -m pip install -r requirements-dev.txt
```

## Environment variables

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

Edit `.env` locally. Do not commit `.env` or any `.env.*` file containing real
values. `.env.example` must stay placeholder-only, with secret values left
blank.

Review the sections in `.env.example` before running services:

- Binance/testnet configuration.
- Telegram control bot configuration.
- Persistence/runtime storage.
- Dashboard configuration.
- Local AI sidecar configuration.

Legacy Baserow environment settings are intentionally absent because the
deprecated Baserow tooling has been removed.

`TRADEBOT_DASHBOARD_HOST=0.0.0.0` exposes the dashboard to the network. Set
`TRADEBOT_DASHBOARD_TOKEN` if the dashboard is not localhost-only.

## Running tests

```bash
source .venv/bin/activate
python -m pytest -q
```

## Running coverage

Coverage is informational only. The repository does not enforce a minimum
coverage percentage yet.

```bash
python3 -m coverage run -m pytest -q
python3 -m coverage report -m
```

## Running smoke checks

Run the Binance REST smoke check after configuring local environment values:

```bash
source .venv/bin/activate
python bot.py
```

Expected output includes ping/server-time checks, market-data checks, and
authenticated account checks when credentials are configured.

Run Python compilation as a quick syntax check:

```bash
python -m compileall -q .
```

## Running local services

Use the service orchestrator for normal local operations:

```bash
python dashboard_orchestrator.py start
python dashboard_orchestrator.py status
python dashboard_orchestrator.py stop
```

Run individual components directly only when debugging:

```bash
python engine.py
python dashboard_server.py
python control_bot.py
python ai_sidecar.py
```

Run a one-off AI review:

```bash
python ai_playground.py
```

For startup, shutdown, status checks, dashboard details, AI sidecar details, and
recovery guidance, see [OPERATIONS.md](OPERATIONS.md).

## Runtime files and logs

Runtime files are local artifacts and must not be committed. Examples include:

- `tradebot.sqlite3`
- `*.sqlite3-wal`
- `*.sqlite3-shm`
- `*.log`
- `*.pid`
- `*.nohup.out`
- `state.json`
- `runtime_state.json`
- `engine_status.json`
- `trades.jsonl`
- `ai_signal.json`
- `ai_decisions.jsonl`
- `ai_memory.json`
- `dashboard_history.json`

For the full source-vs-runtime policy and future runtime layout guidance, see
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

## Repository structure

```text
.
|-- bot.py                         # Binance REST smoke check
|-- engine.py                      # Trading engine
|-- sqlite_store.py                # SQLite persistence helpers
|-- dashboard_server.py            # Dashboard server
|-- dashboard_routes.py            # Dashboard route helpers
|-- dashboard/                     # Dashboard static assets
|-- control_bot.py                 # Telegram control bot
|-- ai_sidecar.py                  # Optional AI sidecar
|-- ai_playground.py               # One-off AI review helper
|-- dashboard_orchestrator.py      # Local service orchestrator
|-- requirements.txt               # Runtime dependencies
|-- requirements-dev.txt           # Development/test dependencies
|-- .env.example                   # Placeholder-only environment template
|-- SECURITY.md                    # Security and secret handling guidance
|-- OPERATIONS.md                  # Operational runbook
|-- docs/                          # Architecture and policy documentation
`-- tests/                         # Test suite
```

Related docs:

- [docs/architecture.md](docs/architecture.md)
- [docs/runtime-artifacts.md](docs/runtime-artifacts.md)
- [docs/legacy-inventory.md](docs/legacy-inventory.md)

## Security

Read [SECURITY.md](SECURITY.md) before configuring credentials or sharing this
repository. Real secrets must never be committed. Runtime databases, logs, JSONL
trade logs, PID files, generated state files, screenshots, and ZIP archives can
contain sensitive operational data.
