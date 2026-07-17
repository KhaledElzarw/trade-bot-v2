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
| 8 | Daily & weekly learning | **Done** | `domain/lessons.py`, `application/lessons.py`, `reports/renderer.py`; 22 tests (schema-enforced hypothesis labelling, committee cannot reorder rank or spare a loser, engine facts override model, idempotency, degraded output, atomic rendering) |
| 9 | Evolution, novelty & promotion | **Done** | `domain/{evaluations,lineage}.py`, `application/{evolution,liquidation,promotion,novelty}.py`; `docs/evolution-policy.md`; 55 tests (all replacement scenarios, ban reuse, roll-forward, shortage rollback, invariants, AST fingerprinting, novelty/mutation thresholds, lineage graph) |
| 10 | Dark Horse | **Done** | `domain/dark_horse.py`, `application/dark_horse.py`; `docs/dark-horse.md`; 21 tests (five-domain committee, explicit missing/stale degradation, no-shorting type, elimination exemption via Phase 9 engine, wallet continuity across upgrade/rollback) |
| 11 | API & dashboard rewrite | **Core done** | `tradebot/api/{security,app,views}.py`, `dashboard/static/dashboard.v2.js`; 44 tests (fail-closed mutations, redacted errors, bind guard, CSP headers, 19 v2 routes, zero unsafe DOM sinks, URL vetting) |
| 12 | Operations, observability & CI | **Done** | `operations/{process_identity,structured_logging}.py`, `cli/tradebotctl.py`, 8-gate `.github/workflows/ci.yml`, `pyproject.toml` (mypy/bandit/coverage), `docs/testing.md`; 48 tests |
| 13 | Independent verification & cleanup | Not started | — |

## Baseline metrics (Phase 0, actual)
- Tests: 403 passed, 11 failed, 1 collection error (`fcntl`), on Windows/Python 3.11.9
- Coverage: 98% (3635 stmts / 69 missed), `fail_under = 0`
- Ruff: 2 errors (select E/F, E501 ignored)
- No mypy / bandit / pip-audit / frontend gates in CI

## Notes
- No production source modified during Phase 0.
- venv created at `.venv` (untracked) for validation only.

### Phase 9b (novelty & lineage) — evidence (actual)
- AST structural fingerprint over 11 spec dimensions + conceptual family; one
  versioned `NoveltyPolicy` (`novelty-policy-v1`) holds every threshold.
- **All 12 built-ins verified pairwise below the 0.65 novel similarity threshold**
  (`test_builtins_are_structurally_dissimilar_pairwise`) and have 12 distinct
  fingerprint digests — distinctness proven under the policy, not asserted.
- Novel checks: duplicate hash, duplicate fingerprint, ban (hash *and*
  fingerprint — a reskinned clone with a new hash is still blocked), structural
  similarity, |signal correlation| (inverted clone caught), trade-entry overlap.
- Model's novelty claim explicitly not accepted as evidence
  (`test_model_claim_of_novelty_is_not_accepted_as_evidence`).
- Mutation checks: ineligible/eliminated parent rejected, identical-to-parent
  rejected, unrelated-to-parent rejected, missing description rejected, valid
  mutation accepted inside the 0.65-0.95 band.
- Lineage: ancestry/generation/children, invalid-edge rejection, cycle-safe walk,
  lineage survives elimination as permanent evidence.
- 21 new tests. New-package suite **210 passed**; ruff clean; full suite
  **613 passed / same 11 pre-existing failures**.

### Phase 10 — evidence (actual)
- Five required domains enforced; every decision records all five states and links
  evidence ids. Missing domain -> MISSING with zero items (nothing invented); stale
  evidence -> STALE; empty-item OK report downgraded to MISSING.
- Degradation policy proven: any degraded domain caps the decision at HOLD
  ("uncertainty never buys"), while defensive REDUCE stays available.
- No-shorting proven at the type level: DarkHorseAction has exactly
  {accumulate, hold, reduce, exit_to_cash}.
- Cross-phase integration: a Dark Horse losing 5,000 USDT with ZERO fills survives
  the Phase 9 elimination engine while the bottom-six rule retires six actives
  (`test_dark_horse_never_eliminated_despite_loss_and_no_trades`).
- Wallet continuity proven: upgrade and rollback both preserve wallet identity,
  quote/base balances and lifetime realized P&L; six successive upgrades never
  restore the 10,000 starting balance.
- 21 new tests. New-package suite **231 passed**; ruff clean; full suite
  **634 passed / same 11 pre-existing failures**.

### Phase 8 — evidence (actual)
- A20 closed at the schema level: `Claim` REJECTS any statement lacking
  `evidence_ids` unless explicitly labelled `is_hypothesis=True` — unsupported
  claims are structurally impossible, not merely discouraged.
- Committee restrictions enforced by validators, not convention:
  ranking must be profit-descending with contiguous ranks; a losing or zero-fill
  active row that is not marked eliminated fails validation. The committee
  therefore cannot reorder the ranking or preserve a losing incumbent.
- Engine owns the numbers: a deliberately lying analyst returning 999,999 profit
  has its figures overwritten by the engine's facts
  (`test_model_cannot_alter_deterministic_figures`); a sneaky synthesizer's
  fabricated ranking is replaced by the engine's
  (`test_weekly_committee_ranking_is_overridden_by_engine`).
