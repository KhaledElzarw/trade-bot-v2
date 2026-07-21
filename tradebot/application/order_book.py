"""A carry-over resting order book for the paper harness.

`ExecutionService` deliberately fills-or-rejects each intent against a single
candle — a non-marketable LIMIT is rejected, never parked. That is the correct
model for its audited one-fill-per-candle semantics, but it means a strategy can
never have an order *resting* on the book waiting for price to come to it, so
the dashboard's "open orders" is structurally always zero.

This book adds the missing piece WITHOUT touching the audited engine: it holds
the LIMIT orders that are not yet marketable, and on each new candle it decides
which resting orders have become fillable (the candle traded through the limit),
which have expired, and hands the fillable ones back to the caller to submit to
`ExecutionService` as normal LIMIT intents. Every fill, fee and ledger posting
still happens inside `ExecutionService`; this layer is pure bookkeeping.

Model assumptions:
* a wallet may hold a small LADDER of resting orders (a grid rests several
  bids/asks), capped at ``MAX_PER_WALLET`` — the oldest is dropped past the cap;
* a BUY limit is fillable when ``snapshot.low <= limit``; a SELL limit when
  ``snapshot.high >= limit`` (the candle traded through the price);
* at most ONE order per wallet fills per candle (the audited ExecutionService
  allows one fill per wallet per candle); any other orders that also became
  marketable stay on the book and are re-checked next candle;
* an order expires after ``expires_after_candles`` unfilled candles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..domain.ledger import Side
from ..domain.market import MarketSnapshot

DEFAULT_EXPIRY_CANDLES = 24  # 2 hours at a 5-minute cadence
MAX_PER_WALLET = 5  # cap the resting ladder so a runaway loop can't grow it


@dataclass(frozen=True, slots=True)
class RestingOrder:
    """One LIMIT order sitting on the book waiting for price to reach it."""

    order_id: str
    wallet_id: str
    strategy_version_id: str
    side: Side
    limit_price: Decimal
    quantity: Decimal
    reason_code: str
    placed_open_ms: int
    expires_after_candles: int = DEFAULT_EXPIRY_CANDLES

    def is_marketable(self, snapshot: MarketSnapshot) -> bool:
        """True when this candle traded through the limit price."""

        if self.side is Side.BUY:
            return snapshot.low <= self.limit_price
        return snapshot.high >= self.limit_price

    def candles_elapsed(self, snapshot: MarketSnapshot, step_ms: int) -> int:
        if step_ms <= 0:
            return 0
        return (snapshot.open_time_ms - self.placed_open_ms) // step_ms


@dataclass(slots=True)
class RestingBook:
    """A per-wallet ladder of resting LIMIT orders."""

    _orders: dict[str, list[RestingOrder]] = field(default_factory=dict)
    _step_ms: int | None = None

    def observe_spacing(self, snapshot: MarketSnapshot,
                        prev: MarketSnapshot | None) -> None:
        """Learn the candle spacing so expiry is measured in real candles."""

        if prev is not None:
            step = snapshot.open_time_ms - prev.open_time_ms
            if step > 0:
                self._step_ms = step

    def rest(self, order: RestingOrder) -> None:
        """Add a resting order to the wallet's ladder (oldest dropped past cap)."""

        ladder = self._orders.setdefault(order.wallet_id, [])
        ladder.append(order)
        if len(ladder) > MAX_PER_WALLET:
            del ladder[0]

    def cancel(self, wallet_id: str) -> list[RestingOrder]:
        return self._orders.pop(wallet_id, [])

    def has_order(self, wallet_id: str) -> bool:
        return bool(self._orders.get(wallet_id))

    def count(self, wallet_id: str) -> int:
        return len(self._orders.get(wallet_id, []))

    def expire(self, snapshot: MarketSnapshot) -> list[RestingOrder]:
        """Remove and return orders that have sat unfilled past their TTL."""

        step = self._step_ms or 0
        if not step:
            return []
        expired: list[RestingOrder] = []
        for wallet_id, ladder in list(self._orders.items()):
            kept = []
            for order in ladder:
                if order.candles_elapsed(snapshot, step) > order.expires_after_candles:
                    expired.append(order)
                else:
                    kept.append(order)
            self._orders[wallet_id] = kept
        return expired

    def due_fills(self, snapshot: MarketSnapshot) -> list[RestingOrder]:
        """Remove and return AT MOST ONE marketable order per wallet.

        The audited ExecutionService fills a wallet at most once per candle, so
        surfacing more than one due order per wallet would only produce spurious
        rejects. Any other marketable orders stay resting for the next candle.
        """

        due: list[RestingOrder] = []
        for wallet_id, ladder in self._orders.items():
            for i, order in enumerate(ladder):
                if order.is_marketable(snapshot):
                    due.append(order)
                    del ladder[i]
                    break
        return due

    def snapshot_open(self) -> dict[str, list[dict]]:
        """The currently-resting set, as JSON rows for the read model."""

        out: dict[str, list[dict]] = {}
        for wallet_id, ladder in self._orders.items():
            if not ladder:
                continue
            out[wallet_id] = [{
                "order_id": o.order_id,
                "side": o.side.value,
                "order_type": "LIMIT",
                "limit_price": f"{o.limit_price:f}",
                "quantity": f"{o.quantity:f}",
                "reason_code": o.reason_code,
                "status": "open",
            } for o in ladder]
        return out
