# Testing & Coverage

## Principles

- **No real external calls.** No Binance, FRED, BLS, BEA, SEC, Coin Metrics,
  news, mempool, CoinGecko, or local-model traffic in the suite. Transports are
  injected; fakes are used everywhere.
- **No live daemons, no real runtime paths, no real database.** Temporary
  SQLite files and `tmp_path` only. CI Gate 8 asserts the working tree is
  unchanged after the suite runs.
- **Deterministic.** Injected clock, ID factory, and seeds. Replays are
  bit-reproducible.

## Honest current numbers (measured 2026-07-17, Windows/Python 3.11.9)

| Metric | Value |
|--------|-------|
| New `tradebot` package tests | **402 passed** |
| Full repository suite | **805 passed, 11 failed** |
| `tradebot` package coverage | **97%** (3018 stmts, 103 missed) |
| Ruff | clean (`tradebot` + `tests/tradebot`) |
| Mypy | clean (62 source files) |
| Bandit | 0 issues |
| pip-audit | no known vulnerabilities |

### The 11 failures are pre-existing, not regressions

They were present at the Phase-0 baseline (403 passed / 11 failed) and are all
platform-specific legacy issues on Windows (signal/PID semantics in
`wrapper_runner`, dashboard helper paths). See
`docs/audits/evolutionary-platform-baseline.md` §4. The new package added
**zero** new failures across every phase.

`tests/test_json_store.py` cannot even be collected on Windows because
`json_store.py` imports POSIX-only `fcntl` (finding **A27**). It collects and
runs on Linux/CI.

### Coverage: the gap to 100%

The spec targets `fail_under = 100`. The honest position:

- **CI Gate 2 enforces `--fail-under=97` on `tradebot/*`** — the measured
  current floor, set as a **ratchet**. Raise it, never lower it. Claiming 100%
  today would be false.
- **103 statements remain uncovered**, concentrated in defensive branches of
  the API/CLI adapters and a few strategy guard paths. They are reachable and
  therefore *should* be tested; this is tracked work, not a permanent exclusion.
- The repo-wide `fail_under` stays at the legacy baseline (`0`) because the
  legacy flat modules are slated for removal in Phase 13 rather than
  retro-fitted with tests.

### The one documented omission

`tradebot/infrastructure/adapters/plugins/worker_main.py` is omitted from
coverage. It executes **only inside the isolated sandbox** (`python -I -E`,
sanitized environment), so the parent process cannot observe its coverage.
Injecting a coverage hook would require re-introducing environment inheritance
into the sandbox — defeating the isolation guarantee the module exists to
provide. Its behaviour *is* exercised end-to-end by
`tests/tradebot/test_plugin_worker.py`, which spawns real subprocesses and
asserts on success, state round-trip, timeout kill, crash reporting, and an
environment-leak probe. This is a measurement limitation, not untested code.

No new `# pragma: no cover` was added to application logic.

## CI gates

| Gate | Enforces |
|------|----------|
| 1 — hygiene | no tracked runtime artifacts, databases, PID/log files, `.env`, or generated bundles; whitespace/conflict markers |
| 2 — correctness | ruff, mypy, compileall, full suite, coverage ratchet on `tradebot/*` |
| 3 — security | bandit, pip-audit, plugin isolation + SSRF + API security suites, secret-pattern scan |
| 4 — database | migration idempotency/rollback, FK integrity, promotion fault injection |
| 5 — frontend | `node --check` on v1 and v2, DOM-sink and URL-vetting tests |
| 6 — replay | money/ledger goldens, A01/A02 regressions, 25-wallet shared-snapshot replay, ranking reproducibility |
| 7 — performance | measured 25-wallet tick budget |
| 8 — release | full suite, working tree unchanged, required docs present |

## Performance baseline (measured, not invented)

300 ticks × 24 strategy wallets = **0.186 s → 0.62 ms/tick** (Windows 11,
Python 3.11.9, excluding external I/O). Gate 7 budgets **10 ms/tick** (~16×
headroom) so a shared CI runner cannot flake while a genuine algorithmic
regression still trips it. Re-measure on the CI runner to tighten.

## Notable adversarial tests

These exist specifically to make dishonest behaviour impossible rather than
discouraged:

- `test_model_cannot_alter_deterministic_figures` — a lying analyst returning
  999,999 profit is overwritten by engine facts.
- `test_weekly_committee_ranking_is_overridden_by_engine` — a sneaky
  synthesizer's fabricated ranking is replaced.
- `test_worker_env_is_sanitized` — a planted `BINANCE_API_SECRET` reads back
  `ABSENT` inside the strategy sandbox.
- `test_unhandled_error_is_redacted_with_correlation_id` — a raised secret
  filesystem path is proven absent from the HTTP response.
- `test_stop_refuses_to_kill_recycled_pid` — a recycled PID receives **no
  signal at all**.
- `test_dns_rebinding_to_private_blocked` — 8 private/metadata addresses.
- `test_builtins_are_structurally_dissimilar_pairwise` — the 12 built-ins are
  proven distinct under the same novelty policy applied to new candidates.