- Idempotency: daily and weekly generation call the model exactly once per
  window; re-running returns the stored record (`cached=True`).
- Degradation: model exception/None yields an explicit degraded record that keeps
  the deterministic figures and invents no analysis; a weekly model failure still
  produces the complete correct deterministic ranking.
- Markdown is a derived export written atomically (temp + os.replace); re-render
  is stable and leaves no .tmp files.
- 22 new tests. New-package suite **253 passed**; ruff clean; full suite
  **656 passed / same 11 pre-existing failures**.

### Phase 11 — evidence (actual)
- **A12 closed**: every mutation requires a Bearer token and fails CLOSED —
  no token / wrong token -> 401, cross-origin -> 403, token in query string ->
  400, empty control payload -> 422. `token_matches` is constant-time and
  returns False on any missing value.
- **A13 closed**: an endpoint raising `RuntimeError("secret db path
  /srv/prod/tradebot.db")` returns a generic message + 16-char correlation id;
  the path and exception type are provably absent from the response body.
- **A14 closed**: `dashboard.v2.js` contains **zero** innerHTML / outerHTML /
  insertAdjacentHTML / document.write / eval / new Function in executable code
  (v1's 26 innerHTML uses remain, asserted as the baseline). All untrusted
  values render via `textContent`; links are parsed with `URL` and admit only
  `https:` (plus loopback `http:`), with `noopener noreferrer`.
- **A09 closed at the UI/API surface**: no aiBaseUrl/allowlist/model-config
  editing exists in either the JS or the API (POST/PUT probes return 404/405).
- Remote bind without a >=32-char token refuses startup (`InsecureBindError`);
  loopback is the default.
- CSP/nosniff/no-referrer/DENY/no-store headers on every response; oversized
  bodies rejected with 413.
- Active vs shadow strictly separated: summary reports 130,000.00 active and
  120,000.00 shadow virtual in distinct sections; filters return 12/12/1/25.
- Dependencies added with documented purpose: fastapi, uvicorn, httpx (+dev:
  pytest-asyncio, hypothesis, mypy, bandit, pip-audit).
- 44 new tests. New-package suite **297 passed**; `node --check` OK; ruff clean;
  full suite **700 passed / same 11 pre-existing failures**.

### Known gap (Phase 11)
- The v1 dashboard/server remain in the tree; removing them is Phase 13 cleanup.
- Node/jsdom behavioural DOM tests + Playwright smoke are Gate 5 work (Phase 12);
  the always-on Python-driven static safety floor is in place now.

### Phase 12 — evidence (actual)
- **A15 closed**: identity = pid + OS start time + executable + command + service
  + instance id + PID-file nonce. A recycled PID (same pid, different start time)
  receives **no signal at all** (`test_stop_refuses_to_kill_recycled_pid`,
  asserts `signals == []`); a stale PID file signals nothing; escalation
  RE-VERIFIES identity after the grace window and aborts if the PID was reused
  mid-window. `tradebotctl stop` exits non-zero rather than killing a mismatch,
  and `restart` aborts instead of starting a duplicate.
- `tradebotctl` implements all 11 required commands with injected side effects
  (no real processes/daemons/DB/network in tests).
- Structured JSON logs carry all 13 mandated correlation fields; secrets are
  redacted by key pattern (nested + lists), long external content truncated,
  exceptions logged as category not traceback text. 16 metrics registered;
  unknown metric names rejected.
- CI upgraded from 1 job to **8 gates**.
- **Real defects found and fixed this phase:**
  1. `MacdMomentum.macd_deceleration` was **dead code** — proven unreachable
     across 390 generated scenarios, because the histogram crosses below zero
     before it can decline 4 candles running, and `h3 < 0` returns first. The
     declared `decel_streak_exit = 3` parameter was also never used (the code
     hardcoded a 4-value chain, and the comment misdescribed it). Fixed with a
     real `declining_streak()` honouring the parameter; the exit now fires.
  2. `cmd_stop` passed Optional adapters straight into `stop_process` — a latent
     None-call crash. Now fails closed: without adapters to VERIFY identity it
     refuses to signal.
  3. **pip-audit found 3 real CVEs** in legacy pins (PYSEC-2026-2270
     python-dotenv, PYSEC-2026-1872 + PYSEC-2026-2275 requests). Pins raised;
     audit now clean.
- Bandit: 2 findings triaged rather than blanket-skipped — the sandbox's
  intentional subprocess use, annotated inline with justification (argv is a
  fixed list, untrusted bundle travels as stdin DATA, never as an argument).
- Gates measured, not invented: mypy **clean (62 files)**, bandit **0 issues**,
  pip-audit **clean**, perf **0.62 ms/tick measured** with a 10 ms budget.
- Coverage on `tradebot/*` raised 93% -> **97%**; Gate 2 enforces
  `--fail-under=97` as a ratchet. **NOT 100% — see docs/testing.md for the exact
  103-statement gap.** One documented omission (worker_main.py, unobservable
  from the parent by design; behaviour covered by real-subprocess tests).
- 48 new tests. New-package suite **402 passed**; full suite **805 passed / same
  11 pre-existing failures**.
