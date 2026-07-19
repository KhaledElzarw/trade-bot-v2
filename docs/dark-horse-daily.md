# Darkhorse - Daily

A second **permanent** wallet alongside Dark Horse. Display name is exactly
`Darkhorse - Daily`. Internal kind `dark_horse_daily`. Starts at 10,000.00
USDT and is counted in the 140,000.00 active baseline (12 active + Dark Horse
+ Darkhorse - Daily).

Same principles as Dark Horse — spot-only, permanent wallet, holding cash is
always valid, exempt from weekly elimination — but where Dark Horse replaces
its strategy code on a monthly review cadence, Darkhorse - Daily **re-tunes
its strategy parameters once per completed UTC day** by learning from the
daily lessons: its own, plus every lesson the other wallets committed.

## The 24-hour learning loop

Once per completed UTC day (`due_for_adaptation`, 24h cadence gate):

1. **Look back at its own trades** — read its own `DailyLesson` for the
   completed day (engine-computed figures + evidence-bound analysis).
2. **Read everyone else's lessons** — every `DailyLesson` the other wallets
   committed for that day, cited by stable ref `date:wallet_id`.
3. **Propose tweaks** — the model proposes parameter adjustments, each with a
   rationale citing the lesson(s) it learned from.
4. **Engine guardrails** (never delegated to the model):
   - unknown parameters are dropped — the tunable surface cannot grow;
   - proposals citing no considered lesson are dropped — learning must be
     traceable to actual lessons, never invented;
   - only the first proposal per parameter counts, and the step budget is
     measured from the day-start value — same-day proposals cannot chain;
   - every value is clamped to hard bounds AND the per-parameter daily step
     budget, so one day's lesson can only nudge the strategy.
5. **Activate** — the result becomes a new daily strategy version
   (`dark-horse-daily-YYYY-MM-DD`) via the same `DarkHorseWallet` continuity
   mechanics: only the version pointer moves, the wallet is **never reset**.

## Guaranteed software invariants

- The wallet, positions, lots, equity history, and lifetime P&L are **never
  reset** — not on daily adaptation, not on rollback, not weekly
  (`test_apply_adaptation_upgrades_version_without_touching_wallet`,
  `test_defective_daily_version_rolls_back_without_reset`).
- **Exempt from weekly loss elimination and weekly no-trade elimination**
  (`test_darkhorse_daily_exempt_from_elimination`).
- **A hypothesis may never move a parameter** — "uncertainty never tweaks",
  mirroring Dark Horse's "uncertainty never buys". Enforced by the
  `ParamAdjustment` schema, not trusted to the model.
- Every adjustment must cite the lessons it learned from, and only lessons
  that were actually considered that day
  (`test_schema_rejects_citations_outside_considered_lessons`).
- A degraded adaptation (model unavailable, no lessons for the day) changes
  nothing, records its reason explicitly, and trading continues on
  yesterday's parameters — parameters are never invented
  (`test_model_failure_yields_degraded_no_change`).
- Re-running a completed day is a cached no-op
  (`test_adaptation_is_idempotent_per_day`).
- A no-change day may not mint a new strategy version — no version churn.

## Tunable surface

The complete tunable surface lives in `TUNABLES`
(`tradebot/domain/dark_horse_daily.py`). Anything outside this table is not a
strategy parameter and cannot be touched by an adaptation.

| Parameter | Bounds | Max daily step | Default |
|-----------|--------|----------------|---------|
| `accumulate_confidence` | 0.50 – 0.90 | 0.05 | 0.60 |
| `reduce_confidence` | 0.50 – 0.90 | 0.05 | 0.60 |
| `accumulate_fraction` | 0.05 – 0.50 | 0.10 | 0.25 |
| `reduce_fraction` | 0.10 – 1.00 | 0.15 | 0.50 |
| `signal_cadence_hours` | 0.5 – 4 | 0.5 | 1 |

## Records and lineage

Each day produces a schema-validated `DailyAdaptation` record
(`darkhorse-daily-adaptation-v1`): the full resulting parameter set, every
adjustment with its rationale and lesson citations, the lessons considered,
and degraded status. Daily versions are linked in the lineage graph with the
`dark_horse_daily_adaptation` relationship, so the wallet's entire adaptation
ancestry is permanent evidence.
