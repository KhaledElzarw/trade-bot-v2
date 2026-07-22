"""Per-wallet analytics time series, reconstructed from candles + fills.

The read model holds only *current* wallet state — there is no stored equity or
P&L curve. Rather than fabricate history, we replay the wallet's own recorded
fills across the candle timeline and mark equity at each closed candle. Every
number is derived from data that actually happened; a wallet with no fills yields
a flat starting-capital line, and an empty candle set yields an empty series.

Kept deliberately free of web/persistence concerns so it is trivially testable:
pure functions over the same ``MarketSnapshot`` candles and the fill dicts the
API already exposes at ``/wallets/{id}/orders``.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from ..domain.market import MarketSnapshot

ZERO = Decimal("0")


def _parse_ms(iso: str | None) -> int | None:
    """Fill timestamps are ISO strings ending in 'Z'; back to epoch ms."""

    if not iso:
        return None
    try:
        cleaned = iso[:-1] if iso.endswith("Z") else iso
        return int(dt.datetime.fromisoformat(cleaned)
                   .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def _dec(value) -> Decimal:
    if value is None or value == "":
        return ZERO
    return Decimal(str(value))


def build_series(
    candles: tuple[MarketSnapshot, ...],
    fills: list[dict],
    *,
    starting_capital: Decimal = Decimal("10000.00"),
) -> list[dict]:
    """Reconstruct the analytics curve, one point per closed candle.

    ``fills`` are the wallet's filled order rows (any order); only rows with a
    ``filled_at`` matching a candle are applied. Each point carries the cumulative
    wallet state as of that candle's close:

        time            UNIX seconds (Lightweight Charts convention)
        equity          quote cash + base_qty * close
        realized_pnl    cumulative booked P&L
        unrealized_pnl  base_qty * (close - avg_cost)
        fees            cumulative fees paid
        exposure_pct    base value / equity, as a percentage
        btc_qty         base held
        trade_count     cumulative fills so far
    """

    if not candles:
        return []

    applied = sorted(
        (f for f in fills if f.get("status") == "filled"
         and _parse_ms(f.get("filled_at")) is not None),
        key=lambda f: _parse_ms(f.get("filled_at")),
    )

    cash = starting_capital
    btc = ZERO
    avg_cost = ZERO
    realized = ZERO
    fees = ZERO
    trades = 0
    idx = 0
    n = len(applied)

    out: list[dict] = []
    for candle in candles:
        close_ms = candle.close_time_ms
        # Apply every fill that completed on or before this candle's close.
        while idx < n and _parse_ms(applied[idx].get("filled_at")) <= close_ms:
            f = applied[idx]
            idx += 1
            qty = _dec(f.get("filled_qty"))
            price = _dec(f.get("price"))
            fee = _dec(f.get("fee"))
            fees += fee
            trades += 1
            if f.get("side") == "BUY":
                spent = qty * price
                new_btc = btc + qty
                # Fee-inclusive weighted average cost, matching the ledger.
                avg_cost = ((avg_cost * btc + spent + fee) / new_btc
                            if new_btc > 0 else ZERO)
                btc = new_btc
                cash -= spent + fee
            else:  # SELL
                cash += qty * price - fee
                btc -= qty
                realized += _dec(f.get("realized_pnl"))
                if btc <= 0:
                    btc = ZERO
                    avg_cost = ZERO

        close = candle.close
        base_value = btc * close
        equity = cash + base_value
        unrealized = base_value - btc * avg_cost if btc > 0 else ZERO
        exposure = (base_value / equity * Decimal("100")) if equity > 0 else ZERO
        out.append({
            "time": close_ms // 1000,
            "equity": f"{equity:.2f}",
            "realized_pnl": f"{realized:.2f}",
            "unrealized_pnl": f"{unrealized:.2f}",
            "fees": f"{fees:.2f}",
            "exposure_pct": f"{exposure:.2f}",
            "btc_qty": f"{btc:.8f}",
            "trade_count": trades,
        })
    return out


def activity_stats(fills: list[dict]) -> dict:
    """Win/loss + averages over a wallet's filled orders.

    A sell with positive realized P&L is a winning round-trip; a buy is a neutral
    open (never counted as win or loss). ``profit_factor`` is gross wins / gross
    losses.
    """

    filled = [f for f in fills if f.get("status") == "filled"]
    buys = [f for f in filled if f.get("side") == "BUY"]
    sells = [f for f in filled if f.get("side") == "SELL"]

    wins = [f for f in sells if _dec(f.get("realized_pnl")) > 0]
    losses = [f for f in sells if _dec(f.get("realized_pnl")) < 0]
    gross_win = sum((_dec(f.get("realized_pnl")) for f in wins), ZERO)
    gross_loss = sum((-_dec(f.get("realized_pnl")) for f in losses), ZERO)

    def avg(rows, total) -> str | None:
        return f"{(total / Decimal(len(rows))):.2f}" if rows else None

    win_rate = (f"{Decimal(len(wins)) / Decimal(len(sells)) * 100:.1f}%"
                if sells else None)
    profit_factor = (f"{(gross_win / gross_loss):.2f}"
                     if gross_loss > 0 else None)

    return {
        "trade_count": len(filled),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": win_rate,
        "avg_win": avg(wins, gross_win),
        "avg_loss": avg(losses, gross_loss),
        "profit_factor": profit_factor,
    }


def reason_breakdown(fills: list[dict]) -> list[dict]:
    """Count filled orders by reason code, most frequent first.

    This is strategy-agnostic: whatever reason codes a wallet's current strategy
    emitted (``grid_buy``, ``z_deep``, ``committee_accumulate``, …) are surfaced
    as-is, so the panel follows the strategy without hardcoding.
    """

    counts: dict[str, int] = {}
    for f in fills:
        if f.get("status") != "filled":
            continue
        reason = f.get("reason") or "—"
        counts[reason] = counts.get(reason, 0) + 1
    return [{"reason": r, "count": c}
            for r, c in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]
