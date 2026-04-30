import json
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
STATE_PATH = BASE / "state.json"
DATA_PATH = Path("/home/claw/AgileSquad/runtime/freqtrade/user_data/data/binance/BTC_USDT-1m.feather")
OUT_PATH = BASE / "grid_honest_replay.json"
EQUITY_LOG_PATH = BASE / "grid_honest_replay_equity.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("Not enough values for EMA")
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def _atr(high: list[float], low: list[float], close: list[float], period: int = 14) -> float:
    if len(close) < period + 1:
        raise ValueError("Not enough data for ATR")
    trs = []
    for i in range(1, len(close)):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
        trs.append(tr)
    window = trs[-period:]
    return sum(window) / len(window)


@dataclass
class PaperAccount:
    usdt: float
    btc: float

    def equity(self, price: float) -> float:
        return self.usdt + self.btc * price


@dataclass
class GridOrder:
    side: str
    price: float
    qty_btc: float


@dataclass
class GridState:
    anchor: float
    spacing_pct: float
    levels: int
    max_exposure_pct: float
    reserved_usdt: float
    reserved_btc: float
    cost_basis_usdt: float
    orders: list
    active: bool = False
    last_recenter_utc: str | None = None


def _spacing_for_mode(mode: str, atr: float, price: float, *, min_scalpy: float, min_fatty: float) -> tuple[float, int]:
    atr_pct = atr / price if price else 0.0
    if mode == "fatty":
        return max(min_fatty, 1.4 * atr_pct), 8
    return max(min_scalpy, 0.8 * atr_pct), 14


def _build_grid_orders(anchor: float, spacing_pct: float, levels: int, qty_per_level: float) -> list[GridOrder]:
    orders = []
    for i in range(1, levels + 1):
        orders.append(GridOrder("BUY", anchor * ((1 - spacing_pct) ** i), qty_per_level))
        orders.append(GridOrder("SELL", anchor * ((1 + spacing_pct) ** i), qty_per_level))
    buys = sorted([o for o in orders if o.side == "BUY"], key=lambda o: o.price, reverse=True)
    sells = sorted([o for o in orders if o.side == "SELL"], key=lambda o: o.price)
    return buys + sells


def _fill_order_paper(paper: PaperAccount, grid: GridState, o: GridOrder, fill_price: float, fee_bps: float):
    fee_rate = max(0.0, fee_bps) / 10000.0
    if o.side == "BUY":
        cost = o.qty_btc * fill_price
        qty = o.qty_btc
        fee = cost * fee_rate
        total = cost + fee
        if total > paper.usdt and fill_price > 0:
            qty = paper.usdt / (fill_price * (1 + fee_rate))
            cost = qty * fill_price
            fee = cost * fee_rate
            total = cost + fee
        if qty <= 0:
            return None
        paper.usdt -= total
        paper.btc += qty
        grid.cost_basis_usdt += total
        return {"event": "BUY", "fee": fee, "pnl": 0.0, "qty": qty}

    qty = min(o.qty_btc, paper.btc)
    if qty <= 0:
        return None
    gross = qty * fill_price
    fee = gross * fee_rate
    proceeds = gross - fee
    btc_before = paper.btc
    paper.btc -= qty
    paper.usdt += proceeds
    basis_sold = 0.0
    if btc_before > 0 and grid.cost_basis_usdt > 0:
        basis_sold = grid.cost_basis_usdt * (qty / btc_before)
        grid.cost_basis_usdt -= basis_sold
    realized = proceeds - basis_sold
    return {"event": "SELL", "fee": fee, "pnl": realized, "qty": qty}


state = _read_json(STATE_PATH)
df = pd.read_feather(DATA_PATH)
df["date"] = pd.to_datetime(df["date"], utc=True)

paper = PaperAccount(float(state.get("paperStartUsdt", 500.0)), float(state.get("paperStartBtc", 0.0)))
grid = None
fee_bps = float(state.get("feeBps", 10))
fee_rate = fee_bps / 10000.0
min_edge_spacing_pct = float(state.get("honestReplayMinEdgeSpacingPct", 0.015))
max_entry_trend_strength = float(state.get("honestReplayMaxEntryTrendStrength", 0.0030))
init_buy_fraction = float(state.get("honestReplayInitBuyFraction", 0.35))
reserve_fraction = float(state.get("honestReplayReserveFraction", 0.22))
max_level_usdt = float(state.get("honestReplayMaxLevelUsdt", 18.0))
max_equity = paper.usdt
min_equity = paper.usdt
realized = 0.0
fees = 0.0
wins = 0
losses = 0
trade_count = 0
equity_curve = []
peak_inventory_btc = 0.0
peak_inventory_cost_basis_usdt = 0.0

