"""Portfolio composition: 12 active + 12 shadow + 2 permanent wallets
(Dark Horse and Darkhorse - Daily).

Wallet naming rule: display names are ``StrategyName_DaysSinceStrategyChanged``
computed from the current assignment's activation timestamp. Display names are
never keys; identity is the immutable wallet_id.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from ..domain.ledger import Wallet
from ..domain.money import quote

ACTIVE_COUNT = 12
SHADOW_COUNT = 12
STARTING_BALANCE = Decimal("10000.00")
# 12 active + Dark Horse + Darkhorse - Daily
ACTIVE_BASELINE = Decimal("140000.00")
DARK_HORSE_DISPLAY_NAME = "Dark Horse"
DARK_HORSE_DAILY_DISPLAY_NAME = "Darkhorse - Daily"


@dataclass(frozen=True, slots=True)
class WalletSlot:
    wallet: Wallet
    kind: str  # active | shadow | dark_horse | dark_horse_daily
    strategy_name: str
    strategy_version_id: str
    activated_at: dt.datetime


def display_name(slot: WalletSlot, now: dt.datetime) -> str:
    """``StrategyName_DaysSinceStrategyChanged`` (permanent wallets are fixed)."""

    if slot.kind == "dark_horse":
        return DARK_HORSE_DISPLAY_NAME
    if slot.kind == "dark_horse_daily":
        return DARK_HORSE_DAILY_DISPLAY_NAME
    days = max(0, (now - slot.activated_at).days)
    return f"{slot.strategy_name}_{days}"


@dataclass(slots=True)
class Portfolio:
    active: list[WalletSlot] = field(default_factory=list)
    shadow: list[WalletSlot] = field(default_factory=list)
    dark_horse: WalletSlot | None = None
    dark_horse_daily: WalletSlot | None = None

    def active_equity(self, mark_price: Decimal) -> Decimal:
        total = sum(
            (s.wallet.equity(mark_price) for s in self.active), start=Decimal("0")
        )
        if self.dark_horse is not None:
            total += self.dark_horse.wallet.equity(mark_price)
        if self.dark_horse_daily is not None:
            total += self.dark_horse_daily.wallet.equity(mark_price)
        return quote(total)

    def shadow_equity(self, mark_price: Decimal) -> Decimal:
        """Virtual evaluation capital — never mixed into the active total."""

        return quote(sum(
            (s.wallet.equity(mark_price) for s in self.shadow), start=Decimal("0")
        ))


def seed_portfolio(
    strategy_names: list[str],
    *,
    now: dt.datetime,
    id_factory: Callable[[str], str],
) -> Portfolio:
    """Create the initial 12 active + 12 shadow + Dark Horse composition.

    ``strategy_names`` must contain exactly 12 distinct names; each is assigned
    to one active and one shadow wallet. ``id_factory`` is injected so IDs are
    deterministic in tests and ULIDs in production.
    """

    if len(strategy_names) != ACTIVE_COUNT:
        raise ValueError(f"expected {ACTIVE_COUNT} strategies, got {len(strategy_names)}")
    if len(set(strategy_names)) != ACTIVE_COUNT:
        raise ValueError("strategy names must be distinct")

    portfolio = Portfolio()
    for name in strategy_names:
        for kind, bucket in (("active", portfolio.active), ("shadow", portfolio.shadow)):
            wallet_id = id_factory(f"{kind}-{name}")
            bucket.append(
                WalletSlot(
                    wallet=Wallet(wallet_id, quote_cash=quote(STARTING_BALANCE)),
                    kind=kind,
                    strategy_name=name,
                    strategy_version_id=f"builtin-{name}-v1",
                    activated_at=now,
                )
            )
    portfolio.dark_horse = WalletSlot(
        wallet=Wallet(id_factory("dark-horse"), quote_cash=quote(STARTING_BALANCE)),
        kind="dark_horse",
        strategy_name="DarkHorse",
        strategy_version_id="dark-horse-v1",
        activated_at=now,
    )
    portfolio.dark_horse_daily = WalletSlot(
        wallet=Wallet(id_factory("dark-horse-daily"),
                      quote_cash=quote(STARTING_BALANCE)),
        kind="dark_horse_daily",
        strategy_name="DarkhorseDaily",
        strategy_version_id="dark-horse-daily-v1",
        activated_at=now,
    )
    return portfolio
