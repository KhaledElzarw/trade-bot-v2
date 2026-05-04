# Tradebot Operations Runbook

This runbook describes safe local operation for the tradebot repository. It is
documentation only; operational behavior is controlled by the application code
and local environment configuration.

Trading automation is high risk. Start with smoke tests and testnet or paper
flows before using any configuration that can affect live funds.

## Operating Modes

### Smoke Test

Use the smoke test to verify Python dependencies, Binance connectivity, server
time, authentication, balances, and market data without starting long-running
services:

```bash
source .venv/bin/activate
python bot.py
```

Fill in local Binance credentials in `.env` before running authenticated smoke
checks. Do not commit `.env`.

### Paper/Testnet

The default examples are testnet-oriented. Confirm the intended exchange base
URL, symbol, API key permissions, and runtime database path before starting the
engine. Testnet and paper-style operation reduce risk, but they do not prove
that live trading is safe.

Initialize or backfill local runtime storage when needed:

```bash
python migrate_to_sqlite.py
```

The dashboard freshness path uses SQLite in WAL mode. Set `TRADEBOT_DB_PATH`
only when you want a non-default local runtime database path.

### Dashboard

The dashboard provides local visibility and selected control surfaces.

Default local URL:

```text
http://localhost:8844/
```

When opening from Codex's in-app browser, use the machine address if `localhost`
resolves to an isolated webview namespace:

```text
http://192.168.1.21:8844/
```

Set `TRADEBOT_DASHBOARD_TOKEN` to protect write endpoints. When set, open the
dashboard with:

```text
http://192.168.1.21:8844/?token=<dashboard-token>
```

Read endpoints remain available for monitoring. Mutations to `/api/control` and
`/api/config` require the token.

Realtime contract:

- `/api/dashboard`: heavy boot/config/intelligence snapshot.
- `/api/market`: polling fallback for light status/runtime/events and optional
  chart seed.
- `/api/live/events`: SSE for engine/runtime/status/events only.
- `/ws/chart`: websocket for chart ticks only.

Every realtime payload carries `schemaVersion`, `channel`, `seq`, and
`serverTimeUtc`; the browser ignores stale out-of-order sequence ids.
Intelligence/news is cached separately so slow feeds do not block chart/status
freshness.

SSE sends compact panel data:

- First frame: `eventsPatch.mode=snapshot` and `ordersPatch.mode=snapshot`.
- Later frames: `eventsPatch.mode=delta` with new events after the last event
  cursor, and `ordersPatch.mode=delta` with order `upsert`/`remove` operations.
- Polling fallback endpoints still return complete snapshots so recovery is
  simple.

### AI Sidecar

The AI sidecar uses a local OpenAI-compatible endpoint by default. For Ollama,
set the base URL to your local `/v1` endpoint:

```text
TRADEBOT_AI_BASE_URL=http://127.0.0.1:11434/v1
TRADEBOT_AI_MODEL=qwen3.5:9b
```

The dashboard can pause/resume AI assist, switch the provider/base URL, and
choose quick/deep/fallback models. It includes these named Ollama endpoints:

- Local: `http://127.0.0.1:11434/v1`
- Battlestation GPU: `http://192.168.1.20:11435/v1`
- Battlestation CPU: `http://192.168.1.20:11436/v1`

Dry-run and shadow decisions are displayed and logged but not enforced by the
engine.

Run a one-off review without changing the active signal:

```bash
python ai_playground.py
```

Write one review as the current engine signal:

```bash
python ai_playground.py --write-signal
```

### Telegram Control Bot

The Telegram control bot provides operator commands through Telegram. Configure
the bot token and admin user ID in local environment files only. Do not commit
Telegram tokens, screenshots that reveal tokens, or exported chat logs with
operational secrets.

Run directly only when debugging:

```bash
python control_bot.py
```

## Startup Commands

Use the service orchestrator for normal local operations so the engine,
dashboard, and AI sidecar move together:

```bash
source .venv/bin/activate
python dashboard_orchestrator.py start
```

Run individual components directly only for debugging:

```bash
python engine.py
python dashboard_server.py
python control_bot.py
python ai_sidecar.py
```

The older detached wrappers still exist as implementation details, but
operators should prefer `dashboard_orchestrator.py`.

## Shutdown Commands

Stop services explicitly through the orchestrator:

```bash
python dashboard_orchestrator.py stop
```

Use direct process stops only when you have confirmed which process is running
and why the orchestrator is not suitable.

## Status Checks

Check orchestrated services:

```bash
python dashboard_orchestrator.py status
```

Check detached wrapper status when diagnosing stale processes:

```bash
python run_dashboard_detached.py status
python run_engine_detached.py status
```

Check dashboard response:

```bash
curl -s http://localhost:8844/api/dashboard
```

Inspect local process state without printing secrets:

```bash
ps -ef | grep -E 'engine.py|dashboard_server.py|ai_sidecar.py|control_bot.py'
```

## Runtime Files

Runtime files are local artifacts, not source code. They are ignored by Git and
must not be committed.

### SQLite DB

SQLite is the canonical operational store:

