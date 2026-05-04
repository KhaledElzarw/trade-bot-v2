# Tradebot Operations Runbook

## Operating principles

This runbook describes local operation for the tradebot repository. It does not
change application behavior, runtime paths, trading strategy, or execution
logic.

Trading automation is high risk. Start with smoke tests and paper/testnet
operation before considering any live-risk workflow. Do not treat a successful
testnet, paper, dry-run, dashboard, Telegram, or AI sidecar workflow as proof
that live trading is safe.

Core operating rules:

- Keep real secrets in local `.env` files or deployment secret storage only.
- Do not commit runtime files, logs, PID files, SQLite databases, or JSON/JSONL
  mirrors.
- Prefer the orchestrator for normal local service start, stop, and status.
- Use direct component commands only for debugging.
- Back up runtime state before upgrades, migration work, or recovery work.
- Do not paste secret values into issues, commits, logs, screenshots, or ZIP
  archives.

## Operating modes

### Smoke test

Use the smoke test to verify Python dependencies, Binance connectivity, server
time, authentication, balances, and market data without starting long-running
services:

```bash
source .venv/bin/activate
python bot.py
```

Authenticated checks require local Binance credentials in `.env`. Do not commit
`.env`.

### Paper/testnet

The repository examples are testnet-oriented. Confirm the intended exchange base
URL, symbol, API key permissions, and local runtime database path before
starting the engine.

Optional/manual storage migration or backfill command:

```bash
python migrate_to_sqlite.py
```

This command writes local runtime storage. Run it only when intentionally
initializing, migrating, or backfilling the local SQLite store.

### Dashboard

The dashboard provides local visibility and selected control surfaces.

Default local URL:

```text
http://localhost:8844/
```

Set `TRADEBOT_DASHBOARD_TOKEN` when the dashboard is not localhost-only. When a
dashboard token is configured, use a placeholder-style URL like:

```text
http://localhost:8844/?token=<dashboard-token>
```

Do not expose the dashboard publicly without authentication/token controls.
Dashboard write endpoints are sensitive operational controls.

### AI sidecar

The AI sidecar uses a local OpenAI-compatible endpoint by default. Example
placeholder configuration:

```text
TRADEBOT_AI_BASE_URL=http://127.0.0.1:11434/v1
TRADEBOT_AI_PROVIDER=ollama
TRADEBOT_AI_MODEL=qwen3.5:9b
```

AI decisions are advisory unless existing engine configuration explicitly
consumes them. Do not treat AI output as a guarantee of safe or profitable
trading.

One-off review command:

```bash
python ai_playground.py
```

Optional/manual command that writes the current engine signal:

```bash
python ai_playground.py --write-signal
```

Run the write-signal command only when you intentionally want to update local AI
signal runtime state.

### Telegram control bot

The Telegram control bot provides operator commands through Telegram. Configure
the bot token and admin user ID in local environment files or deployment secret
storage only.

Direct debug command:

```bash
python control_bot.py
```

Do not commit Telegram tokens, screenshots that reveal tokens, or exported chat
logs with operational secrets.

## Startup commands

Use the service orchestrator for normal local operation:

```bash
source .venv/bin/activate
python dashboard_orchestrator.py start
```

This starts local services and may write logs, PID files, SQLite state, and
JSON/JSONL runtime mirrors.

Run individual components directly only when debugging:

```bash
python engine.py
python dashboard_server.py
python control_bot.py
python ai_sidecar.py
```

## Shutdown commands

Stop orchestrated services explicitly:

```bash
python dashboard_orchestrator.py stop
```

Use manual process termination only after confirming which process is running
and why the orchestrator is not suitable.

## Status checks

Check orchestrated services:

```bash
python dashboard_orchestrator.py status
```

Check detached wrapper status while diagnosing stale processes:

```bash
python run_engine_detached.py status
python run_dashboard_detached.py status
python run_ai_sidecar_detached.py status
```

Check dashboard response:

```bash
curl -s http://localhost:8844/api/dashboard
```

Inspect local processes without printing secrets:

```bash
ps -ef | grep -E 'engine.py|dashboard_server.py|ai_sidecar.py|control_bot.py'
```

## Runtime files

Runtime files are local-only artifacts. They are not source code and must not be
committed. See [docs/runtime-artifacts.md](docs/runtime-artifacts.md) for the
full runtime artifact policy.

### SQLite DB

SQLite is the canonical local runtime store:

- `tradebot.sqlite3`
- `tradebot.sqlite3-wal`
- `tradebot.sqlite3-shm`

### JSON compatibility mirrors

