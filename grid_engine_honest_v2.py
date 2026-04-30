import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DATA_PATH = Path("/home/claw/AgileSquad/runtime/freqtrade/user_data/data/binance/BTC_USDT-1m.feather")
STATE_PATH = Path(__file__).resolve().parent / "state.json"
OUT_PATH = Path(__file__).resolve().parent / "grid_honest_replay_v2.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _ema(values: list[float], period: int) -> float:
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def _atr(high: list[float], low: list[float], close: list[float], period: int = 14) -> float:
    trs = []
    for i in range(1, len(close)):
        tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
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
    spacing_pct: float
    levels: int
    cost_basis_usdt: float
    orders: list
    active: bool = False


def _build_grid_orders(anchor: float, spacing_pct: float, levels: int, qty_per_level: float) -> list[GridOrder]:
    orders = []
    for i in range(1, levels + 1):
        orders.append(GridOrder("BUY", anchor * ((1 - spacing_pct) ** i), qty_per_level))
        orders.append(GridOrder("SELL", anchor * ((1 + spacing_pct) ** i), qty_per_level))
    buys = sorted([o for o in orders if o.side == "BUY"], key=lambda o: o.price, reverse=True)
    sells = sorted([o for o in orders if o.side == "SELL"], key=lambda o: o.price)
    return buys + sells


def _fill(paper: PaperAccount, grid: GridState, o: GridOrder, fee_bps: float):
    fee_rate = fee_bps / 10000.0
    if o.side == "BUY":
        cost = o.qty_btc * o.price
        total = cost * (1 + fee_rate)
        if total > paper.usdt:
            return None
        paper.usdt -= total
        paper.btc += o.qty_btc
        grid.cost_basis_usdt += total
        return {"event": "BUY", "fee": cost * fee_rate, "pnl": 0.0}
    qty = min(o.qty_btc, paper.btc)
    if qty <= 0:
        return None
    gross = qty * o.price
    fee = gross * fee_rate
    proceeds = gross - fee
    btc_before = paper.btc
    paper.btc -= qty
    paper.usdt += proceeds
    basis_sold = grid.cost_basis_usdt * (qty / btc_before) if btc_before > 0 and grid.cost_basis_usdt > 0 else 0.0
    grid.cost_basis_usdt -= basis_sold
    return {"event": "SELL", "fee": fee, "pnl": proceeds - basis_sold}


state = _read_json(STATE_PATH)
df = pd.read_feather(DATA_PATH)
df["date"] = pd.to_datetime(df["date"], utc=True)
rows = df.to_dict("records")

paper = PaperAccount(float(state.get("paperStartUsdt", 500.0)), float(state.get("paperStartBtc", 0.0)))
fee_bps = float(state.get("feeBps", 10))
fee_rate = fee_bps / 10000.0
required_spacing = max(0.012, (2.0 * fee_rate) + 0.004)
levels = 6
max_expo = 0.20
realized = 0.0
fees = 0.0
wins = 0
losses = 0
closed_trades = 0
skipped_ticks = 0
grid = None

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
    atr_pct = atr / price if price else 0.0

    if grid is None or not grid.active:
        if trend_strength > 0.0035 or atr_pct < required_spacing:
            skipped_ticks += 1
            continue
        reserve_usdt = min(paper.equity(price) * max_expo, paper.usdt)
        if reserve_usdt < 100:
            skipped_ticks += 1
            continue
        init_buy_gross = reserve_usdt * 0.35
        init_buy_total = init_buy_gross * (1 + fee_rate)
        if init_buy_total > paper.usdt:
            skipped_ticks += 1
            continue
        init_qty = init_buy_gross / price
        paper.usdt -= init_buy_total
        paper.btc += init_qty
        fees += init_buy_gross * fee_rate
        spacing_pct = max(required_spacing, atr_pct * 1.2)
        grid = GridState(spacing_pct=spacing_pct, levels=levels, cost_basis_usdt=init_buy_total, orders=[], active=True)
        per_level_usdt = (reserve_usdt * 0.65) / levels
        qty_per = per_level_usdt / price
        grid.orders = _build_grid_orders(price, spacing_pct, levels, qty_per)

    filled = []
    for o in grid.orders:
        if not (candle_lo <= o.price <= candle_hi):
            continue
        if o.side == "BUY" and o.qty_btc * o.price * (1 + fee_rate) <= paper.usdt and o.price <= price:
            filled.append(o)
        if o.side == "SELL" and paper.btc >= o.qty_btc and o.price >= price:
            filled.append(o)

    for o in filled:
        try:
            grid.orders.remove(o)
        except ValueError:
            continue
        ev = _fill(paper, grid, o, fee_bps)
        if ev is None:
            grid.orders.append(o)
            continue
        fees += ev["fee"]
        if ev["event"] == "SELL":
            realized += ev["pnl"]
            closed_trades += 1
            if ev["pnl"] >= 0:
                wins += 1
            else:
                losses += 1
        if o.side == "BUY":
            grid.orders.append(GridOrder("SELL", o.price * (1 + grid.spacing_pct), o.qty_btc))
        else:
            grid.orders.append(GridOrder("BUY", o.price * (1 - grid.spacing_pct), o.qty_btc))

    if grid and paper.btc > 0 and grid.cost_basis_usdt > 0:
        avg_cost = grid.cost_basis_usdt / paper.btc
        if trend_strength > 0.006 or price < avg_cost - (1.8 * atr):
            gross = paper.btc * price
            fee = gross * fee_rate
            proceeds = gross - fee
            pnl = proceeds - grid.cost_basis_usdt
            paper.usdt += proceeds
            paper.btc = 0.0
            grid.cost_basis_usdt = 0.0
            grid.orders = []
            grid.active = False
            realized += pnl
            fees += fee
            closed_trades += 1
            if pnl >= 0:
                wins += 1
            else:
                losses += 1

final_price = float(rows[-1]["close"])
pre_liq_equity = paper.equity(final_price)
forced_liq_pnl = 0.0
if paper.btc > 0 and grid and grid.cost_basis_usdt > 0:
    gross = paper.btc * final_price
    fee = gross * fee_rate
    proceeds = gross - fee
    forced_liq_pnl = proceeds - grid.cost_basis_usdt
    paper.usdt += proceeds
    paper.btc = 0.0
    realized += forced_liq_pnl
    fees += fee
    closed_trades += 1
    if forced_liq_pnl >= 0:
        wins += 1
    else:
        losses += 1

result = {
    "required_spacing_pct": required_spacing,
    "max_exposure_pct": max_expo,
    "levels": levels,
    "skipped_ticks": skipped_ticks,
    "start_usdt": float(state.get("paperStartUsdt", 500.0)),
    "pre_liquidation_equity": pre_liq_equity,
    "final_equity_after_liquidation": paper.usdt,
    "realized_pnl_including_forced_liquidation": realized,
    "forced_liquidation_pnl": forced_liq_pnl,
    "fees_total": fees,
    "closed_trades": closed_trades,
    "wins": wins,
    "losses": losses,
}
OUT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True))
print(json.dumps(result, indent=2, sort_keys=True))
