"""Strategy 3 — Rolling VWAP Deviation Reversion.

Volume-weighted fair-value reversion: BUY when price sits materially below
rolling VWAP with adequate liquidity and decelerating downside momentum; exit
on VWAP touch, positive deviation target, time stop, or volume-confirmed
breakdown.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import relative_volume, rolling_vwap


class VwapReversion(BuiltinStrategy):
    name = "VwapReversion"
    family = "vwap_reversion"
    min_warmup = 40
    vwap_period = 30
    entry_deviation = Decimal("-0.015")  # 1.5% below VWAP
    target_deviation = Decimal("0.005")
    min_rel_volume = Decimal("0.8")
    breakdown_rel_volume = Decimal("2.0")
    time_stop = 40
    entry_limit_bps = Decimal("15")  # rest the discount bid below the mark
    exit_limit_bps = Decimal("15")   # rest the profit target above the mark

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        vwap = rolling_vwap(candles, self.vwap_period)
        rel_vol = relative_volume(candles, self.vwap_period)
        if vwap is None or rel_vol is None or vwap == 0:
            return []
        close = candles[-1].close
        deviation = (close - vwap) / vwap
        # Downside deceleration: last drop smaller than the one before it.
        d1 = candles[-1].close - candles[-2].close
        d2 = candles[-2].close - candles[-3].close
        decelerating = d2 < 0 and d1 > d2

        if not holding:
            if (deviation <= self.entry_deviation
                    and rel_vol >= self.min_rel_volume and decelerating):
                return [self.buy_intent(context, "vwap_entry",
                                        limit_bps=self.entry_limit_bps)]
            return []

        if deviation >= 0:
            return [self.sell_all_intent(context, "vwap_touch")]
        if deviation >= self.target_deviation:
            return [self.sell_all_intent(context, "vwap_target",
                                         limit_bps=self.exit_limit_bps)]
        if state.get("candles_held", 0) >= self.time_stop:
            return [self.sell_all_intent(context, "vwap_time_stop")]
        # Volume-confirmed breakdown: heavy volume and a new local low.
        if rel_vol >= self.breakdown_rel_volume and close < min(
            c.low for c in candles[-5:-1]
        ):
            return [self.sell_all_intent(context, "vwap_breakdown")]
        return []


def create_strategy() -> VwapReversion:
    return VwapReversion()
