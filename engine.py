import json
import os
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from bot import BinanceSpotREST
from baserow_sync import BaserowSync


def _fire_and_forget_sync(syncer, *, state, status_payload, runtime_payload, cumulative_payload):
    def run():
        try:
            syncer.sync_tick(
                state=state,
                status_payload=status_payload,
                runtime_payload=runtime_payload,
                cumulative_payload=cumulative_payload,
            )
        except Exception as e:
            _log(f"BASEROW_SYNC_ERROR {e}")
    threading.Thread(target=run, daemon=True).start()


STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")
STATUS_PATH = os.path.join(os.path.dirname(__file__), "engine_status.json")
LOG_PATH = os.path.join(os.path.dirname(__file__), "engine.log")
TRADES_PATH = os.path.join(os.path.dirname(__file__), "trades.jsonl")
CUM_PATH = os.path.join(os.path.dirname(__file__), "cumulative.json")
RUNTIME_PATH = os.path.join(os.path.dirname(__file__), "runtime_state.json")
AI_SIGNAL_PATH = os.path.join(os.path.dirname(__file__), "ai_signal.json")

# Load env from an explicit file (preferred) or default to .env in this folder.
HERE = os.path.dirname(__file__)
_ENV_FILE = os.getenv("TRADEBOT_ENV_FILE") or os.path.join(HERE, ".env")
load_dotenv(_ENV_FILE, override=False)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _atomic_json_write(path: str, obj: dict) -> None:
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    if not os.path.exists(tmp):
        raise FileNotFoundError(f'temporary write path missing before replace: {tmp}')
    os.replace(tmp, path)


def _write_json(path: str, obj: dict) -> None:
    _atomic_json_write(path, obj)


def _log(msg: str) -> None:
    line = f"[{_utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _write_status(payload: dict) -> None:
    _atomic_json_write(STATUS_PATH, payload)


