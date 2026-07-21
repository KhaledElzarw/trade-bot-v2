"""Strategy 6 — EMA Trend Pullback.

Trend continuation: establish a bullish trend (EMA20 > EMA50, positive slope),
BUY a controlled pullback toward EMA20 after a closed-candle recovery, exit on
trend-structure failure or profit target.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import closes, ema_series


class EmaPullback(BuiltinStrategy):
    name = "EmaPullback"
    family = "trend_pullback"
    min_warmup = 60
    fast = 20
    slow = 50
    pullback_tolerance = Decimal("0.005")  # within 0.5% of EMA20
    profit_target = Decimal("0.03")
    entry_limit_bps = Decimal("15")  # rest the pullback bid below the mark
    exit_limit_bps = Decimal("20")   # rest the profit target above the mark

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        values = closes(candles)
        fast_s = ema_series(values, self.fast)
        slow_s = ema_series(values, self.slow)
        if len(fast_s) < 3 or len(slow_s) < 2:
            return []
        ema_fast, ema_slow = fast_s[-1], slow_s[-1]
        uptrend = ema_fast > ema_slow and fast_s[-1] > fast_s[-3]  # positive slope
        last = candles[-1]

        if not holding:
            touched = last.low <= ema_fast * (Decimal(1) + self.pullback_tolerance)
            recovered = last.close > last.open and last.close > ema_fast
            if uptrend and touched and recovered:
                return [self.buy_intent(context, "ema_pullback",
                                        fraction=Decimal("0.35"),
                                        limit_bps=self.entry_limit_bps)]
            return []

        # Trend structure failure ends the trade.
        if ema_fast < ema_slow:
            return [self.sell_all_intent(context, "trend_failure")]
        entry = self.entry_price(state)
        if entry is not None and last.close >= entry * (Decimal(1) + self.profit_target):
            return [self.sell_all_intent(context, "profit_target",
                                         limit_bps=self.exit_limit_bps)]
        return []


def create_strategy() -> EmaPullback:
    return EmaPullback()
