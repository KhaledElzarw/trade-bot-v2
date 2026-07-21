"""Shared plumbing for built-in strategies.

Only mechanics live here (warmup, cooldown, sizing, stale-data guards); every
signal decision is implemented per-strategy so the twelve built-ins are
materially distinct, not parameter presets of one function.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..domain.ledger import Side
from ..domain.market import MarketSnapshot
from ..domain.money import base as base_qty
from ..domain.money import quote
from ..domain.strategies import (
    IntentSpec,
    StrategyContext,
    StrategyDecision,
    StrategyMetadata,
)


class BuiltinStrategy:
    """Base: subclasses implement ``signal`` returning intents."""

    name = "Builtin"
    family = "builtin"
    min_warmup = 30
    cooldown_candles = 1
    entry_fraction = Decimal("0.25")  # fraction of quote cash per entry

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_id=f"builtin-{self.name}",
            strategy_version_id=f"builtin-{self.name}-v1",
            name=self.name,
            family=self.family,
            origin="builtin",
            required_intervals=("1m", "5m", "15m", "1h"),
            min_warmup_candles=self.min_warmup,
        )

    def initialize(self) -> dict[str, Any]:
        return {"last_entry_index": -10_000, "entry_price": None, "candles_held": 0}

    # A limit offset (in basis points) turns an entry/exit into a RESTING order
    # placed off the mark instead of a market order. ``None`` keeps the original
    # market behaviour; individual strategies opt in per call where a resting
    # order is natural (mean-reversion entries, take-profit exits). Stops,
    # invalidations and breakout entries deliberately stay market — they must
    # fill immediately, not sit on the book.
    entry_limit_bps: Decimal | None = None
    exit_limit_bps: Decimal | None = None

    # -- helpers -------------------------------------------------------------

    def buy_intent(self, context: StrategyContext, reason: str,
                   fraction: Decimal | None = None,
                   limit_bps: Decimal | None = None) -> IntentSpec | None:
        cash = context.wallet.quote_cash
        px = context.snapshot.mark_price
        budget = quote(cash * (fraction or self.entry_fraction))
        if budget < Decimal("10"):
            return None
        limit_price = None
        fill_px = px
        if limit_bps is not None and limit_bps > 0:
            # Rest the bid BELOW the mark: buy the dip at a better price.
            limit_price = quote(px * (Decimal(1) - Decimal(limit_bps) / Decimal(10_000)))
            fill_px = limit_price
        qty = base_qty(budget / fill_px)
        if qty <= 0:
            return None
        return IntentSpec(
            side=Side.BUY,
            order_type="LIMIT" if limit_price is not None else "MARKET",
            quantity=qty, limit_price=limit_price, reason_code=reason)

    def sell_all_intent(self, context: StrategyContext, reason: str,
                        limit_bps: Decimal | None = None) -> IntentSpec | None:
        held = context.wallet.base_qty
        if held <= 0:
            return None
        limit_price = None
        if limit_bps is not None and limit_bps > 0:
            # Rest the ask ABOVE the mark: take profit at a target price.
            px = context.snapshot.mark_price
            limit_price = quote(px * (Decimal(1) + Decimal(limit_bps) / Decimal(10_000)))
        return IntentSpec(
            side=Side.SELL,
            order_type="LIMIT" if limit_price is not None else "MARKET",
            quantity=held, limit_price=limit_price, reason_code=reason)

    # -- template ------------------------------------------------------------

    def on_market_snapshot(self, context: StrategyContext,
                           state: dict[str, Any]) -> StrategyDecision:
        candles = context.candles
        # Stale/insufficient data: hold (explicit stale-data behaviour).
        if not context.snapshot.is_closed or len(candles) < self.min_warmup:
            return StrategyDecision(state=state)

        index = self.bar_ordinal(context.snapshot, candles)
        holding = context.wallet.base_qty > 0
        new_state = dict(state)
        if holding:
            new_state["candles_held"] = state.get("candles_held", 0) + 1

        intents = self.signal(context, candles, new_state, holding=holding)

        filtered: list[IntentSpec] = []
        for intent in intents:
            if intent is None:
                continue
            if intent.side is Side.BUY:
                if index - new_state.get("last_entry_index", -10_000) < self.cooldown_candles:
                    continue  # cooldown between entries
                new_state["last_entry_index"] = index
                new_state["entry_price"] = str(context.snapshot.mark_price)
                new_state["candles_held"] = 0
            else:
                new_state["entry_price"] = None
                new_state["candles_held"] = 0
            filtered.append(intent)
        return StrategyDecision(intents=tuple(filtered), state=new_state)

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        raise NotImplementedError

    @staticmethod
    def bar_ordinal(snapshot: MarketSnapshot,
                    candles: tuple[MarketSnapshot, ...]) -> int:
        """A monotonic, window-independent candle clock.

        Time-based gates (entry cooldowns, grid re-centres, signal re-arming)
        must measure ELAPSED candles, not the length of the trailing window they
        happen to be handed. ``len(candles)`` conflates the two: once the caller
        caps the window (the dev harness feeds only the last 150 candles) its
        length flatlines, every ``index - last >= N`` gate reads "no time has
        passed", and the strategy stops trading after the window fills. This
        derives the ordinal from the newest candle's open time and the candle
        spacing instead, so it keeps climbing across the whole run regardless of
        how much history is retained.
        """

        if len(candles) >= 2:
            step = candles[-1].open_time_ms - candles[-2].open_time_ms
            if step > 0:
                return snapshot.open_time_ms // step
        # Degenerate fallback (single candle / zero spacing): still monotonic in
        # open time, so gates never freeze even here.
        return snapshot.open_time_ms

    @staticmethod
    def entry_price(state: dict[str, Any]) -> Decimal | None:
        raw = state.get("entry_price")
        return Decimal(raw) if raw else None
