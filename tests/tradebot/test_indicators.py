"""Indicator unit tests, including every insufficient-data guard clause.

Guard clauses are correctness-critical: a strategy that receives a silently
wrong indicator value on short data would trade on nonsense. Each `None` return
is asserted explicitly rather than covered incidentally.
"""

from decimal import Decimal

from tests.tradebot.strategy_helpers import candle, series
from tradebot.strategies.indicators import (
    atr,
    closes,
    donchian_high,
    donchian_low,
    efficiency_ratio,
    ema,
    ema_series,
    macd_histogram,
    obv_series,
    relative_volume,
    rolling_vwap,
    rsi,
    sma,
    stddev,
    stochastic_k,
    true_range,
    zscore,
)

FLAT = [Decimal(60000) for _ in range(50)]
RISING = [Decimal(60000 + i * 100) for i in range(50)]


# ---- guard clauses (insufficient data -> None, never a wrong number) --------

def test_sma_guards():
    assert sma([Decimal(1)], 5) is None
    assert sma(FLAT, 0) is None
    assert sma([Decimal(2), Decimal(4)], 2) == Decimal(3)


def test_ema_guards():
    assert ema_series([Decimal(1)], 5) == []
    assert ema_series(FLAT, 0) == []
    assert ema([Decimal(1)], 5) is None
    assert ema(FLAT, 10) == Decimal(60000)


def test_stddev_guards():
    assert stddev([Decimal(1)], 5) is None
    assert stddev(FLAT, 1) is None  # period < 2
    assert stddev(FLAT, 10) == Decimal(0)


def test_zscore_guards():
    assert zscore([Decimal(1)], 20) is None
    assert zscore(FLAT, 20) is None  # zero stddev -> undefined, not a div error
    values = FLAT[:-1] + [Decimal(60100)]
    assert zscore(values, 20) > 0


def test_atr_guards():
    assert atr(series(FLAT[:3]), 14) is None
    assert atr(series(FLAT), 14) > 0


def test_true_range_uses_prev_close():
    c = candle(0, "60000", hi_off="50", lo_off="50")
    assert true_range(c, Decimal("60000")) == Decimal("100")
    # Gap up: range measured from the previous close.
    assert true_range(c, Decimal("59000")) == Decimal("1050")


def test_rsi_guards_and_extremes():
    assert rsi([Decimal(1)], 14) is None
    # Only gains in the window -> 100, and no ZeroDivisionError on zero losses.
    assert rsi(RISING, 14) == Decimal(100)
    # Only losses in the window -> 0 (flat candles contribute neither).
    assert rsi(FLAT[:-1] + [Decimal(59000)], 14) == Decimal(0)
    # Mixed gains and losses land strictly between the extremes.
    mixed = FLAT[:-2] + [Decimal(59000), Decimal(60500)]
    assert 0 < rsi(mixed, 14) < 100


def test_stochastic_guards_and_flat_range():
    assert stochastic_k(series(FLAT[:3]), 14) is None
    flat = tuple(candle(i, "60000", hi_off="0", lo_off="0") for i in range(20))
    assert stochastic_k(flat, 14) == Decimal(50)  # hi == lo -> midpoint


def test_macd_guards():
    assert macd_histogram(FLAT[:10]) == []
    assert len(macd_histogram(RISING)) > 0


def test_donchian_guards_and_exclusion():
    assert donchian_high(series(FLAT[:3]), 20) is None
    assert donchian_low(series(FLAT[:3]), 20) is None
    c = series(RISING)
    # exclude_last=True must ignore the newest candle (no self-referencing break)
    assert donchian_high(c, 20, exclude_last=True) < donchian_high(
        c, 20, exclude_last=False)


def test_rolling_vwap_guards():
    assert rolling_vwap(series(FLAT[:3]), 30) is None
    zero_vol = tuple(candle(i, "60000", vol="0") for i in range(40))
    assert rolling_vwap(zero_vol, 30) is None  # no volume -> undefined
    assert rolling_vwap(series(FLAT), 30) == Decimal(60000)


def test_relative_volume_guards():
    assert relative_volume(series(FLAT[:3]), 20) is None
    zero_vol = tuple(candle(i, "60000", vol="0") for i in range(40))
    assert relative_volume(zero_vol, 20) is None
    c = list(series(FLAT[:40]))
    c.append(candle(40, "60000", vol="20"))
    assert relative_volume(tuple(c), 20) == Decimal(2)


def test_efficiency_ratio_guards():
    assert efficiency_ratio(FLAT[:3], 20) is None
    assert efficiency_ratio(FLAT, 20) is None  # zero path -> undefined
    assert efficiency_ratio(RISING, 20) == Decimal(1)  # perfectly efficient


def test_obv_series_directions():
    up = series([Decimal(60000), Decimal(60100), Decimal(60200)])
    assert obv_series(up)[-1] > 0
    down = series([Decimal(60200), Decimal(60100), Decimal(60000)])
    assert obv_series(down)[-1] < 0
    flat = series([Decimal(60000), Decimal(60000)])
    assert obv_series(flat)[-1] == Decimal(0)


def test_closes_helper():
    assert closes(series([Decimal(1), Decimal(2)])) == [Decimal(1), Decimal(2)]
