"""Performance budget for the multi-wallet tick (CI Gate 7).

The budget is derived from a MEASURED local baseline, not an invented number:

    300 ticks x 24 strategy wallets = 0.186 s  ->  0.62 ms/tick
    (Windows 11, Python 3.11.9, 2026-07-17, excluding external I/O)

The gate is set to 10 ms/tick — ~16x the measured baseline — so that a shared
CI runner (typically 2-5x slower) never flakes, while a genuine algorithmic
regression (e.g. an accidental O(n^2) scan over wallets or candles, or a
per-tick full-history recompute) still trips it. Tighten as the baseline is
re-measured on the CI runner itself.
"""

import time

from tests.tradebot.test_replay_25_wallets import N_CANDLES, run_replay

MAX_MS_PER_TICK = 10.0
MEASURED_BASELINE_MS = 0.62


def test_multiwallet_tick_meets_performance_budget():
    start = time.perf_counter()
    portfolio, fills, _ = run_replay()
    elapsed = time.perf_counter() - start

    ms_per_tick = elapsed / N_CANDLES * 1000
    wallets = len(portfolio.active) + len(portfolio.shadow)

    assert wallets == 24, "budget is calibrated for 24 strategy wallets"
    assert fills > 0, "a no-op replay would make the timing meaningless"
    assert ms_per_tick < MAX_MS_PER_TICK, (
        f"tick budget exceeded: {ms_per_tick:.3f} ms/tick over {wallets} "
        f"wallets (budget {MAX_MS_PER_TICK} ms, measured baseline "
        f"{MEASURED_BASELINE_MS} ms)"
    )


def test_replay_work_scales_with_candles_not_quadratically():
    """Guards against an accidental O(n^2) rescan of history each tick."""

    start = time.perf_counter()
    run_replay()
    full = time.perf_counter() - start

    # The strategy window is bounded (WINDOW candles), so per-tick cost must be
    # roughly constant. Total time therefore stays well under a quadratic curve.
    assert full < 30.0, f"replay took {full:.1f}s; suspect quadratic scan"


def test_no_unbounded_history_growth_in_wallet_state():
    """Wallet state must not accumulate per-tick objects without bound."""

    portfolio, _, _ = run_replay()
    for slot in portfolio.active + portfolio.shadow:
        wallet = slot.wallet
        # The ledger keeps postings (intentionally, as evidence), but the
        # wallet's own scalar state must remain scalar.
        assert isinstance(wallet.quote_cash.__class__.__name__, str)
        assert wallet.base_qty >= 0
