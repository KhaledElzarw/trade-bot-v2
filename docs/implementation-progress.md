# Evolutionary Multi-Wallet Platform — Implementation Progress

Branch: `claude/evolutionary-multiwallet-rewrite` (from clean `main` @ `982a554`)

This log records **actual, evidence-backed** progress. A phase is marked complete only
when its acceptance gate passes with recorded command output.

| Phase | Title | Status | Evidence |
|-------|-------|--------|----------|
| 0 | Full local audit & baseline | **Done (audit gate)** | `docs/audits/evolutionary-platform-baseline.md`; baseline 403 pass / 11 fail / 98% cov / ruff 2 |
| 1 | Correct accounting & candle-fill defects (A01/A02) | **Core done** | `tradebot/domain/ledger.py`, `application/execution.py`; `test_ledger.py::test_a02_fee_not_double_counted_over_roundtrip`, `test_execution.py::test_a01_same_candle_cannot_fill_twice` |
| 2 | Package & configuration foundation | **Core done** | `tradebot/{domain,application,...}` skeleton; `tradebot/domain/money.py` Decimal primitives; `test_architecture.py` import-direction gate |
| 3 | Database v2 & legacy migration | **Schema/UoW core done; legacy import pending** | `infrastructure/database/models.py`, `unit_of_work.py`; `test_database.py` (5 tests: idempotent migration, FK unique idempotency key, check constraints, atomic rollback) |
| 4 | Shared market clock & execution simulator | **Core done** | `domain/market.py` immutable snapshot; `application/execution.py` filters + watermark + iteration-order independence (`test_execution.py`) |

### Phase 1/2/4 core — evidence (actual)
- `pytest tests/tradebot -q` → **21 passed**
- `ruff check tradebot tests/tradebot` → clean
- Full suite `pytest -q --ignore=tests/test_json_store.py` → **424 passed, 11 failed** (the same 11 pre-existing platform-specific failures from baseline; no new regressions)
- A02 proof: flat buy+sell at same price yields realized P&L of exactly `-(buy_fee+sell_fee)` — fees counted once.
- A01 proof: a second intent against the same `open_time_ms` returns `RejectReason.DUPLICATE_CANDLE`.

> Scope note: "Core done" means the **new canonical Decimal path** correctly implements the
> invariant with regression tests. Retrofitting the legacy float `engine.py` in place is
> deliberately superseded by this path (legacy remains the migration *source*, per Phase 3).

### Phase 5 — evidence (actual)
- `pytest tests/tradebot -q` → **45 passed** (19 new plugin tests)
- Real subprocess isolation verified: `python -I`, sanitized env (secret probe returns
  `ABSENT`), temp cwd, wall-clock timeout kill (infinite-loop strategy killed in ~3s),
  structured crash/malformed-output reporting, quarantine strike lifecycle.
- AST deny-by-default policy verified for all mandated forbidden modules/calls plus
  alias and reflection escapes; bundle limits (16 files / 256 KiB), traversal & symlink
  rejection, manifest schema (BTCUSDT-only) enforced.
- Windows limitation documented: POSIX rlimits unavailable; parent timeout is the backstop.
- Full suite: **448 passed, 11 failed** (same pre-existing platform failures; no regressions).

### Phase 6 — evidence (actual)
- 12 built-in strategies, each with its own `signal()` implementation and a distinct
  conceptual family (asserted by `test_strategies_are_materially_distinct`).
- Contract battery per strategy: shape, warmup guard, unclosed-candle guard,
  bit-deterministic replay, no-lookahead (62 tests).
- Strategy-specific signal tests: each characteristic entry fires on a crafted
  scenario; falling-knife veto, volume-confirmation rejection, chandelier stop,
  regime subpolicy attribution etc. (19 tests).
- Portfolio seeding: 12 active + 12 shadow + Dark Horse; active baseline exactly
  130,000.00 USDT with shadow 120,000.00 kept separate; display naming rule
  `StrategyName_DaysSinceStrategyChanged` with fixed `Dark Horse` (5 tests).
- 25-wallet shared-snapshot replay over 300 deterministic candles: fills occur,
  invariants hold, run is bit-reproducible, active/shadow ledgers for the same
  strategy evolve identically (fairness), wallet isolation proven (4 tests).
- New-package suite: **135 passed**; ruff clean; full suite **538 passed / same 11
  pre-existing failures**.
| 5 | Plugin SDK & isolation | **Done** | `domain/strategies.py` SDK; `plugins/{validator,worker,worker_main,registry}.py`; `docs/strategy-plugin-sdk.md`; 19 tests (AST policy, traversal/symlink/size limits, real subprocess timeout kill, env-sanitization leak probe, quarantine) |
| 6 | Initial 12 strategies & shadow pool | **Done** | `tradebot/strategies/` (12 modules + indicators + base); `application/portfolio.py`; 90 tests incl. 25-wallet deterministic replay, bit-reproducibility, active/shadow fairness, naming rule, 130k/120k split |
| 7 | DataBroker & local llama.cpp client | **Done** | `infrastructure/data_broker/{policy,client}.py`, `infrastructure/llm/llama_cpp_client.py`; `docs/data-broker.md`; 32 tests (allowlist, SSRF/rebinding/redirect/userinfo/port/mime/size, sanitization, schema-repair, degrade-not-raise) |
| 8 | Daily & weekly learning | Not started | — |
| 9 | Evolution, novelty & promotion | **Rules + atomic promotion done; novelty/lineage pending** | `domain/evaluations.py`, `application/{evolution,liquidation,promotion}.py`; `docs/evolution-policy.md`; 34 tests (all replacement scenarios, ban reuse, roll-forward, shortage rollback, invariants) |
| 10 | Dark Horse | Not started | — |
| 11 | API & dashboard rewrite | Not started | — |
| 12 | Operations, observability & CI | Not started | — |
| 13 | Independent verification & cleanup | Not started | — |

## Baseline metrics (Phase 0, actual)
- Tests: 403 passed, 11 failed, 1 collection error (`fcntl`), on Windows/Python 3.11.9
- Coverage: 98% (3635 stmts / 69 missed), `fail_under = 0`
- Ruff: 2 errors (select E/F, E501 ignored)
- No mypy / bandit / pip-audit / frontend gates in CI

## Notes
- No production source modified during Phase 0.
- venv created at `.venv` (untracked) for validation only.