rows = df.to_dict("records")
for i in range(200, len(rows)):
    window = rows[i - 200:i + 1]
    close = [float(r["close"]) for r in window]
    high = [float(r["high"]) for r in window]
    low = [float(r["low"]) for r in window]
    price = close[-1]
    candle_hi = high[-1]
    candle_lo = low[-1]

    atr = _atr(high, low, close, 14)
    ema20 = _ema(close[-60:], 20)
    ema50 = _ema(close[-120:], 50)
    trend_strength = abs(ema20 - ema50) / price

    if grid is None or not grid.active:
        grid_mode = state.get("gridMode", "scalpy")
        spacing_pct, levels = _spacing_for_mode(
            grid_mode,
            atr,
            price,
            min_scalpy=float(state.get("gridMinSpacingPctScalpy", 0.008)),
            min_fatty=float(state.get("gridMinSpacingPctFatty", 0.01)),
        )
        if trend_strength > max_entry_trend_strength:
            eq = paper.equity(price)
            max_equity = max(max_equity, eq)
            min_equity = min(min_equity, eq)
            continue
        spacing_pct = max(spacing_pct, min_edge_spacing_pct)
        max_expo = min(float(state.get("gridMaxExposurePct", 0.35)), reserve_fraction)
        reserve_usdt = min(paper.equity(price) * max_expo, paper.usdt)
        init_buy_gross = reserve_usdt * init_buy_fraction
        init_buy_total = init_buy_gross * (1 + fee_rate)
        if init_buy_total > paper.usdt:
            init_buy_gross = paper.usdt / (1 + fee_rate)
            init_buy_total = init_buy_gross * (1 + fee_rate)
        init_qty = init_buy_gross / price if price else 0.0
        init_fee = init_buy_gross * fee_rate
        paper.usdt -= init_buy_total
        paper.btc += init_qty
        fees += init_fee
        grid = GridState(price, spacing_pct, levels, max_expo, reserve_usdt - init_buy_gross, init_qty, init_buy_total, [], True, str(window[-1]["date"]))
        base_level_usdt = (reserve_usdt * 0.5) / max(1, levels)
        min_level_usdt = min(float(state.get("gridMinPerLevelUsdt", 45.0)), max_level_usdt)
        per_level_usdt = max(min_level_usdt, min(max_level_usdt, base_level_usdt))
        qty_per = per_level_usdt / price if price else 0.0
        grid.orders = _build_grid_orders(grid.anchor, grid.spacing_pct, grid.levels, qty_per)

    trail_mult = float(state.get("gridTrailAtrMult", 2.0))
    trail_active = bool(state.get("gridTrailActive", True))
    if trail_active and grid and paper.btc > 0 and grid.cost_basis_usdt > 0:
        avg_cost = grid.cost_basis_usdt / paper.btc if paper.btc else 0.0
        candidate_stop = price - trail_mult * atr
        arm_trend = float(state.get("gridTrailArmTrendStrength", 0.008))
        arm_after_atr = float(state.get("gridTrailArmAfterAtr", 3.0))
        armed = bool(getattr(grid, "trail_armed", False))
        if (trend_strength >= arm_trend) or (avg_cost and price >= avg_cost + arm_after_atr * atr):
            setattr(grid, "trail_armed", True)
            armed = True
        if armed and avg_cost and price >= avg_cost:
            prev = float(getattr(grid, "trail_stop", 0.0) or 0.0)
            setattr(grid, "trail_stop", max(prev, candidate_stop))
        trail_stop = float(getattr(grid, "trail_stop", 0.0) or 0.0)
        if armed and trail_stop and price <= trail_stop:
            qty = paper.btc
            gross = qty * price
            fee = gross * (fee_bps / 10000.0)
            proceeds = gross - fee
            pnl = proceeds - grid.cost_basis_usdt
            min_profit_pct = float(state.get("gridTrailMinNetProfitPct", 0.001))
            force_exit_trend = float(state.get("gridTrailForceExitTrendStrength", 0.02))
            want_profit = (avg_cost > 0) and (price >= avg_cost * (1 + min_profit_pct))
            if not ((pnl < 0) and (trend_strength < force_exit_trend) and (not want_profit)):
                paper.btc = 0.0
                paper.usdt += proceeds
                grid.cost_basis_usdt = 0.0
                grid.active = False
                grid.orders = []
                realized += pnl
                fees += fee
                trade_count += 1
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1
                eq = paper.equity(price)
                max_equity = max(max_equity, eq)
                min_equity = min(min_equity, eq)
                continue

    filled = []
    fee_rate = fee_bps / 10000.0
    for o in grid.orders:
        if not (candle_lo <= o.price <= candle_hi):
            continue
        if o.side == "BUY":
            est_total = o.qty_btc * o.price * (1 + fee_rate)
            if est_total <= paper.usdt and o.price <= price:
                filled.append(o)
        else:
            if o.qty_btc > 0 and paper.btc >= o.qty_btc and o.price >= price:
                filled.append(o)

    for o in filled:
        try:
            grid.orders.remove(o)
        except ValueError:
            continue
        ev = _fill_order_paper(paper, grid, o, o.price, fee_bps)
        if ev is None:
            grid.orders.append(o)
            continue
        fees += ev["fee"]
        if ev["event"] == "SELL":
            realized += ev["pnl"]
            trade_count += 1
            if ev["pnl"] >= 0:
                wins += 1
            else:
                losses += 1
        if o.side == "BUY":
            grid.orders.append(GridOrder("SELL", o.price * (1 + grid.spacing_pct), o.qty_btc))
        else:
            grid.orders.append(GridOrder("BUY", o.price * (1 - grid.spacing_pct), o.qty_btc))

    eq = paper.equity(price)
    max_equity = max(max_equity, eq)
    min_equity = min(min_equity, eq)
    peak_inventory_btc = max(peak_inventory_btc, paper.btc)
    if grid:
        peak_inventory_cost_basis_usdt = max(peak_inventory_cost_basis_usdt, grid.cost_basis_usdt)
    row_ts = rows[i]["date"]
    if not equity_curve or row_ts.hour != equity_curve[-1]["tsUtc"][11:13] or row_ts.date().isoformat() != equity_curve[-1]["tsUtc"][:10]:
        equity_curve.append({
            "tsUtc": row_ts.isoformat(),
            "price": price,
            "equityUsdt": eq,
            "usdt": paper.usdt,
            "btc": paper.btc,
            "inventoryCostBasisUsdt": grid.cost_basis_usdt if grid else 0.0,
            "unrealizedPnlUsdt": (paper.btc * price - grid.cost_basis_usdt) if (grid and paper.btc > 0 and grid.cost_basis_usdt > 0) else 0.0,
            "activeOrders": len(grid.orders) if grid else 0,
            "spacingPct": grid.spacing_pct if grid else None,
        })

