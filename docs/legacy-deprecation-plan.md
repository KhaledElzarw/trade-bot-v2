# Legacy Workflow Deprecation Plan

This document records the intentional deprecation direction for non-core
workflows. It is documentation only. It does not authorize file deletion, file
movement, import changes, runtime path changes, trading strategy changes, order
execution changes, or engine main loop changes.

## Core Workflows To Preserve

- `engine.py` main grid engine.
- Dashboard server, routes, contracts, and static assets.
- SQLite persistence through `sqlite_store.py`.
- AI sidecar behavior.
- `migrate_to_sqlite.py` until a replacement migration/backfill path is agreed.
- Tests, CI, and runtime artifact policy.

## Deprecated Non-Core Workflows

| Workflow | Files | Current status | Removal phase |
|---|---|---|---|
| Research replay scripts | `grid_engine_honest.py`, `grid_engine_honest_v2.py` | Deprecated research/backtest scripts; no supported operations invocation found | Phase 1 after owner confirmation |
| Telegram control bot | `control_bot.py` | Deprecated legacy operator control surface; retained for compatibility | Phase 2 after operator/service-manager confirmation |
| Baserow tooling | Removed: `baserow_sync.py`, `migrate_to_baserow.py`, `clean_baserow_tradebot_db.py`, `prune_tradebot_order_grid_rows.py` | Removed legacy sync/export/cleanup tooling; no supported Baserow env settings remain | Complete |
| Alternate trend engine | `engine_trend.py` | Deprecated alternate engine workflow; retained until manual usage is confirmed absent | Phase 3 |
| Advisor/flexy workflow | `advisor.py`, `gridMode=flexy` | Deprecated legacy advisor workflow; `flexy` remains active behavior for now | Phase 4 after characterization and explicit behavior-change approval |

## Do Not Remove Yet

- Do not remove `migrate_to_sqlite.py`.
- Do not remove `gridMode=flexy` from `engine.py` or dashboard validation in this
  documentation phase.
- Do not remove Telegram keys from `.env.example` until tests, docs, and runtime
  notification ownership are updated together.
- Baserow keys have been removed from `.env.example` with the legacy Baserow
  tooling.
- Do not remove `python-telegram-bot` or `pandas` from requirements until the
  owning scripts are removed or replaced.

## Suggested Removal Order

1. Confirm and remove or quarantine research replay scripts.
2. Legacy Baserow scripts and their dedicated tests/env docs have been removed.
3. Confirm and remove Telegram control bot documentation, dependency, and entry
   point.
4. Confirm alternate trend engine is unused, then remove trend-specific docs and
   artifacts.
5. Characterize current `flexy` behavior, then remove advisor/flexy only in an
   explicitly behavior-authorized branch.
