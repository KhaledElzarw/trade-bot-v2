"""Strategy 1 — Volatility-Adaptive Inventory Grid.

Range capture: symmetric levels around a closed-candle anchor, spacing widened
by ATR, BUY depth skewed down as inventory grows, SELL only against owned BTC.
Recenters after configured displacement + minimum elapsed candles.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import atr


class VolAdaptiveGrid(BuiltinStrategy):
    name = "VolAdaptiveGrid"
    family = "inventory_grid"
    min_warmup = 20
    atr_period = 14
    spacing_atr_mult = Decimal("1.0")
    recenter_displacement = Decimal("0.03")  # 3% anchor displacement
    recenter_min_candles = 20
    max_inventory_fraction = Decimal("0.75")  # stop buying past this equity share
    min_edge = Decimal("0.004")  # fee-aware minimum edge for grid sells
    # A grid is the canonical resting-order strategy: bids rest below, asks above.
    entry_limit_bps = Decimal("10")
    exit_limit_bps = Decimal("10")

    def initialize(self) -> dict[str, Any]:
        state = super().initialize()
        state.update({"anchor": None, "anchor_index": -10_000})
        return state

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        close = candles[-1].close
        vol = atr(candles, self.atr_period)
        if vol is None or vol == 0:
            return []
        index = self.bar_ordinal(context.snapshot, candles)

        anchor = Decimal(state["anchor"]) if state.get("anchor") else None
        if anchor is None or (
            abs(close - anchor) / anchor > self.recenter_displacement
            and index - state.get("anchor_index", -10_000) >= self.recenter_min_candles
        ):
            state["anchor"] = str(close)
            state["anchor_index"] = index
            return []  # never trade on the recenter candle

        spacing = vol * self.spacing_atr_mult
        px = context.snapshot.mark_price
        equity = context.wallet.quote_cash + context.wallet.base_qty * px
        inventory_ratio = (
            (context.wallet.base_qty * px) / equity if equity > 0 else Decimal(1)
        )

        intents: list[IntentSpec | None] = []
        # BUY: price at/below anchor minus spacing, deepened by inventory skew.
        buy_level = anchor - spacing * (Decimal(1) + inventory_ratio)
        if close <= buy_level and inventory_ratio < self.max_inventory_fraction:
            intents.append(self.buy_intent(context, "grid_buy",
                                           fraction=Decimal("0.15"),
                                           limit_bps=self.entry_limit_bps))
        # SELL: only against owned BTC, above anchor plus spacing with fee edge.
        sell_level = anchor + spacing
        entry = self.entry_price(state)
        if holding and close >= sell_level and (
            entry is None or close >= entry * (Decimal(1) + self.min_edge)
        ):
            intents.append(self.sell_all_intent(context, "grid_sell",
                                                limit_bps=self.exit_limit_bps))
        return intents


def create_strategy() -> VolAdaptiveGrid:
    return VolAdaptiveGrid()
