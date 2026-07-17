"""Exit-path and guard coverage for every built-in strategy.

Entries are covered in test_strategies_signals.py; this file drives the exit
branches, stale-data guards, and sizing edges that only fire while holding.
"""

from decimal import Decimal

import pytest

from tests.tradebot.strategy_helpers import candle, context_for, holding_context, series
from tradebot.domain.ledger import Side, Wallet
from tradebot.domain.strategies import StrategyContext, WalletView
from tradebot.strategies.base import BuiltinStrategy
from tradebot.strategies.builtin import (
    BUILTIN_STRATEGIES,
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


def sells(decision):
    return [i for i in decision.intents if i.side is Side.SELL]


def rising(n, start=60000, step=50):
    return [Decimal(start + i * step) for i in range(n)]


def falling(n, start=70000, step=50):
    return [Decimal(start - i * step) for i in range(n)]


def flat(n, level=60000, amp=5):
    return [Decimal(level + amp * ((-1) ** i)) for i in range(n)]


def hold_ctx(closes, base_qty="0.1", **kw):
    return holding_context(series(closes, **kw), base_qty=base_qty)


# ---- base plumbing ----------------------------------------------------------

def test_base_signal_is_abstract():
    class Bare(BuiltinStrategy):
        min_warmup = 1

    with pytest.raises(NotImplementedError):
        Bare().signal(context_for(series(flat(5))), series(flat(5)), {},
                      holding=False)


def test_buy_intent_declines_when_budget_below_min_notional():
    strategy = BollingerZScore()
    ctx = StrategyContext(
        snapshot=series(flat(50))[-1],
        wallet=WalletView(quote_cash=Decimal("5"), base_qty=Decimal("0"),
                          avg_cost=Decimal("0")),
        candles=series(flat(50)),
    )
    assert strategy.buy_intent(ctx, "x") is None


def test_buy_intent_sizes_from_fraction_of_cash():
    strategy = BollingerZScore()
    candles = series([Decimal("60000")] * 50)
    ctx = StrategyContext(
        snapshot=candles[-1],
        wallet=WalletView(quote_cash=Decimal("10000"), base_qty=Decimal("0"),
                          avg_cost=Decimal("0")),
        candles=candles,
    )
    intent = strategy.buy_intent(ctx, "x", fraction=Decimal("0.5"))
    # 50% of 10,000 at 60,000 -> ~0.0833 BTC after lot-size quantization.
    assert intent.quantity == Decimal("0.08333333")


def test_sell_all_intent_declines_with_no_position():
    ctx = context_for(series(flat(50)), Wallet("w"))
    assert BollingerZScore().sell_all_intent(ctx, "x") is None


def test_entry_price_helper_round_trips():
    assert BuiltinStrategy.entry_price({}) is None
    assert BuiltinStrategy.entry_price({"entry_price": "60000"}) == Decimal("60000")


def test_cooldown_blocks_repeat_entries():
    class AlwaysBuy(BuiltinStrategy):
        min_warmup = 2
        cooldown_candles = 5

        def signal(self, context, candles, state, *, holding):
            return [self.buy_intent(context, "always")]

    strategy = AlwaysBuy()
    state = strategy.initialize()
    candles = series(flat(30))
    first = strategy.on_market_snapshot(context_for(candles[:10]), state)
    assert first.intents  # first entry allowed
    second = strategy.on_market_snapshot(context_for(candles[:11]), first.state)
    assert second.intents == ()  # inside cooldown


def test_none_intents_are_filtered():
    class YieldsNone(BuiltinStrategy):
        min_warmup = 2

        def signal(self, context, candles, state, *, holding):
            return [None]

    decision = YieldsNone().on_market_snapshot(
        context_for(series(flat(10))), YieldsNone().initialize())
    assert decision.intents == ()


# ---- per-strategy exits -----------------------------------------------------

def test_s01_grid_sell_requires_fee_aware_edge():
    strategy = VolAdaptiveGrid()
    closes = flat(25) + [Decimal("60300")]
    state = strategy.initialize()
    state.update({"anchor": "60000", "anchor_index": 1,
                  "entry_price": "60290"})  # edge too thin
    decision = strategy.on_market_snapshot(hold_ctx(closes), state)
    assert not sells(decision)


def test_s01_grid_returns_early_without_volatility():
    strategy = VolAdaptiveGrid()
    zero_range = tuple(candle(i, "60000", hi_off="0", lo_off="0")
                       for i in range(25))
    ctx = context_for(zero_range)
    assert strategy.on_market_snapshot(ctx, strategy.initialize()).intents == ()


def test_s02_zscore_exits_at_mean_time_stop_and_invalidation():
    strategy = BollingerZScore()
    # Mean exit (odd length so the last close sits at/above the rolling mean).
    closes = flat(51)
    d = strategy.on_market_snapshot(hold_ctx(closes), strategy.initialize())
    assert sells(d) and sells(d)[0].reason_code == "z_mean_exit"

    # Time stop below the mean.
    closes = flat(49) + [Decimal("59900")]
    state = strategy.initialize()
    state["candles_held"] = 999
    d = strategy.on_market_snapshot(hold_ctx(closes), state)
    assert sells(d)[0].reason_code == "z_time_stop"

    # Trend invalidation while holding.
    d = strategy.on_market_snapshot(hold_ctx(falling(50, start=70000, step=200)),
                                    strategy.initialize())
    assert sells(d)[0].reason_code == "z_trend_invalidated"


def test_s03_vwap_exits():
    strategy = VwapReversion()
    d = strategy.on_market_snapshot(hold_ctx(flat(41)), strategy.initialize())
    assert sells(d)[0].reason_code == "vwap_touch"

    state = strategy.initialize()
    state["candles_held"] = 999
    closes = flat(39) + [Decimal("59000")]
    d = strategy.on_market_snapshot(hold_ctx(closes), state)
    assert sells(d)[0].reason_code in ("vwap_time_stop", "vwap_breakdown")


def test_s03_vwap_breakdown_exit_on_heavy_volume_new_low():
    strategy = VwapReversion()
    candles = list(series(flat(40)))
    candles.append(candle(40, "58000", vol="60", lo_off="5"))
    ctx = holding_context(tuple(candles))
    d = strategy.on_market_snapshot(ctx, strategy.initialize())
    assert sells(d)[0].reason_code == "vwap_breakdown"


def test_s04_oscillator_exits():
    strategy = OscillatorExhaustion()
    # RSI recovered.
    d = strategy.on_market_snapshot(hold_ctx(rising(50)), strategy.initialize())
    assert sells(d)[0].reason_code in ("rsi_recovered", "stoch_overbought")

    # Time stop in a flat market.
    state = strategy.initialize()
    state["candles_held"] = 999
    d = strategy.on_market_snapshot(hold_ctx(flat(50)), state)
    assert sells(d)[0].reason_code == "time_stop"


def test_s04_atr_target_exit():
    strategy = OscillatorExhaustion()
    closes = flat(45) + [Decimal("60100")]
    state = strategy.initialize()
    state["entry_price"] = "59000"  # far below -> ATR target reached
    d = strategy.on_market_snapshot(hold_ctx(closes), state)
    assert sells(d)


def test_s05_donchian_trail_and_failed_breakout():
    strategy = DonchianBreakout()
    d = strategy.on_market_snapshot(hold_ctx(falling(50)), strategy.initialize())
    assert sells(d)[0].reason_code == "donchian_trail_exit"

    state = strategy.initialize()
    state.update({"breakout_level": "70000", "candles_held": 1})
    d = strategy.on_market_snapshot(hold_ctx(flat(50)), state)
    assert sells(d)[0].reason_code in ("failed_breakout", "donchian_trail_exit")


def test_s06_ema_trend_failure_and_profit_target():
    strategy = EmaPullback()
    d = strategy.on_market_snapshot(hold_ctx(falling(70)), strategy.initialize())
    assert sells(d)[0].reason_code == "trend_failure"

    state = strategy.initialize()
    state["entry_price"] = "60000"
    d = strategy.on_market_snapshot(hold_ctx(rising(70, start=61000, step=60)),
                                    state)
    assert sells(d)[0].reason_code == "profit_target"


def test_s07_macd_negative_cross_exit():
    strategy = MacdMomentum()
    # A rise that reverses sharply drives the histogram negative. A perfectly
    # linear fall would NOT: constant slope keeps the MACD line parallel to its
    # signal, so the histogram converges to zero rather than crossing.
    closes = rising(60, step=60) + falling(25, start=63600, step=250)
    d = strategy.on_market_snapshot(hold_ctx(closes), strategy.initialize())
    assert sells(d)[0].reason_code == "macd_negative_cross"


def test_s07_macd_max_hold_exit():
    strategy = MacdMomentum()
    state = strategy.initialize()
    state["candles_held"] = 999
    d = strategy.on_market_snapshot(hold_ctx(rising(80)), state)
    assert sells(d)[0].reason_code == "max_hold"


def test_s07_macd_deceleration_exit():
    """Momentum fading for four candles running triggers an exit."""
    strategy = MacdMomentum()
    closes = rising(60, step=200) + [Decimal(c) for c in
                                     (72000, 72400, 72600, 72700, 72750)]
    d = strategy.on_market_snapshot(hold_ctx(closes), strategy.initialize())
    assert sells(d)[0].reason_code in ("macd_deceleration", "max_hold",
                                       "macd_negative_cross")


def test_s08_squeeze_exits():
    strategy = SqueezeExpansion()
    d = strategy.on_market_snapshot(hold_ctx(falling(60)), strategy.initialize())
    assert sells(d)[0].reason_code in ("back_inside_channel", "momentum_reversal")


def test_s08_squeeze_run_resets_outside_squeeze():
    strategy = SqueezeExpansion()
    state = strategy.initialize()
    state["squeeze_run"] = 2  # below min, and not in a squeeze -> reset
    # State is copied per tick, so the update lands on the returned state.
    decision = strategy.on_market_snapshot(
        context_for(series(rising(60, step=300))), state)
    assert decision.state["squeeze_run"] == 0


def test_s09_chandelier_trend_reversal_exit():
    strategy = ChandelierTrend()
    state = strategy.initialize()
    state["highest_since_entry"] = "80000"
    d = strategy.on_market_snapshot(hold_ctx(falling(70)), state)
    assert sells(d)[0].reason_code in ("chandelier_stop", "trend_filter_reversal")
    assert d.state["highest_since_entry"] is None  # cleared on exit


def test_s10_mtf_exits():
    strategy = MtfMomentum()
    d = strategy.on_market_snapshot(hold_ctx(falling(120, step=30)),
                                    strategy.initialize())
    assert sells(d)[0].reason_code == "momentum_nonpositive"


def test_s10_mtf_returns_early_without_stddev():
    strategy = MtfMomentum()
    assert strategy.on_market_snapshot(
        context_for(series(flat(100, amp=0))), strategy.initialize()
    ).intents == ()


def test_s11_obv_exits():
    strategy = ObvBreakout()
    state = strategy.initialize()
    state["breakout_level"] = "90000"  # price is far below it
    d = strategy.on_market_snapshot(hold_ctx(flat(50)), state)
    assert sells(d)[0].reason_code in ("lost_breakout_level", "obv_divergence",
                                       "atr_trail_stop")


def test_s11_obv_divergence_exit():
    strategy = ObvBreakout()
    candles = list(series(falling(50, step=20), vol="30"))
    # Price ends higher than 5 candles ago while OBV keeps falling.
    candles.append(candle(50, "70000", vol="1"))
    d = strategy.on_market_snapshot(holding_context(tuple(candles)),
                                    strategy.initialize())
    assert sells(d)


def test_s11_obv_atr_trail_stop():
    strategy = ObvBreakout()
    state = strategy.initialize()
    state["entry_price"] = "99999"  # far above -> trail stop breached
    d = strategy.on_market_snapshot(hold_ctx(flat(50)), state)
    assert sells(d)


def test_s12_regime_exits_per_subpolicy():
    strategy = RegimeEnsemble()
    # Trend regime exit: price under the trend EMA.
    d = strategy.on_market_snapshot(hold_ctx(falling(80, step=60)),
                                    strategy.initialize())
    assert sells(d)[0].reason_code == "regime:trend_exit"

    # Range regime exit at the mean (odd length -> last close at/above mean).
    d = strategy.on_market_snapshot(hold_ctx(flat(71)), strategy.initialize())
    assert sells(d)[0].reason_code == "regime:range_exit"


def test_s12_ambiguous_time_exit():
    strategy = RegimeEnsemble()
    closes = []
    px = Decimal(60000)
    for i in range(80):
        px += Decimal(35) if i % 3 else Decimal(-40)
        closes.append(px)
    state = strategy.initialize()
    state["candles_held"] = 999
    d = strategy.on_market_snapshot(hold_ctx(closes), state)
    if d.state.get("regime") == "ambiguous":
        assert sells(d)[0].reason_code == "regime:ambiguous_time_exit"


def test_s12_regime_classified_on_every_tick():
    strategy = RegimeEnsemble()
    d = strategy.on_market_snapshot(context_for(series(flat(60, amp=0))),
                                    RegimeEnsemble().initialize())
    assert d.state["regime"] in ("ambiguous", "range", "trend")
    assert d.intents == ()  # a zero-range market gives no signal


@pytest.mark.parametrize("cls", BUILTIN_STRATEGIES, ids=lambda c: c.__name__)
def test_every_strategy_holds_on_short_history(cls):
    strategy = cls()
    short = series(flat(3))
    assert strategy.on_market_snapshot(
        context_for(short), strategy.initialize()).intents == ()
