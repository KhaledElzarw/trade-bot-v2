# Tradebot Architecture

This document maps the current architecture before any structural refactor. It
is descriptive, not prescriptive runtime behavior.

## Current Architecture Overview

The repository is currently organized as a set of root-level Python modules
that share local runtime files, SQLite persistence, dashboard endpoints,
Telegram controls, and optional AI review flows.

The main operating shape is:

- `engine.py` owns the trading loop and engine runtime state.
- `sqlite_store.py` owns the canonical local SQLite store.
- JSON and JSONL files remain compatibility mirrors for legacy/runtime flows.
- `dashboard_server.py`, `dashboard_routes.py`, and dashboard static assets
  expose monitoring and selected control surfaces.
- `control_bot.py` exposes Telegram operator controls.
- `ai_sidecar.py` produces optional AI decisions and review context.
- Wrapper scripts and `dashboard_orchestrator.py` start, stop, and inspect
  local services.

## Main Entry Points

- `bot.py`: Binance REST smoke check.
- `engine.py`: trading engine entry point.
- `dashboard_server.py`: dashboard server entry point.
- `control_bot.py`: Telegram control bot entry point.
- `ai_sidecar.py`: AI sidecar entry point.
- `ai_playground.py`: one-off AI review helper.
- `dashboard_orchestrator.py`: grouped service start/stop/status commands.
- `run_engine_detached.py`: detached engine wrapper.
- `run_dashboard_detached.py`: detached dashboard wrapper.
- `run_ai_sidecar_detached.py`: detached AI sidecar wrapper.
- `migrate_to_sqlite.py`: migration/backfill into the SQLite runtime store.

## Core Engine Responsibilities

`engine.py` is responsible for:

- Reading environment and runtime configuration.
- Maintaining the engine main loop.
- Reading market/account inputs through existing adapters.
- Managing grid state and paper/testnet trading state.
- Reading optional AI signal inputs.
- Applying existing strategy and risk decision logic.
- Writing status, runtime state, and event outputs.
- Recording trade/accounting events through local persistence paths.

The engine is the highest-risk module because it is closest to trading
decisions and exchange interaction. Refactors around this module must preserve
behavior unless a later task explicitly authorizes a behavior change.

## Persistence Model

### SQLite Canonical Store

SQLite is the canonical local runtime store. The default database file is
`tradebot.sqlite3`, with WAL and SHM sidecar files created by SQLite when WAL
mode is active.

`sqlite_store.py` centralizes current SQLite helpers for snapshots, events,
history, AI strategy records, and related runtime persistence.

### JSON Compatibility Mirrors

JSON and JSONL files remain compatibility mirrors for legacy/runtime flows and
simple operator inspection. They are runtime artifacts, not source files.

Examples include:

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

Future refactors should reduce mixed source/runtime concerns carefully, but not
by changing persistence behavior in the same commit as structural movement.

## Dashboard and Control Surface Responsibilities

The dashboard stack is responsible for:

- Serving the browser dashboard.
- Rendering initial dashboard HTML.
- Exposing read endpoints for status, runtime, market, history, orders, and AI
  context.
- Exposing protected mutation endpoints for selected controls/configuration.
- Streaming runtime updates through SSE.
- Streaming or serving chart data through WebSocket and polling fallbacks.
- Coordinating dashboard-specific state normalization and payload contracts.

Current dashboard-related files include:

- `dashboard_server.py`
- `dashboard_routes.py`
- `dashboard_contracts.py`
- `dashboard_data.py`
- `dashboard_orchestrator.py`
- `dashboard/static/dashboard.v1.js`
- `dashboard/static/dashboard.v1.css`

Dashboard refactors must avoid changing the control contract or exposing
mutation endpoints without token controls.

## AI Sidecar Responsibilities

The AI sidecar is responsible for:

- Building AI prompt payloads from current runtime context.
- Loading prompt templates.
- Calling local or OpenAI-compatible AI endpoints when enabled.
- Producing AI reports and portfolio decisions through schema validation.
- Writing current AI signal/runtime outputs.
- Maintaining AI decision, memory, and review artifacts.

AI output must remain advisory unless existing engine configuration explicitly
uses it. Refactors should not silently change AI enablement, stale-signal
handling, model selection, prompt semantics, or engine consumption behavior.

## Telegram Control Bot Responsibilities

The Telegram control bot is responsible for:

- Providing operator commands through Telegram.
- Reading runtime state and status summaries.
- Sending pause/resume/panic/configuration commands through existing local
  control paths.
- Restricting access to configured admin users.
- Reporting summaries and recent trade/activity information.

Telegram tokens and admin identifiers are sensitive local configuration and
belong outside Git.

## Runtime Artifacts and Where They Should Live

Runtime artifacts should live in the local runtime environment, not in source
control. In production-like operation, configure paths so durable runtime files
are stored in a private local volume or deployment-managed data directory.

Local-only runtime artifacts include:

- `.env`
- `.env.*`
- `tradebot.sqlite3`
- `*.sqlite3-wal`
- `*.sqlite3-shm`
- `*.log`
- `*.pid`
- `*.nohup.out`
- JSON and JSONL runtime mirrors.
- `.venv`
- `__pycache__`
- `.pytest_cache`

The repository should only track placeholder examples, source code, tests,
documentation, and CI/configuration files that do not contain secrets or live
runtime data.

## Known Architecture Debt

Current known debt:

- Large root modules combine orchestration, data shaping, runtime I/O, and
  business behavior.
- Some helper functions are duplicated across engine, dashboard, sidecar, and
  wrapper modules.
- Source files and runtime files historically lived side by side in the repo
  root, which increases accidental tracking risk.
- Dashboard server responsibilities are broad.
- AI prompt, schema, memory, and sidecar responsibilities are still closely
  coupled.
- Legacy Baserow sync/export scripts have been removed; SQLite is the supported
  local runtime store.
- Service wrappers and orchestration have overlapping process-management
  concerns.

These are structural concerns, not authorization to change behavior.

## Future Target Structure

A future structure may separate source, tests, documentation, and runtime
artifacts more clearly. One possible target is:

```text
.
|-- src/tradebot/
|   |-- engine/
|   |-- dashboard/
|   |-- ai/
|   |-- telegram/
|   |-- persistence/
|   |-- integrations/
|   `-- runtime/
|-- tests/
|-- docs/
|-- scripts/
|-- requirements.txt
|-- requirements-dev.txt
`-- .env.example
```

This is a direction for future planning only. A `src/` migration should not
happen until import boundaries, entry points, packaging, runtime paths, and
deployment/service wrappers are confirmed.

## Refactor Safety Principles

- One commit should have one purpose.
- Prefer documentation, tests, and CI before structural moves.
- Preserve current behavior unless a task explicitly asks for a behavior
  change.
- Add characterization tests before moving sensitive behavior.
- Keep trading strategy behavior and order execution behavior isolated from
  hygiene refactors.
- Avoid mixing file moves with logic edits.
- Avoid changing runtime file paths and process behavior in the same commit.
- Keep secrets and runtime artifacts out of diffs, logs, tests, and examples.
- Validate with tests and compile checks before committing.

## Do-Not-Touch-Yet List

Do not change these areas during foundation modernization unless a later task
explicitly authorizes it:

- Trading strategy behavior.
- Order execution behavior.
- Engine main loop.
- `src/` migration.
- Legacy engine moves until confirmed.
- Exchange API semantics.
- AI decision semantics consumed by the engine.
- Dashboard mutation/control contract.
- Runtime persistence semantics.
- Live credential handling beyond documentation and guardrails.
