"""Read-model protocol + an in-memory implementation for the API.

Keeps the HTTP layer free of persistence details (dependency injection): the
production adapter reads from the canonical database; tests inject a fake.

Active and shadow totals are computed separately here and never mixed — the
API cannot accidentally report a combined figure.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from ..application.portfolio import (
    ACTIVE_BASELINE,
    Portfolio,
    display_name,
)


def money(value: Decimal) -> str:
    """Plain decimal string for the API/UI.

    `str(Decimal("0E-8"))` renders as ``0E-8`` — scientific notation that is
    meaningless in a balance column. Normalizing through a fixed-point format
    keeps the exact value while emitting ``0.00000000``.
    """

    return f"{value:f}"


class PortfolioView(Protocol):  # pragma: no cover - structural protocol
    def readiness(self) -> dict: ...
    def portfolio_summary(self) -> dict: ...
    def wallets(self, kind: str | None = None) -> list[dict]: ...
    def wallet(self, wallet_id: str) -> dict | None: ...
    def wallet_equity(self, wallet_id: str) -> list[dict]: ...
    def wallet_orders(self, wallet_id: str) -> list[dict]: ...
    def wallet_fills(self, wallet_id: str) -> list[dict]: ...
    def wallet_ledger(self, wallet_id: str) -> list[dict]: ...
    def strategies(self) -> list[dict]: ...
    def strategy(self, strategy_version_id: str) -> dict | None: ...
    def lineage(self) -> list[dict]: ...
    def evaluations(self, window: str | None = None) -> list[dict]: ...
    def promotions(self) -> list[dict]: ...
    def reports_daily(self, date: str | None = None) -> list[dict]: ...
    def reports_weekly(self, window: str | None = None) -> list[dict]: ...
    def quarantines(self) -> list[dict]: ...
    def data_sources(self) -> list[dict]: ...
    def llm_status(self) -> dict: ...
    def refresh_daily(self, date: str) -> dict: ...


@dataclass(slots=True)
class InMemoryPortfolioView:
    """Read model over an in-memory Portfolio (used by tests and the seeder)."""

    portfolio: Portfolio
    mark_price: Decimal
    now: dt.datetime
    archived_lifetime_pnl: Decimal = Decimal("0.00")
    llm_healthy: bool = True
    llm_model_id: str = "unknown"
    source_status: list[dict] = field(default_factory=list)
    daily_reports: list[dict] = field(default_factory=list)
    weekly_reports: list[dict] = field(default_factory=list)
    quarantine_records: list[dict] = field(default_factory=list)
    lineage_edges: list[dict] = field(default_factory=list)
    promotion_records: list[dict] = field(default_factory=list)
    evaluation_records: list[dict] = field(default_factory=list)
    # Per-wallet drill-down data (populated by the seeder/harness; empty here).
    trades_by_wallet: dict[str, list[dict]] = field(default_factory=dict)
    open_orders_by_wallet: dict[str, list[dict]] = field(default_factory=dict)
    strategy_descriptions: dict[str, str] = field(default_factory=dict)

    # -- system --------------------------------------------------------------

    def readiness(self) -> dict:
        return {
            "ready": True,
            "database": "ok",
            "market_data": "ok",
            "local_model": "ok" if self.llm_healthy else "degraded",
            "strategy_workers": "ok",
        }

    def llm_status(self) -> dict:
        return {
            "provider": "llama_cpp",
            "status": "ok" if self.llm_healthy else "degraded",
            "model_id": self.llm_model_id,
        }

    def data_sources(self) -> list[dict]:
        return list(self.source_status)

    # -- portfolio -----------------------------------------------------------

    def portfolio_summary(self) -> dict:
        """Active and shadow figures are strictly separate sections."""

        active_equity = self.portfolio.active_equity(self.mark_price)
        return {
            "active": {
                "starting_capital": money(ACTIVE_BASELINE),
                "current_equity": money(active_equity),
                "net_pnl": money(active_equity - ACTIVE_BASELINE),
                "archived_lifetime_net_pnl": money(self.archived_lifetime_pnl),
                "wallet_count": len(self.portfolio.active),
            },
            "shadow": {
                "virtual_equity": money(self.portfolio.shadow_equity(self.mark_price)),
                "wallet_count": len(self.portfolio.shadow),
                "note": "virtual evaluation capital; excluded from active totals",
            },
            "dark_horse": self._permanent_summary(self.portfolio.dark_horse),
            "dark_horse_daily": self._permanent_summary(
                self.portfolio.dark_horse_daily),
            "mark_price": money(self.mark_price),
        }

    def _permanent_summary(self, slot) -> dict | None:
        if slot is None:
            return None
        equity = slot.wallet.equity(self.mark_price)
        return {
            "wallet_id": slot.wallet.wallet_id,
            "display_name": display_name(slot, self.now),
            "current_equity": money(equity),
            "lifetime_net_pnl": money(equity - Decimal("10000.00")),
        }

    # -- wallets -------------------------------------------------------------

    def _slots(self):
        out = [(s, "active") for s in self.portfolio.active]
        out += [(s, "shadow") for s in self.portfolio.shadow]
        if self.portfolio.dark_horse is not None:
            out.append((self.portfolio.dark_horse, "dark_horse"))
        if self.portfolio.dark_horse_daily is not None:
            out.append((self.portfolio.dark_horse_daily, "dark_horse_daily"))
        return out

    def _wallet_dict(self, slot, kind: str) -> dict:
        w = slot.wallet
        equity = w.equity(self.mark_price)
        completed = sum(1 for t in self.trades_by_wallet.get(w.wallet_id, [])
                        if t.get("status") == "filled")
        return {
            "wallet_id": w.wallet_id,
            "display_name": display_name(slot, self.now),
            "kind": kind,
            "strategy_name": slot.strategy_name,
            "strategy_version_id": slot.strategy_version_id,
            "days_since_assignment_changed": max(
                0, (self.now - slot.activated_at).days),
            "starting_equity": "10000.00",
            "current_equity": money(equity),
            "lifetime_net_pnl": money(equity - Decimal("10000.00")),
            "unrealized_pnl": money(w.unrealized_pnl(self.mark_price)),
            "total_fees": money(w.total_fees),
            "btc_quantity": money(w.base_qty),
            "usdt_quantity": money(w.quote_cash),
            "realized_pnl": money(w.realized_pnl),
            "open_orders": len(self.open_orders_by_wallet.get(w.wallet_id, [])),
            "completed_orders": completed,
            "status": "active",
            "health": "ok",
        }

    def wallets(self, kind: str | None = None) -> list[dict]:
        return [self._wallet_dict(s, k) for s, k in self._slots()
                if kind is None or k == kind]

    def wallet(self, wallet_id: str) -> dict | None:
        for slot, kind in self._slots():
            if slot.wallet.wallet_id == wallet_id:
                detail = self._wallet_dict(slot, kind)
                detail["strategy_description"] = self.strategy_descriptions.get(
                    slot.strategy_version_id, "")
                detail["insights"] = self._insights(slot)
                detail["open_orders"] = self.wallet_open_orders(wallet_id)
                return detail
        return None

    def _insights(self, slot) -> dict:
        """Compact performance snapshot for the wallet drill-down."""

        w = slot.wallet
        filled = [t for t in self.trades_by_wallet.get(w.wallet_id, [])
                  if t.get("status") == "filled"]
        buys = [t for t in filled if t.get("side") == "BUY"]
        sells = [t for t in filled if t.get("side") == "SELL"]
        wins = sum(1 for t in sells
                   if Decimal(t.get("realized_pnl") or "0") > 0)
        win_rate = (f"{Decimal(wins) / Decimal(len(sells)) * 100:.1f}%"
                    if sells else None)
        equity = w.equity(self.mark_price)
        return {
            "current_equity": money(equity),
            "lifetime_net_pnl": money(equity - Decimal("10000.00")),
            "realized_pnl": money(w.realized_pnl),
            "unrealized_pnl": money(w.unrealized_pnl(self.mark_price)),
            "total_fees": money(w.total_fees),
            "avg_cost": money(w.avg_cost),
            "btc_quantity": money(w.base_qty),
            "usdt_quantity": money(w.quote_cash),
            "trade_count": len(filled),
            "buy_count": len(buys),
            "sell_count": len(sells),
            "win_count": wins,
            "win_rate": win_rate,
        }

    def wallet_equity(self, wallet_id: str) -> list[dict]:
        found = self.wallet(wallet_id)
        if found is None:
            return []
        return [{"time": self.now.isoformat(), "equity": found["current_equity"]}]

    def wallet_orders(self, wallet_id: str) -> list[dict]:
        """Full order history (filled + rejected), newest first."""

        return list(reversed(self.trades_by_wallet.get(wallet_id, [])))

    def wallet_fills(self, wallet_id: str) -> list[dict]:
        return [t for t in self.wallet_orders(wallet_id)
                if t.get("status") == "filled"]

    def wallet_open_orders(self, wallet_id: str) -> list[dict]:
        """Resting orders awaiting a fill. The paper harness executes or
        rejects every intent on its own candle, so this is normally empty."""

        return list(self.open_orders_by_wallet.get(wallet_id, []))

    def wallet_ledger(self, wallet_id: str) -> list[dict]:
        return []

    # -- strategies / evolution ---------------------------------------------

    def strategies(self) -> list[dict]:
        seen: dict[str, dict] = {}
        for slot, kind in self._slots():
            seen.setdefault(slot.strategy_version_id, {
                "strategy_version_id": slot.strategy_version_id,
                "name": slot.strategy_name,
                "origin": "builtin",
                "kind": kind,
            })
        return list(seen.values())

    def strategy(self, strategy_version_id: str) -> dict | None:
        for s in self.strategies():
            if s["strategy_version_id"] == strategy_version_id:
                return s
        return None

    def lineage(self) -> list[dict]:
        return list(self.lineage_edges)

    def evaluations(self, window: str | None = None) -> list[dict]:
        if window is None:
            return list(self.evaluation_records)
        return [e for e in self.evaluation_records
                if e.get("evaluation_window") == window]

    def promotions(self) -> list[dict]:
        return list(self.promotion_records)

    def quarantines(self) -> list[dict]:
        return list(self.quarantine_records)

    # -- reports -------------------------------------------------------------

    def reports_daily(self, date: str | None = None) -> list[dict]:
        if date is None:
            return list(self.daily_reports)
        return [r for r in self.daily_reports if r.get("date") == date]

    def reports_weekly(self, window: str | None = None) -> list[dict]:
        if window is None:
            return list(self.weekly_reports)
        return [r for r in self.weekly_reports
                if r.get("evaluation_window") == window]

    def refresh_daily(self, date: str) -> dict:
        return {"queued": True, "date": date}
