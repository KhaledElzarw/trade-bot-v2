# tradebot

Trading bot tooling for Binance Spot testnet-style workflows, local runtime
storage, dashboard monitoring, Telegram controls, and optional local AI review.

This repository contains automation for a high-risk domain. Treat every change,
configuration file, credential, and runtime artifact with care.

## Project Overview

`tradebot` is organized around a grid-style trading engine and local operating
tools:

- A Binance REST smoke check in `bot.py`.
- A trading engine in `engine.py`.
- SQLite-backed persistence through `sqlite_store.py`.
- A dashboard and control surface through `dashboard_server.py`,
  `dashboard_routes.py`, and related dashboard modules.
- A Telegram control bot in `control_bot.py`.
- An optional AI sidecar in `ai_sidecar.py` with local AI playground tooling.
- Service wrappers and an orchestrator for local process management.

The current default examples are testnet-oriented. Do not assume that code or
configuration is production-ready for live funds.

## Safety Warning

Trading bots can lose money quickly. Testnet, paper, and dry-run workflows do
not guarantee live trading safety. Live trading adds exchange latency, partial
fills, network failures, account permission risk, market volatility, and human
configuration mistakes.

Keep secrets out of Git. Never commit real Binance API keys, Telegram bot
tokens, dashboard tokens, `.env` files, databases, logs, JSONL trade logs, or
runtime state files. If real secrets were committed or shared in a ZIP, rotate
them immediately.

## Architecture Overview

### Engine

`engine.py` runs the trading loop, reads configuration and runtime state, writes
status, and records trading events. It is the most sensitive part of the
repository because it can interact with exchange APIs depending on
configuration.

### SQLite Persistence

`sqlite_store.py` provides local persistence. SQLite (`tradebot.sqlite3`) is the
canonical runtime store, while JSON files remain compatibility mirrors for some
runtime flows.

### Dashboard and Control Surface

`dashboard_server.py`, `dashboard_routes.py`, dashboard static assets, and
supporting modules provide local visibility and controls. Dashboard realtime
channels are split by responsibility:

- SSE `/api/live/events`: engine/runtime/status/events.
- WebSocket `/ws/chart`: chart ticks.
- Polling `/api/market`: fallback and chart seed.
- Heavy `/api/dashboard`: boot/config/intelligence.

Realtime payloads use `dashboard.snapshot.v1`, monotonic `seq` ids, and SSE
patch fields: `eventsPatch` for event snapshots/deltas and `ordersPatch` for
order snapshot/upsert/remove operations.

### AI Sidecar

`ai_sidecar.py` can produce local AI decisions for review and optional engine
consumption. Configure it with an OpenAI-compatible local endpoint such as
Ollama. The AI sidecar must not be treated as a guarantee of profitable or safe
trading decisions.

### Telegram Control Bot

`control_bot.py` provides Telegram-based controls for approved operators.
Telegram bot tokens and admin IDs are sensitive operational configuration and
belong in local environment files or deployment secret storage.

## Requirements

- Python 3.11 is recommended for CI parity.
- Python 3.10 currently works in the local development environment.
- Git.
- Network access for dependency installation.
- Optional local services depending on what you run: Ollama-compatible AI
  endpoint, Telegram bot, dashboard access, and Binance testnet credentials.

## Setup

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Install runtime dependencies:

```bash
python -m pip install -r requirements.txt
```

Install development and test dependencies:

```bash
python -m pip install -r requirements-dev.txt
```

## Environment Variables

Start from the example file:

```bash
cp .env.example .env
```

Edit `.env` locally. Do not commit `.env` or any `.env.*` file containing real
values.

Important variables include:

```bash
BINANCE_BASE_URL=https://testnet.binance.vision
BINANCE_MARKETDATA_URL=https://api.binance.com
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_SYMBOL=BTCUSDT
```

Keep `BINANCE_API_KEY` and `BINANCE_API_SECRET` blank in `.env.example`. Put
real credentials only in local environment files or deployment secret storage.

Dashboard exposure deserves special care:

```bash
TRADEBOT_DASHBOARD_HOST=0.0.0.0
TRADEBOT_DASHBOARD_PORT=8844
TRADEBOT_DASHBOARD_TOKEN=
```

`TRADEBOT_DASHBOARD_HOST=0.0.0.0` exposes the dashboard to the network. Set
`TRADEBOT_DASHBOARD_TOKEN` if the dashboard is not localhost-only.

## Running Tests

```bash
source .venv/bin/activate
python -m pytest -q
```

Run coverage locally when you want a line-by-line test coverage report. Coverage
is informational only; the repository does not enforce a minimum percentage yet.

```bash
python3 -m coverage run -m pytest -q
python3 -m coverage report -m
```

## Running Smoke Checks

Run the Binance REST smoke check:

```bash
source .venv/bin/activate
python bot.py
```

Expected output includes:

- OK ping and server time.
- Authenticated response and balances count when credentials are configured.
- Klines OK and last close.

Run Python compilation as a quick syntax check:

```bash
python -m compileall -q .
```

## Running Local Services

Use the service orchestrator for normal local operations:

```bash
python dashboard_orchestrator.py start
python dashboard_orchestrator.py status
python dashboard_orchestrator.py stop
```

Run components directly when debugging:

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

The dashboard exposes named Ollama endpoints:

- Local: `http://127.0.0.1:11434/v1`
- Battlestation GPU: `http://192.168.1.20:11435/v1`
- Battlestation CPU: `http://192.168.1.20:11436/v1`

Example local AI configuration:

```bash
TRADEBOT_AI_BASE_URL=http://127.0.0.1:11434/v1
TRADEBOT_AI_PROVIDER=ollama
TRADEBOT_AI_MODEL=qwen3.5:9b
```

## Runtime Files and Logs

Runtime files are local artifacts and must not be committed. Examples include:

- `tradebot.sqlite3`
- `*.sqlite3-wal`
- `*.sqlite3-shm`
- `*.log`
- `*.pid`
- `*.nohup.out`
- `ai_signal.json`
- `ai_decisions.jsonl`
- `ai_memory.json`
- `cumulative.json`
- `dashboard_history.json`
- `engine_status.json`
- `runtime_state.json`
- `state.json`
- `trades.jsonl`

## Troubleshooting

- If `python -m pytest -q` fails because `pytest` is missing, install
  development dependencies with `python -m pip install -r requirements-dev.txt`.
- If `python3 -m pip` is unavailable on a system Python, use the project virtual
  environment after creating it with `python3 -m venv .venv`.
- If Binance authentication fails, confirm that credentials are present only in
  local `.env` files and that the base URL matches the intended testnet or live
  environment.
- If the dashboard is unreachable, check `TRADEBOT_DASHBOARD_HOST`,
  `TRADEBOT_DASHBOARD_PORT`, firewall rules, and whether the orchestrator or
  dashboard process is running.
- If the AI sidecar fails, confirm the local AI endpoint is reachable and that
  the configured model exists.

## Repository Structure

```text
.
|-- bot.py                         # Binance REST smoke check
|-- engine.py                      # Trading engine
|-- sqlite_store.py                # SQLite persistence helpers
|-- dashboard_server.py            # Dashboard HTTP/WebSocket/SSE server
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
`-- tests/                         # Test suite
```

## Security Note

Read [SECURITY.md](SECURITY.md) before configuring credentials or sharing this
repository. Real secrets must never be committed. Runtime databases, logs, JSONL
trade logs, PID files, and generated state files are local-only artifacts.
