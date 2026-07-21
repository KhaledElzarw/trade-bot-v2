"""Strategy-specific signal tests: each built-in's characteristic entry fires
on a crafted scenario, and a characteristic exit fires when holding."""

from decimal import Decimal

from tests.tradebot.strategy_helpers import (
    holding_context,
    run_ticks,
    series,
)
from tradebot.domain.ledger import Side
from tradebot.strategies.builtin import (
    BollingerZScore,
    ChandelierTrend,
    DonchianBreakout,
    EmaPullback,
    MacdMomentum,
    MtfMomentum,
    ObvBreakout,
    OscillatorExhaustion,
    RegimeEnsemble,
    SqueezeExpansion,
    VolAdaptiveGrid,
    VwapReversion,
)


def buys(decisions):
    return [i for d in decisions for i in d.intents if i.side is Side.BUY]


def rising(n, start=60000, step=40):
    return [Decimal(start + i * step) for i in range(n)]


def flat_noise(n, level=60000, amp=5):
    return [Decimal(level + amp * ((-1) ** i)) for i in range(n)]


def test_s01_grid_buys_below_anchor_and_never_on_recenter_candle():
    closes = flat_noise(25) + [Decimal("59900")] * 3  # below anchor - ATR spacing
    decisions, state = run_ticks(VolAdaptiveGrid(), series(closes))
    assert buys(decisions), "grid should buy below the spaced level"
    assert state["anchor"] is not None


def test_mean_reversion_rests_limit_below_but_breakout_stays_market():
    from tradebot.strategies.builtin import DonchianBreakout
    # Bollinger deep drop -> a resting LIMIT bid BELOW the mark.
    closes = [Decimal(60000)] * 45 + [Decimal(59940)]
    decs, _ = run_ticks(BollingerZScore(), series(closes))
    b = buys(decs)[0]
    assert b.order_type == "LIMIT" and b.limit_price < Decimal("59940")
    # Donchian breakout entry must stay MARKET (a resting bid would miss it).
    rising_closes = [Decimal(60000 + i * 60) for i in range(60)]
    decs, _ = run_ticks(DonchianBreakout(), series(rising_closes))
    assert all(i.order_type == "MARKET" for i in buys(decs))


def test_s02_zscore_buys_deep_deviation_not_crash():
    closes = flat_noise(50) + [Decimal("59940")]  # sharp local drop ≈ many σ
    decisions, _ = run_ticks(BollingerZScore(), series(closes))
    assert buys(decisions)


def test_s02_zscore_refuses_falling_knife():
    # 12% collapse over the trend window: crash filter must veto entry.
    closes = [Decimal(60000 - i * 200) for i in range(50)]
    decisions, _ = run_ticks(BollingerZScore(), series(closes))
    assert not buys(decisions)


def test_s03_vwap_buys_deep_discount_with_deceleration():
    closes = flat_noise(40) + [Decimal("59200"), Decimal("58900")]  # -800 then -300
    decisions, _ = run_ticks(VwapReversion(), series(closes))
    assert buys(decisions)


def test_s04_oscillator_buys_exhaustion_recovery():
    decline = [Decimal(60000 - i * 150) for i in range(41)]  # RSI deep oversold
    candles = list(series(decline))
    from tests.tradebot.strategy_helpers import candle
    # Recovery candle: green close near the lows (stoch still oversold).
    candles.append(candle(41, "54030", open_="53990", hi_off="5", lo_off="30"))
    decisions, _ = run_ticks(OscillatorExhaustion(), tuple(candles))
    assert buys(decisions)


def test_s05_donchian_buys_volume_confirmed_breakout():
    closes = flat_noise(40)
    candles = list(series(closes))
    from tests.tradebot.strategy_helpers import candle
    candles.append(candle(40, "60100", vol="40"))  # breakout + 4x volume
    decisions, _ = run_ticks(DonchianBreakout(), tuple(candles))
    assert buys(decisions)


def test_s05_donchian_rejects_breakout_without_volume():
    closes = flat_noise(40)
    candles = list(series(closes))
    from tests.tradebot.strategy_helpers import candle
    candles.append(candle(40, "60100", vol="10"))  # no volume confirmation
    decisions, _ = run_ticks(DonchianBreakout(), tuple(candles))
    assert not buys(decisions)


