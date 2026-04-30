import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from bot import BinanceSpotREST


STATE_PATH = os.path.join(os.path.dirname(__file__), "state_trend.json")
STATUS_PATH = os.path.join(os.path.dirname(__file__), "engine_status_trend.json")
LOG_PATH = os.path.join(os.path.dirname(__file__), "engine_trend.log")
TRADES_PATH = os.path.join(os.path.dirname(__file__), "trades_trend.jsonl")
CUM_PATH = os.path.join(os.path.dirname(__file__), "cumulative_trend.json")

# Load env from an explicit file (preferred) or default to .env in this folder.
HERE = os.path.dirname(__file__)
_ENV_FILE = os.getenv("TRADEBOT_ENV_FILE") or os.path.join(HERE, ".env")
load_dotenv(_ENV_FILE, override=False)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _log(msg: str) -> None:
    line = f"[{_utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _write_status(payload: dict) -> None:
    tmp = STATUS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    os.replace(tmp, STATUS_PATH)


def _append_trade(event: dict) -> None:
    with open(TRADES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def _read_cum() -> dict:
    if not os.path.exists(CUM_PATH):
        return {"sinceUtc": None, "realizedPnlUsdt": 0.0, "feesPaidUsdt": 0.0, "trades": 0, "wins": 0, "losses": 0}
    with open(CUM_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_cum(c: dict) -> None:
    tmp = CUM_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2, sort_keys=True)
    os.replace(tmp, CUM_PATH)


def _required(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


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
    # simple moving average of TR
    window = trs[-period:]
    return sum(window) / len(window)


@dataclass
class PaperAccount:
    usdt: float
    btc: float

    def equity(self, price: float) -> float:
        return self.usdt + self.btc * price


@dataclass
class Position:
    entry_price: float
    qty_btc: float
    stop: float
    tp: float
    entry_time: datetime
    entry_fee_usdt: float = 0.0
    initial_stop: float | None = None


@dataclass
class Stats:
    day: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl_usdt: float = 0.0
    max_drawdown_pct: float = 0.0
    peak_equity: float = 0.0
    last_trade_was_loss: bool = False
    cooldown_until: datetime | None = None


def _day_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _interval_to_seconds(interval: str) -> int:
    if interval.endswith("m"):
        return int(interval[:-1]) * 60
    if interval.endswith("h"):
        return int(interval[:-1]) * 3600
    raise ValueError(f"Unsupported interval: {interval}")


def main():
    # Env is loaded at import-time from TRADEBOT_ENV_FILE (defaults to .env)

    base_url = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")
    md_url = os.getenv("BINANCE_MARKETDATA_URL", "https://api.binance.com")
    api_key = _required("BINANCE_API_KEY")
    api_secret = _required("BINANCE_API_SECRET")

    tg_token = os.getenv("TELEGRAM_CONTROL_BOT_TOKEN")

    # bootstrap state
    state = _read_json(STATE_PATH)

    symbol = state.get("symbol", os.getenv("BINANCE_SYMBOL", "BTCUSDT"))
    interval = state.get("interval", "15m")

    client = BinanceSpotREST(base_url=base_url, api_key=api_key, api_secret=api_secret)
    md = BinanceSpotREST(base_url=md_url, api_key=api_key, api_secret=api_secret)

    # paper account bootstrap:
    # - if we have persisted runtime balances/position in state, resume from them
    # - otherwise start from paperStart* for reproducible tests
    usdt = float(state.get("paperUsdt", state.get("paperStartUsdt", 10000.0)))
    btc = float(state.get("paperBtc", state.get("paperStartBtc", 0.0)))
    paper = PaperAccount(usdt=usdt, btc=btc)

    pos: Position | None = None
    p = state.get("position")
    if isinstance(p, dict) and p.get("qtyBtc") and p.get("entryPrice"):
        try:
            pos = Position(
                entry_price=float(p.get("entryPrice")),
                qty_btc=float(p.get("qtyBtc")),
                stop=float(p.get("stop")),
                tp=float(p.get("tp")),
                entry_time=datetime.fromisoformat(str(p.get("entryTimeUtc")).replace("Z", "+00:00")).astimezone(timezone.utc),
                entry_fee_usdt=float(p.get("entryFeeUsdt", 0.0) or 0.0),
                initial_stop=float(p.get("initialStop", p.get("stop"))),
            )
            _log(f"RESUME position qty={pos.qty_btc:.6f} entry={pos.entry_price:.2f} stop={pos.stop:.2f} tp={pos.tp:.2f}")
        except Exception:
            pos = None
    stats = Stats(day=_day_key(_utc_now()))
    stats.peak_equity = 0.0

    last_summary_at = _utc_now()
    last_hourly_at = _utc_now()

    cum = _read_cum()
    if not cum.get("sinceUtc"):
        cum["sinceUtc"] = _utc_now().isoformat()
        _write_cum(cum)

    _log(f"ENGINE_START mode={state.get('mode')} symbol={symbol} interval={interval} paper_equity_init_usdt={paper.usdt} paper_btc_init={paper.btc}")

    _append_trade({
        "tsUtc": _utc_now().isoformat(),
        "event": "ENGINE_START",
        "mode": state.get("mode"),
        "symbol": symbol,
        "paper": True
    })

    _write_status({
        "tsUtc": _utc_now().isoformat(),
        "mode": state.get("mode"),
        "symbol": symbol,
        "interval": interval,
        "price": None,
        "equityUsdt": None,
        "usdt": paper.usdt,
        "btc": paper.btc,
        "position": None,
        "stats": {"day": stats.day, "trades": stats.trades, "wins": stats.wins, "losses": stats.losses, "pnlUsdt": stats.pnl_usdt},
        "lastEvent": "ENGINE_START"
    })

    while True:
        state = _read_json(STATE_PATH)
        if state.get("paused"):
            time.sleep(2)
            continue

        # reset daily stats
        now = _utc_now()
        if _day_key(now) != stats.day:
            stats = Stats(day=_day_key(now), peak_equity=stats.peak_equity)

        # fetch candles
        kl15 = md.klines(symbol=symbol, interval=interval, limit=210)
        # Sleep until the next candle close to avoid spamming the API / reacting mid-candle.
        try:
            interval_s = _interval_to_seconds(interval)
            last_open_ms = int(kl15[-1][0])
            next_close_s = (last_open_ms // 1000) + interval_s
            now_s = int(_utc_now().timestamp())
            sleep_s = max(2, min(60, next_close_s - now_s))  # cap to stay responsive to /pause
        except Exception:
            sleep_s = 5
        close15 = [float(k[4]) for k in kl15]
        high15 = [float(k[2]) for k in kl15]
        low15 = [float(k[3]) for k in kl15]
        price = close15[-1]

        # equity tracking & drawdown
        eq = paper.equity(price)
        if stats.peak_equity <= 0:
            stats.peak_equity = eq
        if eq > stats.peak_equity:
            stats.peak_equity = eq
        dd = (stats.peak_equity - eq) / stats.peak_equity if stats.peak_equity > 0 else 0
        stats.max_drawdown_pct = max(stats.max_drawdown_pct, dd)

        # daily loss stop
        if stats.peak_equity > 0:
            daily_loss_pct = max(0.0, (stats.peak_equity - eq) / stats.peak_equity)
            if daily_loss_pct >= float(state.get("maxDailyLossPct", 0.015)):
                _log(f"DAILY_STOP hit daily_loss_pct={daily_loss_pct:.4f} >= {state.get('maxDailyLossPct')} -> pausing")
                state["paused"] = True
                _write_json(STATE_PATH, state)
                if tg_token and state.get("adminChatId"):
                    _tg_send(tg_token, int(state["adminChatId"]), f"TradeBot paused: max daily loss reached ({daily_loss_pct*100:.2f}%).")
                continue

        # cooldown after loss
        if stats.cooldown_until and now < stats.cooldown_until:
            time.sleep(2)
            continue

        # TREND strategy (paper): only enter when uptrend is confirmed
        if pos is None:
            if stats.trades >= int(state.get("maxTradesPerDay", 6)):
                time.sleep(5)
                continue

            atr = _atr(high15, low15, close15, period=14)
            ema20 = _ema(close15[-60:], period=20)
            ema50 = _ema(close15[-120:], period=50)
            ema200 = _ema(close15[-210:], period=200)
            trend_strength = abs(ema20 - ema50) / price

            # Entry filters: the old version was prone to chop + TIME exits.
            # We add a breakout + volatility gate so entries happen when there's a reason to expect follow-through.
            min_trend = float(state.get("trendMinTrendStrength", 0.0020))
            use_ema200 = bool(state.get("trendUseEma200", False))
            ema200_buf = float(state.get("trendEma200BufferPct", 0.0))

            lookback = int(state.get("trendBreakoutLookback", 20))
            lb = max(5, min(60, lookback))
            prior_high = max(high15[-(lb + 1):-1])
            breakout = price >= prior_high

            min_atr_pct = float(state.get("trendMinAtrPct", 0.0015))  # 0.15%
            atr_pct = atr / price if price else 0.0

            uptrend = (ema20 > ema50) and (trend_strength >= min_trend) and (price > ema50) and breakout and (atr_pct >= min_atr_pct)
            if use_ema200:
                uptrend = uptrend and (price > ema200 * (1 - ema200_buf))
            if not uptrend:
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
                    "stats": {"day": stats.day, "trades": stats.trades, "wins": stats.wins, "losses": stats.losses, "pnlUsdt": stats.pnl_usdt, "maxDrawdownPct": stats.max_drawdown_pct},
                    "lastEvent": f"NO_TREND ema20={ema20:.2f} ema50={ema50:.2f} ema200={ema200:.2f} strength={trend_strength:.4f} breakout={breakout} atrPct={atr_pct:.4f}"
                })

                # persist runtime balances even when no trade happens
                try:
                    state["paperUsdt"] = paper.usdt
                    state["paperBtc"] = paper.btc
                    state["position"] = None
                    _write_json(STATE_PATH, state)
                except Exception:
                    pass

                time.sleep(sleep_s)
                continue

            cap = float(state.get("positionCapPct", 0.30))
            target_notional = paper.equity(price) * cap
            if target_notional < 10:  # avoid dust
                time.sleep(sleep_s)
                continue

            qty = target_notional / price
            # enter at current price (paper)
            stop_mult = float(state.get("trendAtrStopMult", 2.0))
            stop = price - stop_mult * atr
            # Take-profit at a multiple of initial risk (R).
            r = max(0.0, price - stop)
            tp_r = float(state.get("trendTpR", 2.0))
            tp = price + tp_r * r

            # update paper balances (Binance standard fee assumed: feeBps, default 10 = 0.10%)
            fee_rate = float(state.get("feeBps", 10)) / 10_000.0
            cost = qty * price
            fee = cost * fee_rate
            total = cost + fee
            if total > paper.usdt and price:
                qty = paper.usdt / (price * (1 + fee_rate))
                cost = qty * price
                fee = cost * fee_rate
                total = cost + fee
            paper.usdt -= total
            paper.btc += qty

            pos = Position(entry_price=price, qty_btc=qty, stop=stop, tp=tp, entry_time=now, entry_fee_usdt=fee, initial_stop=stop)
            stats.trades += 1

            # persist runtime balances + position
            state["paperUsdt"] = paper.usdt
            state["paperBtc"] = paper.btc
            state["position"] = {
                "entryPrice": pos.entry_price,
                "qtyBtc": pos.qty_btc,
                "stop": pos.stop,
                "tp": pos.tp,
                "initialStop": (pos.initial_stop if pos.initial_stop is not None else pos.stop),
                "entryTimeUtc": pos.entry_time.isoformat(),
                "entryFeeUsdt": pos.entry_fee_usdt,
            }
            _write_json(STATE_PATH, state)

            cum = _read_cum()
            cum["trades"] = int(cum.get("trades", 0)) + 1
            cum["feesPaidUsdt"] = float(cum.get("feesPaidUsdt", 0.0)) + fee
            _write_cum(cum)

            _log(f"ENTER paper long qty={qty:.6f} entry={price:.2f} stop={stop:.2f} tp={tp:.2f} eq={eq:.2f}")
            _append_trade({
                "tsUtc": _utc_now().isoformat(),
                "event": "ENTER",
                "side": "BUY",
                "type": "PAPER_MARKET",
                "symbol": symbol,
                "qtyBtc": qty,
                "price": price,
                "quote": "USDT",
                "notionalUsdt": cost,
                "feeUsdt": fee,
                "paper": True
            })

            _write_status({
                "tsUtc": _utc_now().isoformat(),
                "mode": state.get("mode"),
                "symbol": symbol,
                "interval": interval,
                "price": price,
                "equityUsdt": paper.equity(price),
                "usdt": paper.usdt,
                "btc": paper.btc,
                "position": {
                    "entryPrice": pos.entry_price,
                    "qtyBtc": pos.qty_btc,
                    "stop": pos.stop,
                    "tp": pos.tp,
                    "entryTimeUtc": pos.entry_time.isoformat(),
                    "unrealizedPnlUsdt": (price - pos.entry_price) * pos.qty_btc,
                    "unrealizedPnlPct": (price / pos.entry_price - 1.0),
                },
                "stats": {"day": stats.day, "trades": stats.trades, "wins": stats.wins, "losses": stats.losses, "pnlUsdt": stats.pnl_usdt, "maxDrawdownPct": stats.max_drawdown_pct},
                "lastEvent": "ENTER"
            })

        else:
            # manage exit
            # Time stop: the old 90m was basically forcing fee-churn in chop.
            # Default to 6 hours; configurable.
            tstop_min = int(state.get("trendTimeStopMinutes", 360))
            time_stop = pos.entry_time + timedelta(minutes=tstop_min)

            # Improve exits: breakeven + ATR trailing after trade moves in our favor.
            fee_rate = float(state.get("feeBps", 10)) / 10_000.0
            try:
                atr = _atr(high15, low15, close15, period=14)
            except Exception:
                atr = 0.0

            initial_stop = pos.initial_stop if pos.initial_stop is not None else pos.stop
            r0 = max(0.0, pos.entry_price - initial_stop)

            be_r = float(state.get("trendBreakevenAfterR", 1.0))
            trail_r = float(state.get("trendTrailStartR", 1.0))
            trail_atr_mult = float(state.get("trendTrailAtrMult", 2.5))
            use_tp = bool(state.get("trendUseTp", True))

            # move stop to breakeven (fees-aware) once price reaches +be_r * R
            if r0 > 0 and price >= pos.entry_price + be_r * r0:
                be_price = pos.entry_price * (1 + 2 * fee_rate)  # rough fee buffer (entry+exit)
                if be_price > pos.stop:
                    pos.stop = be_price

            # ATR trail after +trail_r * R
            if r0 > 0 and atr > 0 and price >= pos.entry_price + trail_r * r0:
                trail_stop = price - trail_atr_mult * atr
                if trail_stop > pos.stop:
                    pos.stop = trail_stop

            # persist any stop updates to state so restarts don't lose them
            try:
                state["position"]["stop"] = pos.stop
                state["position"]["initialStop"] = initial_stop
                _write_json(STATE_PATH, state)
            except Exception:
                pass

            exit_reason = None
            if price <= pos.stop:
                exit_reason = "STOP"
            elif use_tp and price >= pos.tp:
                exit_reason = "TP"
            elif now >= time_stop and price < pos.entry_price:
                exit_reason = "TIME"
            else:
                # Trend exit: if trend flips, get out.
                try:
                    ema20 = _ema(close15[-60:], period=20)
                    ema50 = _ema(close15[-120:], period=50)
                    if ema20 < ema50:
                        exit_reason = "TREND_FLIP"
                except Exception:
                    pass

            if exit_reason:
                # exit at current price (paper) with fee
                gross = pos.qty_btc * price
                fee = gross * fee_rate
                proceeds = gross - fee
                paper.btc -= pos.qty_btc
                paper.usdt += proceeds

                # pnl is net of fees (includes entry + exit fees)
                pnl = proceeds - (pos.qty_btc * pos.entry_price) - float(getattr(pos, "entry_fee_usdt", 0.0) or 0.0)
                stats.pnl_usdt += pnl
                cum = _read_cum()
                cum["feesPaidUsdt"] = float(cum.get("feesPaidUsdt", 0.0)) + fee
                cum["realizedPnlUsdt"] = float(cum.get("realizedPnlUsdt", 0.0)) + pnl

                if pnl >= 0:
                    stats.wins += 1
                    stats.last_trade_was_loss = False
                    cum["wins"] = int(cum.get("wins", 0)) + 1
                else:
                    stats.losses += 1
                    stats.last_trade_was_loss = True
                    cum["losses"] = int(cum.get("losses", 0)) + 1
                    mins = int(state.get("cooldownMinutesAfterLoss", 20))
                    stats.cooldown_until = now + timedelta(minutes=mins)

                _write_cum(cum)

                _log(f"EXIT {exit_reason} qty={pos.qty_btc:.6f} exit={price:.2f} entry={pos.entry_price:.2f} pnl={pnl:.2f} eq={paper.equity(price):.2f}")
                _append_trade({
                    "tsUtc": _utc_now().isoformat(),
                    "event": "EXIT",
                    "side": "SELL",
                    "reason": exit_reason,
                    "type": "PAPER_MARKET",
                    "symbol": symbol,
                    "qtyBtc": pos.qty_btc,
                    "price": price,
                    "quote": "USDT",
                    "notionalUsdt": gross,
                    "feeUsdt": fee,
                    "realizedPnlUsdt": pnl,
                    "paper": True
                })

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
                    "stats": {"day": stats.day, "trades": stats.trades, "wins": stats.wins, "losses": stats.losses, "pnlUsdt": stats.pnl_usdt, "maxDrawdownPct": stats.max_drawdown_pct},
                    "lastEvent": f"EXIT_{exit_reason}"
                })

                # persist runtime balances + clear position
                state["paperUsdt"] = paper.usdt
                state["paperBtc"] = paper.btc
                state["position"] = None
                _write_json(STATE_PATH, state)

                pos = None

        # always refresh live status snapshot
        _write_status({
            "tsUtc": _utc_now().isoformat(),
            "mode": state.get("mode"),
            "symbol": symbol,
            "interval": interval,
            "price": price,
            "equityUsdt": paper.equity(price),
            "usdt": paper.usdt,
            "btc": paper.btc,
            "position": None if pos is None else {
                "entryPrice": pos.entry_price,
                "qtyBtc": pos.qty_btc,
                "stop": pos.stop,
                "tp": pos.tp,
                "entryTimeUtc": pos.entry_time.isoformat(),
                "unrealizedPnlUsdt": (price - pos.entry_price) * pos.qty_btc,
                "unrealizedPnlPct": (price / pos.entry_price - 1.0),
            },
            "stats": {"day": stats.day, "trades": stats.trades, "wins": stats.wins, "losses": stats.losses, "pnlUsdt": stats.pnl_usdt, "maxDrawdownPct": stats.max_drawdown_pct},
            "lastEvent": "TICK"
        })

        def _compute_trade_totals() -> dict:
            out = {"buys": 0, "buyUsdt": 0.0, "sells": 0, "sellUsdt": 0.0}
            try:
                if not os.path.exists(TRADES_PATH):
                    return out
                with open(TRADES_PATH, "r", encoding="utf-8") as f:
                    for ln in f.read().splitlines():
                        if not ln.strip():
                            continue
                        try:
                            e = json.loads(ln)
                        except Exception:
                            continue
                        if e.get("event") not in ("ENTER", "EXIT"):
                            continue
                        side = e.get("side")
                        notional = float(e.get("notionalUsdt", 0.0))
                        if side == "BUY":
                            out["buys"] += 1
                            out["buyUsdt"] += notional
                        elif side == "SELL":
                            out["sells"] += 1
                            out["sellUsdt"] += notional
            except Exception:
                pass
            return out

        # Hourly updates
        if state.get("hourlySummary") and state.get("adminChatId") and tg_token:
            if _utc_now() - last_hourly_at >= timedelta(hours=1):
                cum = _read_cum()
                totals = _compute_trade_totals()
                realized = float(cum.get("realizedPnlUsdt", 0.0))
                unreal = 0.0 if pos is None else (price - pos.entry_price) * pos.qty_btc

                recs = []
                if totals["buys"] > 25 and totals["sells"] < 5:
                    recs.append("Many BUYs but few SELLs → consider adding persistent position reconciliation to avoid re-entering on restarts.")
                if float(state.get("positionCapPct", 0.3)) > 0.3:
                    recs.append("Position cap >30% → expect larger drawdowns; consider 10–30% during testing.")
                if not recs:
                    recs.append("Next upgrades: fee/slippage model + persist position across restarts + regime filter.")

                msg = (
                    f"Hourly Update (paper)\n"
                    f"Realized PnL: {realized:.2f} USDT\n"
                    f"Unrealized PnL: {unreal:.2f} USDT\n"
                    f"BUY trades: {totals['buys']} | USDT spent: {totals['buyUsdt']:.2f}\n"
                    f"SELL trades: {totals['sells']} | USDT received: {totals['sellUsdt']:.2f}\n"
                    f"Recommendations:\n- " + "\n- ".join(recs)
                )
                try:
                    _tg_send(tg_token, int(state["adminChatId"]), msg)
                    last_hourly_at = _utc_now()
                except Exception as e:
                    _log(f"HOURLY_SEND_FAILED {e}")

        # 8-hour summary
        if state.get("adminChatId") and tg_token:
            if _utc_now() - last_summary_at >= timedelta(hours=8):
                wr = (stats.wins / stats.trades) if stats.trades else 0.0
                cum = _read_cum()
                cum_wr = (cum.get("wins", 0) / cum.get("trades", 1)) if cum.get("trades", 0) else 0.0
                # include last 3 trades
                last_lines = []
                try:
                    if os.path.exists(TRADES_PATH):
                        with open(TRADES_PATH, "r", encoding="utf-8") as f:
                            all_lines = [ln for ln in f.read().splitlines() if ln.strip()]
                        # keep only ENTER/EXIT
                        events = []
                        for ln in all_lines[-50:]:
                            try:
                                e = json.loads(ln)
                                if e.get("event") in ("ENTER", "EXIT"):
                                    events.append(e)
                            except Exception:
                                continue
                        last_lines = events[-3:]
                except Exception:
                    last_lines = []

                trades_block = ""
                trades_block = ""
                if last_lines:
                    trades_block = "Recent trades (extracted from trades.jsonl):\n"
                    for e in last_lines:
                        ts = (e.get("tsUtc") or "").replace("T", " ").replace("+00:00", " UTC").replace("Z", " UTC")
                        ev = e.get("event")
                        qty = float(e.get("qtyBtc", 0.0))
                        px = float(e.get("price", 0.0))
                        notional = float(e.get("notionalUsdt", 0.0))
                        if ev == "ENTER":
                            trades_block += f"- {ts} • ENTER {symbol} • qty={qty:.6f} @ {px:.2f} • notional={notional:.2f} USDT\n"
                        elif ev == "EXIT":
                            pnl = float(e.get("realizedPnlUsdt", 0.0))
                            reason = e.get("reason", "")
                            trades_block += f"- {ts} • EXIT  {symbol} • pnl={pnl:.2f} USDT • reason={reason}\n"

                msg = (
                    f"8h Summary (paper)\n"
                    f"symbol={symbol}\n"
                    f"session: trades={stats.trades} winrate={wr*100:.1f}% pnl={stats.pnl_usdt:.2f} USDT maxDD={stats.max_drawdown_pct*100:.2f}%\n"
                    f"cumulative since {cum.get('sinceUtc')}: trades={cum.get('trades')} winrate={cum_wr*100:.1f}% realizedPnL={cum.get('realizedPnlUsdt'):.2f} USDT\n"
                    f"equity={paper.equity(price):.2f} USDT (price={price:.2f})\n"
                    f"{trades_block}".strip()
                )
                try:
                    _tg_send(tg_token, int(state["adminChatId"]), msg)
                    last_summary_at = _utc_now()
                except Exception as e:
                    _log(f"SUMMARY_SEND_FAILED {e}")

        # pace loop: check roughly every 30s
        time.sleep(30)


if __name__ == "__main__":
    main()