- `tradebot.sqlite3`
- `tradebot.sqlite3-wal`
- `tradebot.sqlite3-shm`

### JSON Compatibility Mirrors

The JSON/JSONL files below are maintained as compatibility mirrors during the
SQLite cutover and can be used as rollback input by re-running
`python migrate_to_sqlite.py` against a fresh database:

- `state.json`
- `state_trend.json`
- `runtime_state.json`
- `engine_status.json`
- `engine_status_trend.json`
- `cumulative.json`
- `cumulative_trend.json`
- `trades.jsonl`
- `trades_trend.jsonl`
- `ai_signal.json`
- `ai_decisions.jsonl`
- `ai_memory.json`
- `dashboard_history.json`

### Logs

Logs are local runtime artifacts:

- `advisor.log`
- `engine.log`
- `engine_trend.log`
- `*.nohup.out`

### PID Files

PID files are local process markers:

- `dashboard.pid`
- `engine.pid`
- `ai_sidecar.pid`
- `*.pid`

## Backup Guidance

Back up runtime state before upgrades, migrations, or recovery work:

1. Stop services with `python dashboard_orchestrator.py stop`.
2. Copy `tradebot.sqlite3` and any `tradebot.sqlite3-wal` or
   `tradebot.sqlite3-shm` files to a private backup location.
3. Copy JSON/JSONL compatibility mirrors if you need rollback context.
4. Keep backups out of Git and outside public ZIP archives.
5. Record backup time, branch, and commit hash without recording secret values.

## Recovery Guidance

If the dashboard shows stale data:

1. Run `python dashboard_orchestrator.py status`.
2. Check `curl -s http://localhost:8844/api/dashboard`.
3. If the shell endpoint is live but the in-app browser is stale, switch the
   browser to the machine address shown by `hostname -I`.
4. Inspect logs locally without pasting secret values into issues or commits.
5. Restart intentionally only after confirming whether the engine, dashboard,
   and AI sidecar are already running.

If pid files are stale, use `status` to inspect and `restart` only when you
intentionally want to replace the process.

## How To Rotate Secrets

Rotate secrets immediately if they were committed, logged, shared in a ZIP, or
shown in screenshots:

1. Revoke the exposed Binance API key, Telegram bot token, dashboard token, or
   other affected credential at the provider.
2. Create a replacement credential with minimum required permissions.
3. Update local `.env` files and deployment secret storage.
4. Restart affected services.
5. Verify status without printing secret values.
6. Review recent exchange, Telegram, and dashboard activity for unexpected use.

## Troubleshooting Failed Tests

Run the test suite from the project virtual environment:

```bash
source .venv/bin/activate
python -m pytest -q
```

If `pytest` is missing, install development dependencies:

```bash
python -m pip install -r requirements-dev.txt
```

If `python3 -m pip` is unavailable on the system Python, use the virtual
environment created by:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

If a hygiene test fails, review the listed tracked file paths. Do not print or
paste file contents if the path may contain secrets or runtime state.

## Troubleshooting Missing Dashboard, Control Bot, or AI Sidecar

For the dashboard:

- Check `python dashboard_orchestrator.py status`.
- Check `TRADEBOT_DASHBOARD_HOST` and `TRADEBOT_DASHBOARD_PORT`.
- Confirm firewall or network rules if using a non-localhost host.
- Set `TRADEBOT_DASHBOARD_TOKEN` when the dashboard is not localhost-only.

For the Telegram control bot:

- Confirm the local environment contains a bot token and admin user ID.
- Confirm the running process is `control_bot.py`.
- Check network access to Telegram without printing token values.

For the AI sidecar:

- Confirm the local AI endpoint is reachable.
- Confirm `TRADEBOT_AI_BASE_URL` points to the endpoint `/v1` path.
- Confirm the configured model exists locally.
- Run `python ai_playground.py` for a one-off review.

## What Not To Commit

Never commit:

- Real secrets or local `.env` files.
- Binance API keys or Telegram bot tokens.
- Dashboard tokens.
- SQLite databases and WAL/SHM files.
- JSON/JSONL runtime state and trade logs.
- Logs, PID files, and `*.nohup.out` files.
- `.venv`, `__pycache__`, `.pytest_cache`, and other cache folders.
- Screenshots, exports, or ZIP files that contain secrets or runtime state.

## Safe Upgrade Process

Use a conservative upgrade process:

1. Confirm the working tree is clean or intentionally preserved on another
   branch or stash.
2. Back up runtime files.
3. Review dependency, migration, and configuration changes before running
   services.
4. Install dependencies in a virtual environment.
5. Run `python -m pytest -q`.
6. Run `python -m compileall -q .`.
7. Run `python bot.py` against the intended testnet or smoke-test environment.
8. Start services with `python dashboard_orchestrator.py start`.
9. Check dashboard, engine, AI sidecar, and Telegram control status.
10. Monitor logs and runtime state before considering live-risk operation.

## Deprecated Baserow Path

Baserow is no longer used for live engine/dashboard freshness. The Baserow
scripts remain available only for manual legacy export/cleanup work, and
`TRADEBOT_BASEROW_SYNC` should stay disabled unless explicitly required for a
legacy task.