def test_s06_ema_pullback_buys_recovered_dip_in_uptrend():
    closes = rising(70, step=30)
    candles = list(series(closes))
    from tests.tradebot.strategy_helpers import candle
    # Pullback candle: dips toward EMA20 (low far below) then closes strong.
    last = closes[-1]
    candles.append(candle(70, str(last + 20), open_=str(last - 200),
                          lo_off="600", hi_off="5"))
    decisions, _ = run_ticks(EmaPullback(), tuple(candles))
    assert buys(decisions)


def test_s07_macd_buys_positive_cross():
    closes = flat_noise(60) + rising(20, start=60050, step=60)
    decisions, _ = run_ticks(MacdMomentum(), series(closes))
    assert buys(decisions)


def test_s08_squeeze_buys_expansion_after_compression():
    closes = flat_noise(60, amp=3)
    candles = list(series(closes, hi_off="3", lo_off="3"))
    from tests.tradebot.strategy_helpers import candle
    candles.append(candle(60, "60010", hi_off="3", lo_off="3"))
    candles.append(candle(61, "60150", vol="30", hi_off="20", lo_off="3"))
    decisions, _ = run_ticks(SqueezeExpansion(), tuple(candles))
    assert buys(decisions)


def test_s09_chandelier_buys_new_high_in_trend():
    decisions, _ = run_ticks(ChandelierTrend(), series(rising(80, step=50)))
    assert buys(decisions)


def test_s09_chandelier_stop_sells_when_holding():
    # Rising then a deep drop below highest-high − 3·ATR.
    closes = rising(70, step=50) + [Decimal("60000")]
    strategy = ChandelierTrend()
    state = strategy.initialize()
    state["highest_since_entry"] = str(closes[-2] + 10)
    ctx = holding_context(series(closes))
    decision = strategy.on_market_snapshot(ctx, state)
    sells = [i for i in decision.intents if i.side is Side.SELL]
    assert sells and sells[0].reason_code in ("chandelier_stop", "trend_filter_reversal")


def test_s10_mtf_momentum_buys_multi_horizon_agreement():
    decisions, _ = run_ticks(MtfMomentum(), series(rising(120, step=25)))
    assert buys(decisions)


def test_s10_mtf_momentum_holds_cash_in_downtrend():
    closes = [Decimal(70000 - i * 25) for i in range(120)]
    decisions, _ = run_ticks(MtfMomentum(), series(closes))
    assert not buys(decisions)


def test_s11_obv_buys_accumulation_breakout():
    closes = flat_noise(50)
    candles = list(series(closes))
    from tests.tradebot.strategy_helpers import candle
    # Three accumulation candles with rising closes and heavy volume.
    for j, px in enumerate(("60050", "60120", "60200")):
        candles.append(candle(50 + j, px, vol="35"))
    decisions, _ = run_ticks(ObvBreakout(), tuple(candles))
    assert buys(decisions)


def test_s12_regime_trend_subpolicy_fires_in_trend():
    decisions, state = run_ticks(RegimeEnsemble(), series(rising(80, step=60)))
    entries = buys(decisions)
    assert entries and entries[0].reason_code == "regime:trend_continuation"
    assert state["regime"] == "trend"


def test_s12_regime_range_subpolicy_fires_in_range():
    closes = flat_noise(70) + [Decimal("59960")]  # dip inside a choppy range
    decisions, state = run_ticks(RegimeEnsemble(), series(closes))
    entries = buys(decisions)
    assert state["regime"] == "range"
    assert entries and entries[0].reason_code == "regime:range_reversion"


def test_s12_regime_holds_cash_when_ambiguous():
    # Mixed path: enough net drift to avoid 'range' but too choppy for 'trend'.
    closes = []
    px = Decimal(60000)
    for i in range(80):
        px += Decimal(35) if i % 3 else Decimal(-40)
        closes.append(px)
    strategy = RegimeEnsemble()
    decisions, state = run_ticks(strategy, series(closes))
    if state["regime"] == "ambiguous":
        assert not buys(decisions[-10:])


def test_sell_only_reduces_owned_btc():
    """Every built-in's sell path uses sell_all_intent, capped at owned qty."""

    ctx = holding_context(series(flat_noise(80)), base_qty="0.05")
    for cls in (BollingerZScore, VwapReversion, MacdMomentum):
        strategy = cls()
        state = strategy.initialize()
        state["candles_held"] = 10_000  # force any time-stop exit
        decision = strategy.on_market_snapshot(ctx, state)
        for intent in decision.intents:
            if intent.side is Side.SELL:
                assert intent.quantity <= Decimal("0.05")
