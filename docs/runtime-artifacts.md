# Runtime Artifact Policy

This policy defines what belongs in source control and what belongs in local
runtime storage. It does not change current runtime paths or move any files.

## Source Files vs Runtime Files

Source control should contain:

- Application source code.
- Tests.
- Documentation.
- CI configuration.
- Placeholder-only examples such as `.env.example`.
- Dependency manifests.

Source control should not contain:

- Real secrets.
- Local environment files.
- SQLite databases.
- Logs.
- PID files.
- Runtime JSON/JSONL state.
- Local backups and archives.
- Virtual environments and cache folders.

Runtime files are produced by local operation. They may contain account state,
trade history, dashboard state, AI output, operational logs, or local secrets.
They must stay in local runtime storage, private backups, or deployment-managed
volumes.

## SQLite DB Files

SQLite files are local runtime artifacts:

- `*.sqlite3`
- `*.sqlite3-wal`
- `*.sqlite3-shm`

The default local database is currently `tradebot.sqlite3`. WAL and SHM files
may be created by SQLite while the database is active. These files should not be
committed or shared in public archives.

## Logs

Logs are local runtime artifacts:

- `*.log`
- `*.nohup.out`

Logs may include operational decisions, endpoint errors, account context,
runtime paths, or other sensitive details. Keep logs private unless they have
been reviewed and redacted.

## PID Files

PID files are local process markers:

- `*.pid`

PID files are machine-specific and should never be committed.

## JSON/JSONL Runtime Mirrors

JSON and JSONL runtime mirrors are local artifacts. Current examples include:

- `state.json`
- `runtime_state.json`
- `trades.jsonl`
- `ai_decisions.jsonl`
- `dashboard_history.json`
- `dashboard_intelligence.json` - generated local dashboard intelligence cache
- `engine_status.json`
- `cumulative.json`
- `ai_signal.json`
- `ai_memory.json`

Legacy local artifacts from removed workflows also remain ignored:

- `advisor.log` - legacy advisor/flexy workflow log
- `grid_honest_replay*.json` - legacy generated honest replay outputs; ignored
  to avoid surfacing old local files after replay script removal
- `state_trend.json` - legacy trend engine mirror
- `engine_status_trend.json` - legacy trend engine mirror
- `cumulative_trend.json` - legacy trend engine mirror
- `trades_trend.jsonl` - legacy trend engine mirror
- `engine_trend.log` - legacy trend engine log

Runtime mirrors and retained legacy artifacts may be useful for compatibility,
inspection, recovery, or migration work, but they are not source files.

## Local Config

Local environment files are private configuration:

- `.env`
- `.env.*`

`.env.example` is the tracked template and must contain placeholders only. Do
not commit real Binance keys, dashboard tokens, local service URLs, account
identifiers, or production configuration values.

## Recommended Future Layout

The repository currently keeps some runtime paths in the repo root for
compatibility. A future behavior-preserving migration can separate runtime data
from source more clearly:

```text
var/
|-- runtime/
|   |-- tradebot.sqlite3
|   |-- state.json
|   |-- runtime_state.json
|   `-- dashboard_history.json
|-- logs/
|   |-- engine.log
|   |-- dashboard.nohup.out
|   `-- ai_sidecar.nohup.out
`-- archive/
    |-- accounting_archive_YYYY-MM-DD/
    `-- manual-backups/
```

Recommended future directories:

- `var/runtime/` for active SQLite, JSON, and JSONL runtime state.
- `var/logs/` for logs and detached process output.
- `var/archive/` for private local backups and accounting archives.

Do not move runtime paths until code, wrappers, tests, deployment scripts, and
operator workflows are ready.

## Backup Guidance

Before upgrades, migrations, cleanup, or recovery work:

1. Stop local services when possible.
2. Copy SQLite files, including WAL and SHM sidecars when present.
3. Copy JSON/JSONL mirrors if they are needed for recovery or comparison.
4. Copy logs only when needed for troubleshooting or audit.
5. Store backups outside Git in a private local or deployment-managed location.
6. Do not include secrets or runtime artifacts in public ZIP files.
7. Record the branch, commit hash, timestamp, and reason for the backup without
   recording secret values.

## Restore Guidance

To restore local runtime state safely:

1. Stop local services.
2. Back up the current runtime files before replacing them.
3. Restore the intended SQLite database and matching WAL/SHM files if needed.
4. Restore JSON/JSONL mirrors only if they are part of the intended recovery.
5. Start services.
6. Check status and dashboard health.
7. Review logs locally without printing secret values into issues, commits, or
   support messages.

## Git Hygiene Commands

Inspect tracked files:

```bash
git ls-files
```

Search tracked files for runtime-looking paths:

```bash
git ls-files | grep -E '(^\\.env|\\.sqlite3|\\.sqlite3-wal|\\.sqlite3-shm|\\.log$|\\.pid$|\\.nohup\\.out$|\\.jsonl$)'
```

If a local runtime file was accidentally staged, unstage it:

```bash
git restore --staged path/to/runtime-file
```

If a runtime file was accidentally committed and should stop being tracked in a
future cleanup commit, use `git rm --cached` examples like these, but do not run
them without confirming the file is truly a runtime artifact:

```bash
git rm --cached tradebot.sqlite3
git rm --cached engine.log
git rm --cached trades.jsonl
git rm --cached .env
```

After removing a tracked runtime artifact from Git, rotate any exposed secrets
and keep the local file in private runtime storage.