JSON and JSONL files remain compatibility mirrors for selected runtime flows and
recovery/migration workflows:

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

### PID files

PID files are local process markers:

- `dashboard.pid`
- `engine.pid`
- `ai_sidecar.pid`
- `*.pid`

## Backup guidance

Back up local runtime state before upgrades, migration work, cleanup, or
recovery:

1. Stop services when possible with `python dashboard_orchestrator.py stop`.
2. Copy `tradebot.sqlite3` and matching WAL/SHM files when present.
3. Copy JSON/JSONL mirrors if they are needed for rollback or comparison.
4. Copy logs only when needed for troubleshooting or audit.
5. Store backups outside Git in a private local or deployment-managed location.
6. Record branch, commit hash, timestamp, and reason for the backup without
   recording secret values.

## Restore/recovery guidance

Restore local runtime state carefully:

1. Stop services.
2. Back up the current runtime files before replacing anything.
3. Restore the intended SQLite database and matching WAL/SHM files if needed.
4. Restore JSON/JSONL mirrors only when they are part of the intended recovery.
5. Start services.
6. Run status checks.
7. Review logs locally without printing secret values into public channels.

If the dashboard appears stale, check orchestrator status, dashboard response,
and local logs before restarting services.

## Secret rotation

Rotate secrets immediately if they were committed, logged, screenshotted, shared
in a ZIP, or exposed outside the trusted operating environment:

1. Revoke the exposed credential at the provider.
2. Create a replacement credential with minimum required permissions.
3. Update local `.env` files and deployment secret storage.
4. Restart affected services.
5. Verify status without printing secret values.
6. Review recent exchange, Telegram, and dashboard activity for unexpected use.

## Troubleshooting failed tests

Run tests from the project virtual environment:

```bash
source .venv/bin/activate
python -m pytest -q
```

If `pytest` is missing, install development dependencies:

```bash
python -m pip install -r requirements-dev.txt
```

If `python3 -m pip` is unavailable on the system Python, create and activate a
virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

If a hygiene test fails, review only the listed file paths first. Do not print
or paste file contents when the path may contain secrets or runtime state.

## Troubleshooting dashboard

- Check `python dashboard_orchestrator.py status`.
- Check `TRADEBOT_DASHBOARD_HOST` and `TRADEBOT_DASHBOARD_PORT`.
- Confirm firewall or network rules if using a non-localhost host.
- Set `TRADEBOT_DASHBOARD_TOKEN` when the dashboard is not localhost-only.
- Check `curl -s http://localhost:8844/api/dashboard`.
- Review dashboard logs locally without printing secret values.

## Troubleshooting Telegram control bot

- Confirm the local environment contains a bot token and admin user ID.
- Confirm the running process is `control_bot.py`.
- Confirm network access to Telegram.
- Review logs locally without printing token values.
- Rotate the bot token if it may have been exposed.

## Troubleshooting AI sidecar

- Confirm the local AI endpoint is reachable.
- Confirm `TRADEBOT_AI_BASE_URL` points to the endpoint `/v1` path.
- Confirm `TRADEBOT_AI_PROVIDER` and `TRADEBOT_AI_MODEL` match the local
  provider and installed model.
- Run `python ai_playground.py` for a one-off review.
- Review AI runtime files locally without committing them.

## What not to commit

Never commit:

- Real secrets or local `.env` files.
- Binance API keys or Telegram bot tokens.
- Dashboard tokens.
- SQLite databases and WAL/SHM files.
- JSON/JSONL runtime state and trade logs.
- Logs, PID files, and `*.nohup.out` files.
- `.venv`, `__pycache__`, `.pytest_cache`, and other cache folders.
- Screenshots, exports, or ZIP files that contain secrets or runtime state.

## Safe upgrade process

Use a conservative upgrade process:

1. Confirm the working tree is clean or intentionally preserved on another
   branch or stash.
2. Back up runtime files.
3. Review dependency, migration, configuration, and documentation changes before
   running services.
4. Install dependencies in a virtual environment.
5. Run `python -m pytest -q`.
6. Run `python -m compileall -q .`.
7. Run `python bot.py` against the intended smoke-test environment.
8. Start services with `python dashboard_orchestrator.py start`.
9. Check dashboard, engine, AI sidecar, and Telegram control status.
10. Monitor logs and runtime state before considering any live-risk operation.

## Removed Baserow path

Legacy Baserow tooling has been removed. There are no supported Baserow
environment variables, scripts, or operator commands in this repository. Use
the SQLite runtime store and `migrate_to_sqlite.py` for supported local storage
workflows.