final_price = float(rows[-1]["close"])
pre_liq_equity = paper.equity(final_price)
liq_fee = 0.0
liq_pnl = 0.0
if paper.btc > 0 and grid and grid.cost_basis_usdt > 0:
    gross = paper.btc * final_price
    liq_fee = gross * (fee_bps / 10000.0)
    proceeds = gross - liq_fee
    liq_pnl = proceeds - grid.cost_basis_usdt
    paper.usdt += proceeds
    paper.btc = 0.0
    realized += liq_pnl
    fees += liq_fee
    trade_count += 1
    if liq_pnl >= 0:
        wins += 1
    else:
        losses += 1

final_equity = paper.usdt
result = {
    "replay_range": f"{rows[200]['date']}..{rows[-1]['date']}",
    "start_usdt": float(state.get("paperStartUsdt", 500.0)),
    "min_edge_spacing_pct": min_edge_spacing_pct,
    "max_entry_trend_strength": max_entry_trend_strength,
    "init_buy_fraction": init_buy_fraction,
    "reserve_fraction": reserve_fraction,
    "max_level_usdt": max_level_usdt,
    "pre_liquidation_equity": pre_liq_equity,
    "final_equity_after_liquidation": final_equity,
    "realized_pnl_including_forced_liquidation": realized,
    "forced_liquidation_pnl": liq_pnl,
    "fees_total": fees,
    "closed_trades": trade_count,
    "wins": wins,
    "losses": losses,
    "max_equity": max_equity,
    "min_equity": min_equity,
    "peak_inventory_btc": peak_inventory_btc,
    "peak_inventory_cost_basis_usdt": peak_inventory_cost_basis_usdt,
    "equity_log_points": len(equity_curve),
}
OUT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True))
EQUITY_LOG_PATH.write_text(json.dumps(equity_curve, indent=2))
print(json.dumps(result, indent=2, sort_keys=True))
