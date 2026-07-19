# Dark Horse

One additional **permanent** active wallet. Display name is exactly `Dark
Horse`. Internal role `dark_horse`. Starts at 10,000.00 USDT and is counted in
the 140,000.00 active baseline (alongside `Darkhorse - Daily` — see
`docs/dark-horse-daily.md`).

## Guaranteed software invariants

- The wallet, positions, lots, equity history, and lifetime P&L are **never
  reset** — not on strategy upgrade, not on rollback, not weekly.
- **Exempt from weekly loss elimination and weekly no-trade elimination.**
  Verified by `test_dark_horse_never_eliminated_despite_loss_and_no_trades`: a
  Dark Horse that lost 5,000 USDT with zero fills survives a week in which the
  bottom-six rule retires six active strategies.
- Spot-only. `DarkHorseAction` contains exactly `accumulate`, `hold`, `reduce`,
  `exit_to_cash` — **no shorting action exists in the type**.
- Every decision records the state of all five required domains.

## Five required analysis domains

| Domain | Covers |
|--------|--------|
| `macro` | inflation, employment, rates, yield curve, dollar/liquidity proxies, FOMC decisions & communications, release calendar |
| `technical` | daily/weekly trend, market structure, volatility, momentum, long-horizon S/R, drawdown from highs |
| `bitcoin_fundamental` | network security, supply issuance, adoption/market access, ETF disclosures, material regulatory disclosures |
| `onchain` | active addresses, transaction activity, mempool/fee pressure, realized-cap & holder-behaviour metrics |
| `liquidity_derivatives` | spot liquidity, spread/depth, futures positioning, open interest, funding/premium, liquidations, CFTC positioning |

Each domain returns a schema-validated `DomainReport` of `EvidenceItem`s
carrying source, timestamps, freshness, value, interpretation, confidence, and
the `data_snapshot_id` linking back to broker provenance.

## Degradation policy (simulation assumption, not a guarantee)

Missing or stale domains are **explicit**, never defaulted and never fabricated
(closes A18/A19/A20 on this path):

- A report with no evidence items is downgraded to `MISSING`, not treated as OK.
- Evidence older than the freshness budget (default 36h) downgrades the domain
  to `STALE`.
- **Uncertainty never buys:** `ACCUMULATE` requires all five domains usable and
  sufficient bullish confidence. Any degraded domain caps the decision at
  `HOLD`.
- Defensive action stays available while degraded — a broadly bearish read can
  still `REDUCE` an existing position.

## Cadence

Long-horizon: evaluate once per completed 4h bar by default (configurable).
Forced trading is never required — `HOLD` and holding cash for weeks or months
are valid outcomes, which is why the no-trade exemption exists.

## Strategy evolution with wallet continuity

`DarkHorseWallet` separates the **permanent wallet** from the **mutable
strategy-version pointer**:

- `upgrade(new_version_id)` moves the pointer and appends to
  `version_history`; the wallet object, balances, and lifetime P&L are
  untouched (`test_upgrade_preserves_wallet_and_records_history`).
- `rollback()` restores the last technically valid version on defect — again
  without touching the wallet
  (`test_rollback_restores_last_valid_version_without_resetting_wallet`).
- Repeated upgrades never restore the 10,000 starting balance
  (`test_balance_never_reset_across_many_upgrades`).

Proposed Dark Horse versions must pass replay and a dedicated shadow comparison
before activation; monthly is the default cadence for major code replacement
unless an operator changes it.
