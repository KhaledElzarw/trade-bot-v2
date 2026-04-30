import json
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from bot import BinanceSpotREST


HERE = os.path.dirname(__file__)

# Env is loaded at import-time from TRADEBOT_ENV_FILE (defaults to .env)
_ENV_FILE = os.getenv("TRADEBOT_ENV_FILE") or os.path.join(HERE, ".env")
load_dotenv(_ENV_FILE, override=False)

STATE_PATH = os.path.join(HERE, "state.json")
LOG_PATH = os.path.join(HERE, "advisor.log")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dubai_now_str() -> str:
    dz = ZoneInfo("Asia/Dubai")
    return _utc_now().astimezone(dz).strftime("%Y-%m-%d %H:%M:%S GST")


def _log(msg: str) -> None:
    line = f"[{_utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _required(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _tg_send(token: str, chat_id: int, text: str) -> None:
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


def _sleep_until_next_quarter() -> None:
    now = _utc_now()
    # next boundary at minute 0/15/30/45
    m = now.minute
    next_m = ((m // 15) + 1) * 15
    next_hour = now.replace(second=0, microsecond=0)
    if next_m >= 60:
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        next_hour = now.replace(minute=next_m, second=0, microsecond=0)

    dt = (next_hour - now).total_seconds()
    time.sleep(max(1.0, dt))


def _diff(old: dict, new: dict) -> list[str]:
    keys = sorted(set(old.keys()) | set(new.keys()))
    out = []
    for k in keys:
        if old.get(k) != new.get(k):
            out.append(f"- {k}: {old.get(k)} -> {new.get(k)}")
    return out


def _advisor_light(state: dict, market: dict) -> dict:
    # Deterministic "AI-like" advisor (no LLM yet): choose scalpy/fatty based on ATR% and trend strength.
    price = market["price"]
    atr_pct = market["atr"] / price if price else 0.0
    trend_strength = market["trend_strength"]

    # Prefer scalpy (more trades) when volatility is moderate and trend is not extreme.
    if atr_pct >= 0.012 or trend_strength >= 0.006:
        chosen = "fatty"
    else:
        chosen = "scalpy"

    # Respect manual override: if user explicitly set scalpy/fatty, keep it.
    if state.get("gridMode") in ("scalpy", "fatty"):
        chosen = state["gridMode"]

    patch = dict(state)
    patch["gridMode"] = chosen

    # Nudge trailing stop aggressiveness based on volatility.
    # Base is 2.0x; widen slightly when volatility is high.
    base = float(state.get("gridTrailAtrMult", 2.0))
    if atr_pct >= 0.015:
        patch["gridTrailAtrMult"] = max(base, 2.5)
    elif atr_pct <= 0.008:
        patch["gridTrailAtrMult"] = min(base, 2.0)

    patch["advisorLastLightUtc"] = _utc_now().isoformat()
    return patch


def _advisor_full(state: dict, market: dict) -> dict:
    # Hourly heavier adjustments (still bounded + auditable)
    price = market["price"]
    atr_pct = market["atr"] / price if price else 0.0

    patch = dict(state)

    # If user wants more trades/day, raise cap but keep a sanity ceiling.
    patch["maxTradesPerDay"] = int(min(400, max(int(state.get("maxTradesPerDay", 200)), 200)))

    # Fee-aware minimum spacing:
    # With 10bps fees, a full buy+sell cycle costs ~20bps. Scalpy needs room above that.
    min_scalpy = float(state.get("gridMinSpacingPctScalpy", 0.006))
    min_fatty = float(state.get("gridMinSpacingPctFatty", 0.010))

    if patch.get("gridMode") == "scalpy":
        patch["gridSpacingPct"] = max(min_scalpy, 0.8 * atr_pct)
        patch["gridLevels"] = int(max(10, min(18, state.get("gridLevels", 14))))
    else:
        patch["gridSpacingPct"] = max(min_fatty, 1.4 * atr_pct)
        patch["gridLevels"] = int(max(6, min(12, state.get("gridLevels", 8))))

    patch["advisorLastFullUtc"] = _utc_now().isoformat()
    return patch


def _fetch_market(client: BinanceSpotREST, symbol: str, interval: str) -> dict:
    kl = client.klines(symbol=symbol, interval=interval, limit=210)
    close = [float(k[4]) for k in kl]
    high = [float(k[2]) for k in kl]
    low = [float(k[3]) for k in kl]

    price = close[-1]
    atr = _atr(high, low, close, period=14)
    ema20 = _ema(close[-60:], period=20)
    ema50 = _ema(close[-120:], period=50)
    trend_strength = abs(ema20 - ema50) / price

    return {"price": price, "atr": atr, "trend_strength": trend_strength}


def main() -> None:
    base_url = os.getenv("BINANCE_BASE_URL", "https://testnet.binance.vision")
    md_url = os.getenv("BINANCE_MARKETDATA_URL", "https://api.binance.com")
    api_key = _required("BINANCE_API_KEY")
    api_secret = _required("BINANCE_API_SECRET")

    tg_token = os.getenv("TELEGRAM_CONTROL_BOT_TOKEN")

    # Use prod market data by default; testnet klines can have absurd wicks.
    client = BinanceSpotREST(base_url=base_url, api_key=api_key, api_secret=api_secret)
    md = BinanceSpotREST(base_url=md_url, api_key=api_key, api_secret=api_secret)

    _log("ADVISOR_START")

    while True:
        try:
            state = _read_json(STATE_PATH)
            if not state.get("advisorEnabled", True):
                _sleep_until_next_quarter()
                continue

            symbol = state.get("symbol", "BTCUSDT")
            interval = state.get("interval", "15m")

            market = _fetch_market(md, symbol=symbol, interval=interval)

            # LIGHT every 15m
            new_state = _advisor_light(state, market)

            # FULL every hour (minute == 0)
            if _utc_now().minute == 0:
                new_state = _advisor_full(new_state, market)

            changes = _diff(state, new_state)
            if changes:
                _write_json(STATE_PATH, new_state)

                if tg_token and new_state.get("adminChatId"):
                    title = f"AI Advisor update ({_dubai_now_str()})\nprice={market['price']:.2f} atr={market['atr']:.2f} trend={market['trend_strength']:.4f}"
                    body = "\n".join(changes[:40])
                    if len(changes) > 40:
                        body += f"\n... (+{len(changes)-40} more)"
                    _tg_send(tg_token, int(new_state["adminChatId"]), title + "\n" + body)

                _log(f"APPLIED {len(changes)} changes")
            else:
                _log("NO_CHANGES")

        except Exception as e:
            _log(f"ERROR {type(e).__name__}: {e}")

        _sleep_until_next_quarter()


if __name__ == "__main__":
    main()