def _append_trade(event: dict) -> None:
    with open(TRADES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def _read_cum() -> dict:
    if not os.path.exists(CUM_PATH):
        return {"sinceUtc": None, "realizedPnlUsdt": 0.0, "feesPaidUsdt": 0.0, "trades": 0, "wins": 0, "losses": 0}
    with open(CUM_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_cum(c: dict) -> None:
    _atomic_json_write(CUM_PATH, c)


def _serialize_grid(grid: "GridState | None") -> dict | None:
    if grid is None:
        return None
    return {
        "anchor": grid.anchor,
        "spacing_pct": grid.spacing_pct,
        "levels": grid.levels,
        "max_exposure_pct": grid.max_exposure_pct,
        "reserved_usdt": grid.reserved_usdt,
        "reserved_btc": grid.reserved_btc,
        "cost_basis_usdt": grid.cost_basis_usdt,
        "orders": [
            {"side": o.side, "price": o.price, "qty_btc": o.qty_btc}
            for o in grid.orders
        ],
        "active": grid.active,
        "last_recenter_utc": grid.last_recenter_utc,
        "trail_armed": bool(grid.__dict__.get("trail_armed", False)),
        "trail_stop": float(grid.__dict__.get("trail_stop", 0.0) or 0.0),
    }


def _deserialize_grid(payload: dict | None) -> "GridState | None":
    if not payload:
        return None
    grid = GridState(
        anchor=float(payload.get("anchor", 0.0)),
        spacing_pct=float(payload.get("spacing_pct", 0.0)),
        levels=int(payload.get("levels", 0)),
        max_exposure_pct=float(payload.get("max_exposure_pct", 0.0)),
        reserved_usdt=float(payload.get("reserved_usdt", 0.0)),
        reserved_btc=float(payload.get("reserved_btc", 0.0)),
        cost_basis_usdt=float(payload.get("cost_basis_usdt", 0.0)),
        orders=[
            GridOrder(side=o["side"], price=float(o["price"]), qty_btc=float(o["qty_btc"]))
            for o in payload.get("orders", [])
        ],
        active=bool(payload.get("active", False)),
        last_recenter_utc=payload.get("last_recenter_utc"),
    )
    grid.__dict__["trail_armed"] = bool(payload.get("trail_armed", False))
    grid.__dict__["trail_stop"] = float(payload.get("trail_stop", 0.0) or 0.0)
    return grid


def _read_runtime_state() -> dict:
    if not os.path.exists(RUNTIME_PATH):
        return {}
    with open(RUNTIME_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_runtime_state(payload: dict) -> None:
    _atomic_json_write(RUNTIME_PATH, payload)


def _required(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _read_ai_signal() -> dict:
    if not os.path.exists(AI_SIGNAL_PATH):
        return {}
    try:
        with open(AI_SIGNAL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_ai_signal(payload: dict) -> None:
    _atomic_json_write(AI_SIGNAL_PATH, payload)


def _read_ai_decision_for_engine(state: dict) -> dict:
    if not bool(state.get("aiEnabled", False)):
        return {"enabled": False, "source": "disabled"}
    signal = _read_ai_signal()
    if not signal:
        return {"enabled": True, "stale": True, "source": "missing_signal"}
    ts = signal.get("tsUtc")
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
            max_age = max(10.0, (float(state.get("aiPollSeconds", 60.0) or 60.0) * 3.0))
            if age > max_age:
                signal = dict(signal)
                signal["stale"] = True
                signal["source"] = "expired_signal"
        except Exception:
            signal = dict(signal)
            signal["stale"] = True
            signal["source"] = "invalid_signal_ts"
    return signal


def _tg_send(token: str, chat_id: int, text: str) -> None:
    # Minimal Telegram send via HTTPS (no extra deps).
    import requests

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    r.raise_for_status()


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
    side: str  # BUY or SELL
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
    orders: list[GridOrder]
    active: bool = False
    last_recenter_utc: str | None = None


@dataclass
class Stats:
    day: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl_usdt: float = 0.0
    max_drawdown_pct: float = 0.0
    peak_equity: float = 0.0
    cooldown_until: datetime | None = None


def _day_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _spacing_for_mode(mode: str, atr: float, price: float, *, min_scalpy: float, min_fatty: float) -> tuple[float, int]:
    # Return (spacing_pct, levels)
    # NOTE: With 10bps fees, a full cycle (buy+sell) costs ~20bps, so spacing must be well above 0.20%.
    atr_pct = atr / price if price else 0.0
    if mode == "fatty":
        spacing_pct = max(min_fatty, 1.4 * atr_pct)
        levels = 8
        return spacing_pct, levels

    # scalpy default
    spacing_pct = max(min_scalpy, 0.8 * atr_pct)
    levels = 14
    return spacing_pct, levels


def _build_grid_orders(anchor: float, spacing_pct: float, levels: int, qty_per_level: float) -> list[GridOrder]:
    orders: list[GridOrder] = []
    for i in range(1, levels + 1):
        buy_px = anchor * ((1 - spacing_pct) ** i)
        sell_px = anchor * ((1 + spacing_pct) ** i)
        orders.append(GridOrder(side="BUY", price=buy_px, qty_btc=qty_per_level))
        orders.append(GridOrder(side="SELL", price=sell_px, qty_btc=qty_per_level))
    # sort buys descending (closest first), sells ascending (closest first)
    buys = sorted([o for o in orders if o.side == "BUY"], key=lambda o: o.price, reverse=True)
    sells = sorted([o for o in orders if o.side == "SELL"], key=lambda o: o.price)
    return buys + sells


def _fill_order_paper(
    paper: PaperAccount,
    grid: GridState,
    o: GridOrder,
    fill_price: float,
    fee_bps: float,
    slip_bps: float = 0.0,
) -> dict | None:
    # Returns a trade event dict (ENTER/EXIT style) and updates balances.
    fee_rate = max(0.0, fee_bps) / 10_000.0
    slip_rate = max(0.0, slip_bps) / 10_000.0

    if o.side == "BUY":
        if o.qty_btc <= 0:
            return None
        effective_price = fill_price * (1 + slip_rate)
        cost = o.qty_btc * effective_price
        if cost > paper.usdt:
            # partial fill to available USDT
            qty = paper.usdt / effective_price if effective_price else 0.0
            cost = qty * effective_price
        else:
            qty = o.qty_btc

        fee = cost * fee_rate
        total = cost + fee
        if total > paper.usdt and effective_price:
            # shrink qty so we can pay fee too
            qty = paper.usdt / (effective_price * (1 + fee_rate))
            cost = qty * effective_price
            fee = cost * fee_rate
            total = cost + fee

        paper.usdt -= total
        paper.btc += qty
        # fee increases cost basis (paid to acquire)
        grid.cost_basis_usdt += (cost + fee)
        return {
            "tsUtc": _utc_now().isoformat(),
            "event": "ENTER",
            "side": "BUY",
            "type": "PAPER_LIMIT",
            "symbol": "BTCUSDT",
            "qtyBtc": qty,
            "price": effective_price,
            "quote": "USDT",
            "notionalUsdt": cost,
            "feeUsdt": fee,
            "slippageBps": slip_bps,
            "paper": True,
        }

    # SELL
    qty = min(o.qty_btc, paper.btc)
    if qty <= 0:
        return None
    effective_price = fill_price * (1 - slip_rate)
    gross = qty * effective_price
    fee = gross * fee_rate
    proceeds = gross - fee

    btc_before = paper.btc
    paper.btc -= qty
    paper.usdt += proceeds

    # cost basis allocation (avg cost)
    basis_sold = 0.0
    if btc_before > 0 and grid.cost_basis_usdt > 0:
        basis_sold = grid.cost_basis_usdt * (qty / btc_before)
        grid.cost_basis_usdt -= basis_sold

    realized = proceeds - basis_sold
    return {
        "tsUtc": _utc_now().isoformat(),
        "event": "EXIT",
        "side": "SELL",
        "reason": "GRID_CYCLE",
        "type": "PAPER_LIMIT",
        "symbol": "BTCUSDT",
        "qtyBtc": qty,
        "price": effective_price,
        "quote": "USDT",
        "notionalUsdt": gross,
        "feeUsdt": fee,
        "slippageBps": slip_bps,
        "realizedPnlUsdt": realized,
        "paper": True,
    }


def main():
    base_url = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")
    # Use real (prod) market data by default; testnet klines can be garbage (spike wicks).
    md_url = os.getenv("BINANCE_MARKETDATA_URL", "https://api.binance.com")

    api_key = _required("BINANCE_API_KEY")
    api_secret = _required("BINANCE_API_SECRET")

    tg_token = os.getenv("TELEGRAM_CONTROL_BOT_TOKEN")

    state = _read_json(STATE_PATH)
    symbol = state.get("symbol", os.getenv("BINANCE_SYMBOL", "BTCUSDT"))
    interval = state.get("interval", "15m")

    # Trading client (testnet/prod depending on env)
    client = BinanceSpotREST(base_url=base_url, api_key=api_key, api_secret=api_secret)
    # Market-data client (prod by default)
    md = BinanceSpotREST(base_url=md_url, api_key=api_key, api_secret=api_secret)

    runtime_state = _read_runtime_state()
    baserow_sync = BaserowSync()
    paper_state = runtime_state.get("paper") or {}
    paper = PaperAccount(
        usdt=float(paper_state.get("usdt", state.get("paperStartUsdt", 10000.0))),
        btc=float(paper_state.get("btc", state.get("paperStartBtc", 0.0))),
    )

    stats_state = runtime_state.get("stats") or {}
    stats = Stats(
        day=stats_state.get("day", _day_key(_utc_now())),
        trades=int(stats_state.get("trades", 0)),
        wins=int(stats_state.get("wins", 0)),
        losses=int(stats_state.get("losses", 0)),
        pnl_usdt=float(stats_state.get("pnl_usdt", 0.0)),
        max_drawdown_pct=float(stats_state.get("max_drawdown_pct", 0.0)),
        peak_equity=float(stats_state.get("peak_equity", 0.0)),
        cooldown_until=datetime.fromisoformat(stats_state["cooldown_until"]) if stats_state.get("cooldown_until") else None,
    )

    cum = _read_cum()
    if not cum.get("sinceUtc"):
        cum["sinceUtc"] = _utc_now().isoformat()
        _write_cum(cum)

    grid: GridState | None = _deserialize_grid(runtime_state.get("grid"))

    _log(f"ENGINE_START mode={state.get('mode')} symbol={symbol} interval={interval} paper_equity_init_usdt={paper.usdt} paper_btc_init={paper.btc}")
    start_event = {"tsUtc": _utc_now().isoformat(), "event": "ENGINE_START", "mode": state.get("mode"), "symbol": symbol, "paper": True}
    _append_trade(start_event)
    baserow_sync.sync_event(state=state, event=start_event, cumulative_payload=cum)

    while True:
        state = _read_json(STATE_PATH)
        if state.get("paused"):
            time.sleep(1)
            continue

        now = _utc_now()
        if _day_key(now) != stats.day:
            stats = Stats(day=_day_key(now), peak_equity=stats.peak_equity)

        kl = md.klines(symbol=symbol, interval=interval, limit=210)
        close = [float(k[4]) for k in kl]
        high = [float(k[2]) for k in kl]
        low = [float(k[3]) for k in kl]

        price = close[-1]
        candle_hi = high[-1]
        candle_lo = low[-1]

        eq = paper.equity(price)
        if stats.peak_equity <= 0:
            stats.peak_equity = eq
        if eq > stats.peak_equity:
            stats.peak_equity = eq
        dd = (stats.peak_equity - eq) / stats.peak_equity if stats.peak_equity > 0 else 0
        stats.max_drawdown_pct = max(stats.max_drawdown_pct, dd)

        # daily stop
        daily_loss_pct = max(0.0, (stats.peak_equity - eq) / stats.peak_equity) if stats.peak_equity > 0 else 0.0
        if daily_loss_pct >= float(state.get("maxDailyLossPct", 0.10)):
            _log(f"DAILY_STOP hit daily_loss_pct={daily_loss_pct:.4f} >= {state.get('maxDailyLossPct')} -> pausing")
            state["paused"] = True
            _write_json(STATE_PATH, state)
            if tg_token and state.get("adminChatId"):
                _tg_send(tg_token, int(state["adminChatId"]), f"GRID paused: max daily loss reached ({daily_loss_pct*100:.2f}%).")
            time.sleep(1)
            continue

        # cooldown
        if stats.cooldown_until and now < stats.cooldown_until:
            time.sleep(1)
            continue

        atr = _atr(high, low, close, period=14)
        ema20 = _ema(close[-60:], period=20)
        ema50 = _ema(close[-120:], period=50)
        trend_strength = abs(ema20 - ema50) / price
        ai_signal = _read_ai_decision_for_engine(state)

        # Trailing stop (ATR-based): trail up, never down, then exit via stop loss.
        trail_mult = float(state.get("gridTrailAtrMult", 2.0))
        trail_active = bool(state.get("gridTrailActive", True))

        if trail_active and grid and paper.btc > 0 and grid.cost_basis_usdt > 0:
            avg_cost = grid.cost_basis_usdt / paper.btc if paper.btc else 0.0
            candidate_stop = price - trail_mult * atr
            market_slip_bps = float(state.get("paperMarketSlipBps", 12.0))

            # Arm trailing stop only when breakout risk is elevated OR we have a meaningful cushion.
            arm_trend = float(state.get("gridTrailArmTrendStrength", 0.004))
            arm_after_atr = float(state.get("gridTrailArmAfterAtr", 1.0))
            armed = bool(grid.__dict__.get("trail_armed", False))
            if (trend_strength >= arm_trend) or (avg_cost and price >= avg_cost + arm_after_atr * atr):
                grid.__dict__["trail_armed"] = True
                armed = True

            # Only trail once armed AND at/above cost basis; never lower the stop.
            if armed and avg_cost and price >= avg_cost:
                prev = float(grid.__dict__.get("trail_stop", 0.0) or 0.0)
                new_stop = max(prev, candidate_stop)
                grid.__dict__["trail_stop"] = new_stop

            trail_stop = float(grid.__dict__.get("trail_stop", 0.0) or 0.0)
            if armed and trail_stop and price <= trail_stop:
                fee_rate = float(state.get("feeBps", 10)) / 10_000.0
                qty = paper.btc
                effective_exit_price = price * (1 - (market_slip_bps / 10_000.0))
                gross = qty * effective_exit_price
                fee = gross * fee_rate
                proceeds = gross - fee
                realized = proceeds - grid.cost_basis_usdt

                # Guardrail: don't churn out at a net loss just because fees turned a tiny green move into red.
                # Only allow loss exits when trend strength indicates breakout risk.
                min_profit_pct = float(state.get("gridTrailMinNetProfitPct", 0.0010))  # 0.10%
                force_exit_trend = float(state.get("gridTrailForceExitTrendStrength", 0.02))
                want_profit = (avg_cost > 0) and (price >= avg_cost * (1 + min_profit_pct))
                if (realized < 0) and (trend_strength < force_exit_trend) and (not want_profit):
                    # Ignore the stop for now; let the grid work instead of paying fees repeatedly.
                    time.sleep(1)
                    continue

                paper.btc = 0.0
                paper.usdt += proceeds
                grid.cost_basis_usdt = 0.0
                grid.active = False
                grid.orders = []

                cum = _read_cum()
                cum["trades"] = int(cum.get("trades", 0)) + 1
                cum["feesPaidUsdt"] = float(cum.get("feesPaidUsdt", 0.0)) + fee
                cum["realizedPnlUsdt"] = float(cum.get("realizedPnlUsdt", 0.0)) + realized
                if realized >= 0:
                    cum["wins"] = int(cum.get("wins", 0)) + 1
                else:
                    cum["losses"] = int(cum.get("losses", 0)) + 1
                    mins = int(state.get("cooldownMinutesAfterLoss", 20))
                    stats.cooldown_until = now + timedelta(minutes=mins)
                _write_cum(cum)

                _log(f"GRID_TRAIL_STOP hit price={price:.2f} stop={trail_stop:.2f} pnl={realized:.2f}")
                exit_event = {
                    "tsUtc": _utc_now().isoformat(),
                    "event": "EXIT",
                    "side": "SELL",
                    "reason": "TRAIL_STOP",
                    "type": "PAPER_MARKET",
                    "symbol": symbol,
                    "qtyBtc": qty,
                    "price": effective_exit_price,
                    "quote": "USDT",
                    "notionalUsdt": gross,
                    "feeUsdt": fee,
                    "slippageBps": market_slip_bps,
                    "realizedPnlUsdt": realized,
                    "paper": True,
                }
                _append_trade(exit_event)
                baserow_sync.sync_event(state=state, event=exit_event, cumulative_payload=cum)

                time.sleep(1)
                continue

        # Determine mode parameters
        grid_mode = state.get("gridMode", "scalpy")
        if ai_signal.get("enabled") and not ai_signal.get("stale"):
            if ai_signal.get("recommendedMode") in {"scalpy", "fatty"}:
                grid_mode = ai_signal.get("recommendedMode")
        if grid_mode == "flexy":
            # Placeholder: if still flexy and no advisor, choose based on ATR%
            grid_mode = "scalpy" if (atr / price) < 0.01 else "fatty"

        # IMPORTANT: do NOT auto-reinitialize the grid on tiny ATR/spacing drift.
        # That was causing repeated re-buys and corrupt cost basis / unrealized PnL.
        min_scalpy = float(state.get("gridMinSpacingPctScalpy", 0.006))
        min_fatty = float(state.get("gridMinSpacingPctFatty", 0.010))
        spacing_pct, levels = _spacing_for_mode(grid_mode, atr=atr, price=price, min_scalpy=min_scalpy, min_fatty=min_fatty)
        if ai_signal.get("enabled") and not ai_signal.get("stale"):
            spacing_pct = _clamp(float(ai_signal.get("recommendedSpacingPct", spacing_pct) or spacing_pct), max(min_scalpy / 2, 0.003), 0.03)
            levels = int(_clamp(float(ai_signal.get("recommendedLevels", levels) or levels), 4, 24))
        max_expo = float(state.get("gridMaxExposurePct", 0.10))
        if ai_signal.get("enabled") and not ai_signal.get("stale"):
            max_expo = _clamp(float(ai_signal.get("recommendedMaxExposurePct", max_expo) or max_expo), 0.05, 0.60)

        if ai_signal.get("enabled") and not ai_signal.get("stale") and not ai_signal.get("gridAllowed", True):
            _write_status({
                "tsUtc": _utc_now().isoformat(),
                "mode": state.get("mode"),
                "symbol": symbol,
                "interval": interval,
                "price": price,
                "equityUsdt": paper.equity(price),
                "usdt": paper.usdt,
                "btc": paper.btc,
                "position": None if paper.btc <= 0 else {
                    "entryPrice": (grid.cost_basis_usdt / paper.btc) if (grid and paper.btc > 0) else None,
                    "qtyBtc": paper.btc,
                    "stop": float((grid.__dict__.get("trail_stop", 0.0) or 0.0)) if grid else None,
                    "tp": None,
                    "entryTimeUtc": grid.last_recenter_utc if grid else None,
                    "unrealizedPnlUsdt": 0.0,
                    "unrealizedPnlPct": 0.0,
                },
                "stats": {
                    "day": stats.day,
                    "trades": stats.trades,
                    "wins": stats.wins,
                    "losses": stats.losses,
                    "pnlUsdt": stats.pnl_usdt,
                    "maxDrawdownPct": stats.max_drawdown_pct,
                    "trendStrength": trend_strength,
                    "grid": {
                        "mode": grid_mode,
                        "spacingPct": spacing_pct,
                        "levels": levels,
                        "openOrders": len(grid.orders) if grid else 0,
                        "skipped": True,
                        "skipReason": "ai_grid_disallowed",
                    },
                    "ai": ai_signal,
                },
                "lastEvent": "AI_SKIP",
            })
            time.sleep(1)
            continue

        # initialize grid only when none/inactive
        if grid is None or (not grid.active):
            # reserve capital
            reserve_usdt = paper.equity(price) * max_expo
            reserve_usdt = min(reserve_usdt, paper.usdt)

            # Refuse to initialize a fresh grid if the spacing is too tight to overcome round-trip fees.
            # This prevents churn in low-volatility conditions where gross grid capture is mostly consumed by fees.
            fee_rate = float(state.get("feeBps", 10)) / 10_000.0
            min_edge_spacing = max(
                float(state.get("gridMinSpacingPctScalpy", 0.006)),
                (2.0 * fee_rate) + float(state.get("gridTrailMinNetProfitPct", 0.0010)),
            )
            if spacing_pct < min_edge_spacing:
                _write_status({
                    "tsUtc": _utc_now().isoformat(),
                    "mode": state.get("mode"),
                    "symbol": symbol,
                    "interval": interval,
                    "price": price,
                    "equityUsdt": paper.equity(price),
                    "usdt": paper.usdt,
                    "btc": paper.btc,
                    "position": None,
                    "stats": {
                        "day": stats.day,
                        "trades": stats.trades,
                        "wins": stats.wins,
                        "losses": stats.losses,
                        "pnlUsdt": stats.pnl_usdt,
                        "maxDrawdownPct": stats.max_drawdown_pct,
                        "trendStrength": trend_strength,
                        "grid": {
                            "mode": state.get("gridMode"),
                            "spacingPct": spacing_pct,
                            "levels": levels,
                            "openOrders": 0,
                            "skipped": True,
                            "skipReason": "spacing_below_fee_floor",
                            "requiredMinSpacingPct": min_edge_spacing,
                        },
                    },
                    "lastEvent": "GRID_SKIP",
                })
                time.sleep(1)
                continue

            # convert ~50% reserve to BTC so sells are possible
            # IMPORTANT: this is a real buy (even in paper mode) and MUST be journaled,
            # otherwise Telegram will show "sold more BTC than bought".
            fee_rate = float(state.get("feeBps", 10)) / 10_000.0
            market_slip_bps = float(state.get("paperMarketSlipBps", 12.0))
            init_effective_price = price * (1 + (market_slip_bps / 10_000.0))
            init_buy_gross = reserve_usdt * 0.5  # before fee
            init_buy_total = init_buy_gross * (1 + fee_rate)
            if init_buy_total > paper.usdt:
                init_buy_gross = paper.usdt / (1 + fee_rate)
                init_buy_total = init_buy_gross * (1 + fee_rate)

            init_qty = init_buy_gross / init_effective_price if init_effective_price else 0.0
            init_fee = init_buy_gross * fee_rate
            paper.usdt -= init_buy_total
            paper.btc += init_qty

            grid = GridState(
                anchor=price,
                spacing_pct=spacing_pct,
                levels=levels,
                max_exposure_pct=max_expo,
                reserved_usdt=reserve_usdt - init_buy_gross,
                reserved_btc=init_qty,
                cost_basis_usdt=init_buy_gross + init_fee,
                orders=[],
                active=True,
                last_recenter_utc=_utc_now().isoformat(),
            )

            if init_qty > 0:
                enter_event = {
                    "tsUtc": _utc_now().isoformat(),
                    "event": "ENTER",
                    "side": "BUY",
                    "reason": "GRID_INIT",
                    "type": "PAPER_MARKET",
                    "symbol": symbol,
                    "qtyBtc": init_qty,
                    "price": init_effective_price,
                    "quote": "USDT",
                    "notionalUsdt": init_buy_gross,
                    "feeUsdt": init_fee,
                    "slippageBps": market_slip_bps,
                    "paper": True,
                }
                _append_trade(enter_event)
                baserow_sync.sync_event(state=state, event=enter_event, cumulative_payload=cum)
                cum = _read_cum()
                cum["trades"] = int(cum.get("trades", 0)) + 1
                cum["feesPaidUsdt"] = float(cum.get("feesPaidUsdt", 0.0)) + init_fee
                _write_cum(cum)
                stats.trades += 1

            # qty per level: spread remaining reserve across levels
            total_levels = max(1, levels)
            min_per_level = float(state.get("gridMinPerLevelUsdt", 20.0))
            per_level_usdt = max(min_per_level, (reserve_usdt * 0.5) / total_levels)
            qty_per = per_level_usdt / price if price else 0.0
            grid.orders = _build_grid_orders(anchor=grid.anchor, spacing_pct=grid.spacing_pct, levels=grid.levels, qty_per_level=qty_per)

            _log(f"GRID_INIT mode={grid_mode} spacing={spacing_pct:.4f} levels={levels} maxExpo={max_expo:.2f} anchor={price:.2f}")
            grid_init_event = {
                "tsUtc": _utc_now().isoformat(),
                "event": "GRID_INIT",
                "mode": grid_mode,
                "spacingPct": spacing_pct,
                "levels": levels,
                "maxExposurePct": max_expo,
                "anchor": price,
                "paper": True,
            }
            _append_trade(grid_init_event)
            baserow_sync.sync_event(state=state, event=grid_init_event, cumulative_payload=cum)

        # Fill logic: if candle crosses order price.
        fee_rate = float(state.get("feeBps", 10)) / 10_000.0
        limit_slip_bps = float(state.get("paperLimitSlipBps", 3.0))

        filled: list[GridOrder] = []
        for o in grid.orders:
            # A limit order only fills if the candle traded through that price.
            if not (candle_lo <= o.price <= candle_hi):
                continue

            if o.side == "BUY":
                # Must have enough USDT to buy AND pay the fee.
                est_total = o.qty_btc * o.price * (1 + fee_rate)
                if est_total <= paper.usdt and o.price <= price:
                    filled.append(o)

            elif o.side == "SELL":
                # Must actually have BTC to sell; otherwise we'd log fake 0-qty "fills".
                if o.qty_btc > 0 and paper.btc >= o.qty_btc and o.price >= price:
                    filled.append(o)

        for o in filled:
            if stats.trades >= int(state.get("maxTradesPerDay", 200)):
                break

            # remove order
            try:
                grid.orders.remove(o)
            except ValueError:
                continue

            # fill at order price
            fee_bps = float(state.get("feeBps", 10))
            ev = _fill_order_paper(paper, grid, o, fill_price=o.price, fee_bps=fee_bps, slip_bps=limit_slip_bps)
            if ev is None:
                # Could not fill (insufficient balance / zero qty). Keep order on the book.
                grid.orders.append(o)
                continue
            ev["symbol"] = symbol
            _append_trade(ev)
            baserow_sync.sync_event(state=state, event=ev, cumulative_payload=cum)

            cum = _read_cum()
            cum["trades"] = int(cum.get("trades", 0)) + 1
            cum["feesPaidUsdt"] = float(cum.get("feesPaidUsdt", 0.0)) + float(ev.get("feeUsdt") or 0.0)
            if ev.get("event") == "EXIT":
                pnl = float(ev.get("realizedPnlUsdt") or 0.0)
                cum["realizedPnlUsdt"] = float(cum.get("realizedPnlUsdt", 0.0)) + pnl
                stats.pnl_usdt += pnl
                if pnl >= 0:
                    cum["wins"] = int(cum.get("wins", 0)) + 1
                else:
                    cum["losses"] = int(cum.get("losses", 0)) + 1
            _write_cum(cum)

            stats.trades += 1

            # place opposite order one level away
            if o.side == "BUY":
                new_px = o.price * (1 + grid.spacing_pct)
                grid.orders.append(GridOrder(side="SELL", price=new_px, qty_btc=o.qty_btc))
            else:
                new_px = o.price * (1 - grid.spacing_pct)
                grid.orders.append(GridOrder(side="BUY", price=new_px, qty_btc=o.qty_btc))

        # Update status every tick
        unreal = 0.0
        unreal_pct = 0.0
        if paper.btc > 0 and grid and grid.cost_basis_usdt > 0:
            mkt_value = paper.btc * price
            unreal = mkt_value - grid.cost_basis_usdt
            avg_cost = grid.cost_basis_usdt / paper.btc if paper.btc else 0.0
            unreal_pct = (price / avg_cost - 1.0) if avg_cost else 0.0

        _write_status({
            "tsUtc": _utc_now().isoformat(),
            "mode": state.get("mode"),
            "symbol": symbol,
            "interval": interval,
            "price": price,
            "equityUsdt": paper.equity(price),
            "usdt": paper.usdt,
            "btc": paper.btc,
            "position": None if paper.btc <= 0 else {
                "entryPrice": (grid.cost_basis_usdt / paper.btc) if (grid and paper.btc > 0) else None,
                "qtyBtc": paper.btc,
                "stop": float((grid.__dict__.get("trail_stop", 0.0) or 0.0)) if grid else None,
                "tp": None,
                "entryTimeUtc": grid.last_recenter_utc if grid else None,
                "unrealizedPnlUsdt": unreal,
                "unrealizedPnlPct": unreal_pct,
            },
            "stats": {
                "day": stats.day,
                "trades": stats.trades,
                "wins": stats.wins,
                "losses": stats.losses,
                "pnlUsdt": stats.pnl_usdt,
                "maxDrawdownPct": stats.max_drawdown_pct,
                "trendStrength": trend_strength,
                "grid": {
                    "mode": state.get("gridMode"),
                    "spacingPct": grid.spacing_pct if grid else None,
                    "levels": grid.levels if grid else None,
                    "openOrders": len(grid.orders) if grid else 0,
                },
                "ai": ai_signal,
            },
            "lastEvent": "TICK",
        })

        runtime_payload = {
            "paper": {
                "usdt": paper.usdt,
                "btc": paper.btc,
            },
            "stats": {
                "day": stats.day,
                "trades": stats.trades,
                "wins": stats.wins,
                "losses": stats.losses,
                "pnl_usdt": stats.pnl_usdt,
                "max_drawdown_pct": stats.max_drawdown_pct,
                "peak_equity": stats.peak_equity,
                "cooldown_until": stats.cooldown_until.isoformat() if stats.cooldown_until else None,
            },
            "market": {
                "price": price,
                "candle": {
                    "open": close[-2] if len(close) >= 2 else price,
                    "high": candle_hi,
                    "low": candle_lo,
                    "close": price,
                    "volumeBase": float(kl[-1][5]) if kl and len(kl[-1]) > 5 else 0.0,
                    "volumeUsdt": float(kl[-1][7]) if kl and len(kl[-1]) > 7 else 0.0,
                    "openTimeMs": int(kl[-1][0]) if kl and len(kl[-1]) > 0 else None,
                    "closeTimeMs": int(kl[-1][6]) if kl and len(kl[-1]) > 6 else None,
                },
            },
            "grid": _serialize_grid(grid),
            "ai": ai_signal,
            "savedAt": _utc_now().isoformat(),
        }
        _write_runtime_state(runtime_payload)
        _log(f"RUNTIME_STATE_WRITE savedAt={runtime_payload['savedAt']} ai_model={((ai_signal or {}).get('model'))} has_ai={('ai' in runtime_payload)}")
        _fire_and_forget_sync(
            baserow_sync,
            state=state,
            status_payload=_read_json(STATUS_PATH),
            runtime_payload=runtime_payload,
            cumulative_payload=_read_cum(),
        )

        _log(f"HEARTBEAT price={price:.2f} equity={paper.equity(price):.4f} orders={len(grid.orders) if grid else 0}")
        time.sleep(1)


if __name__ == "__main__":
    main()
