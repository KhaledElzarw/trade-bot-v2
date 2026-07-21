"""Strategy 2 — Bollinger Z-Score Mean Reversion.

Statistical deviation reversion (not a standing grid): BUY on deep negative
z-score when the trend filter shows no uncontrolled downtrend; exit at the
rolling mean, or on time stop / trend invalidation.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import closes, sma, zscore


class BollingerZScore(BuiltinStrategy):
    name = "BollingerZScore"
    family = "mean_reversion"
    min_warmup = 40
    z_period = 20
    entry_z = Decimal("-2.0")
    deep_z = Decimal("-3.0")  # staged deeper entry
    trend_period = 40
    trend_crash_pct = Decimal("0.10")  # 10% drop over trend window = downtrend
    time_stop = 30
    entry_limit_bps = Decimal("15")  # rest the mean-reversion bid below the mark

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        values = closes(candles)
        z = zscore(values, self.z_period)
        mean = sma(values, self.z_period)
        if z is None or mean is None:
            return []
        # Trend filter: refuse to catch a falling knife.
        window_start = values[-self.trend_period]
        crashing = (window_start - values[-1]) / window_start > self.trend_crash_pct

        if not holding:
            if crashing:
                return []
            if z <= self.deep_z:
                return [self.buy_intent(context, "z_deep", fraction=Decimal("0.40"),
                                        limit_bps=self.entry_limit_bps)]
            if z <= self.entry_z:
                return [self.buy_intent(context, "z_entry",
                                        limit_bps=self.entry_limit_bps)]
            return []

        # Exits: mean touch, time stop, or thesis-invalidating downtrend.
        if values[-1] >= mean:
            return [self.sell_all_intent(context, "z_mean_exit")]
        if state.get("candles_held", 0) >= self.time_stop:
            return [self.sell_all_intent(context, "z_time_stop")]
        if crashing:
            return [self.sell_all_intent(context, "z_trend_invalidated")]
        return []


def create_strategy() -> BollingerZScore:
    return BollingerZScore()
