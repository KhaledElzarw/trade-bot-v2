"""Strategy 7 — MACD Histogram Momentum Acceleration.

Momentum transition: BUY when the histogram crosses positive or shows renewed
acceleration in a bullish context; exit on histogram deceleration streak,
negative cross, or maximum holding period.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import closes, macd_histogram


class MacdMomentum(BuiltinStrategy):
    name = "MacdMomentum"
    family = "macd_momentum"
    min_warmup = 60
    # Consecutive shrinking-histogram candles that end a still-positive trade.
    # Kept at 2: the histogram crosses below zero within ~3 candles of momentum
    # peaking, so a longer streak would always be pre-empted by the negative
    # cross below and the deceleration exit could never fire.
    decel_streak_exit = 2
    max_hold = 60

    @staticmethod
    def declining_streak(hist: list[Decimal]) -> int:
        """Count consecutive shrinking histogram values, newest first."""

        streak = 0
        for i in range(len(hist) - 1, 0, -1):
            if hist[i] < hist[i - 1]:
                streak += 1
            else:
                break
        return streak

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        hist = macd_histogram(closes(candles))
        if len(hist) < 4:
            return []
        h1, h2, h3 = hist[-3], hist[-2], hist[-1]

        if not holding:
            crossed_positive = h2 <= 0 < h3
            accelerating = h3 > 0 and h3 > h2 > h1  # renewed acceleration
            if crossed_positive or accelerating:
                return [self.buy_intent(context, "macd_momentum",
                                        fraction=Decimal("0.30"))]
            return []

        if h3 < 0:
            return [self.sell_all_intent(context, "macd_negative_cross")]
        if self.declining_streak(hist) >= self.decel_streak_exit:
            # Momentum fading while still positive: exit before the cross.
            return [self.sell_all_intent(context, "macd_deceleration")]
        if state.get("candles_held", 0) >= self.max_hold:
            return [self.sell_all_intent(context, "max_hold")]
        return []


def create_strategy() -> MacdMomentum:
    return MacdMomentum()
