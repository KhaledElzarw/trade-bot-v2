"""Strategy 4 — RSI/Stochastic Exhaustion Recovery.

Oscillator exhaustion + recovery: BUY only when RSI and stochastic both show
exhaustion AND the last closed candle confirms recovery (close > open). Exits
on RSI mean recovery, stochastic overbought, ATR profit target, or time stop.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import atr, closes, rsi, stochastic_k


class OscillatorExhaustion(BuiltinStrategy):
    name = "OscillatorExhaustion"
    family = "oscillator_reversal"
    min_warmup = 40
    rsi_period = 14
    stoch_period = 14
    rsi_oversold = Decimal("30")
    stoch_oversold = Decimal("20")
    rsi_exit = Decimal("55")
    stoch_overbought = Decimal("80")
    atr_target_mult = Decimal("2.0")
    time_stop = 30
    entry_limit_bps = Decimal("15")  # rest the exhaustion bid below the mark
    exit_limit_bps = Decimal("15")   # rest the ATR profit target above the mark

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        values = closes(candles)
        r = rsi(values, self.rsi_period)
        k = stochastic_k(candles, self.stoch_period)
        if r is None or k is None:
            return []
        last = candles[-1]

        if not holding:
            recovery = last.close > last.open  # closed-candle recovery trigger
            if r <= self.rsi_oversold and k <= self.stoch_oversold and recovery:
                return [self.buy_intent(context, "exhaustion_recovery",
                                        limit_bps=self.entry_limit_bps)]
            return []

        if r >= self.rsi_exit:
            return [self.sell_all_intent(context, "rsi_recovered")]
        if k >= self.stoch_overbought:
            return [self.sell_all_intent(context, "stoch_overbought")]
        entry = self.entry_price(state)
        vol = atr(candles, self.rsi_period)
        if entry is not None and vol is not None and last.close >= entry + vol * self.atr_target_mult:
            return [self.sell_all_intent(context, "atr_target",
                                         limit_bps=self.exit_limit_bps)]
        if state.get("candles_held", 0) >= self.time_stop:
            return [self.sell_all_intent(context, "time_stop")]
        return []


def create_strategy() -> OscillatorExhaustion:
    return OscillatorExhaustion()
