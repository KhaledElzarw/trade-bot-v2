import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from binance_client import BinanceSpotREST
import sqlite_store


STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")
STATUS_PATH = os.path.join(os.path.dirname(__file__), "engine_status.json")
LOG_PATH = os.path.join(os.path.dirname(__file__), "engine.log")
TRADES_PATH = os.path.join(os.path.dirname(__file__), "trades.jsonl")
CUM_PATH = os.path.join(os.path.dirname(__file__), "cumulative.json")
RUNTIME_PATH = os.path.join(os.path.dirname(__file__), "runtime_state.json")
AI_SIGNAL_PATH = os.path.join(os.path.dirname(__file__), "ai_signal.json")
DEFAULT_TRADES_PATH = TRADES_PATH

# Load env from an explicit file (preferred) or default to .env in this folder.
HERE = os.path.dirname(__file__)
_ENV_FILE = os.getenv("TRADEBOT_ENV_FILE") or os.path.join(HERE, ".env")
load_dotenv(_ENV_FILE, override=False)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


RUNTIME_SNAPSHOT_MIN_SECONDS = max(1.0, _env_float("TRADEBOT_RUNTIME_SNAPSHOT_MIN_SECONDS", 15.0))
RUNTIME_SNAPSHOT_MAX_SECONDS = max(
    RUNTIME_SNAPSHOT_MIN_SECONDS,
    _env_float("TRADEBOT_RUNTIME_SNAPSHOT_MAX_SECONDS", 60.0),
)
RUNTIME_SNAPSHOT_MARKET_CHANGE_BPS = max(0.0, _env_float("TRADEBOT_RUNTIME_SNAPSHOT_MARKET_CHANGE_BPS", 5.0))
HEARTBEAT_LOG_SECONDS = max(0.0, _env_float("TRADEBOT_ENGINE_HEARTBEAT_LOG_SECONDS", 1.0))
SUPPORTED_GRID_MODES = {"scalpy", "fatty"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: str) -> dict:
    key = sqlite_store.snapshot_key_for_path(path)
    if key and sqlite_store.has_snapshot(key):
        return sqlite_store.read_snapshot(key)
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
    key = sqlite_store.snapshot_key_for_path(path)
    if key:
        sqlite_store.write_snapshot(key, obj)
    _atomic_json_write(path, obj)


def _log(msg: str) -> None:
    line = f"[{_utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _write_status(payload: dict) -> None:
    sqlite_store.write_snapshot("engine_status", payload)
    _atomic_json_write(STATUS_PATH, payload)


def _append_trade(event: dict) -> None:
    if TRADES_PATH == DEFAULT_TRADES_PATH:
        sqlite_store.append_event(event)
    with open(TRADES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def _safe_read_json(path: str) -> dict:
    try:
        return _read_json(path)
    except Exception:
        return {}


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _acquire_engine_lock() -> tuple[int, bool]:
    current_pid = os.getpid()
    runtime_state = _safe_read_json(RUNTIME_PATH)
    owner_pid = int(runtime_state.get("enginePid") or 0)
    had_existing_owner = bool(owner_pid and owner_pid != current_pid and _pid_alive(owner_pid))
    if had_existing_owner:
        raise RuntimeError(f"Engine already running with pid {owner_pid}")
    is_fresh_start = int(runtime_state.get("enginePid") or 0) != current_pid
    runtime_state["enginePid"] = current_pid
    runtime_state["engineStartedAt"] = _utc_now().isoformat() if is_fresh_start else runtime_state.get("engineStartedAt") or _utc_now().isoformat()
    runtime_state["savedAt"] = _utc_now().isoformat()
    _write_runtime_state(runtime_state)
    return current_pid, is_fresh_start


def _read_cum() -> dict:
    if sqlite_store.has_snapshot("cumulative"):
        return _normalize_cumulative(sqlite_store.read_snapshot("cumulative"))
    if not os.path.exists(CUM_PATH):
        return {"sinceUtc": None, "realizedPnlUsdt": 0.0, "grossRealizedPnlUsdt": 0.0, "feesPaidUsdt": 0.0, "trades": 0, "wins": 0, "losses": 0}
    with open(CUM_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if "grossRealizedPnlUsdt" not in payload:
        payload["grossRealizedPnlUsdt"] = float(payload.get("realizedPnlUsdt", 0.0) or 0.0) + float(payload.get("feesPaidUsdt", 0.0) or 0.0)
    return _normalize_cumulative(payload)


def _write_cum(c: dict) -> None:
    payload = _normalize_cumulative(c)
    sqlite_store.write_snapshot("cumulative", payload)
    _atomic_json_write(CUM_PATH, payload)


def _normalize_cumulative(c: dict) -> dict:
    normalized = dict(c or {})
    normalized["feesPaidUsdt"] = float(normalized.get("feesPaidUsdt", 0.0) or 0.0)
    normalized["grossRealizedPnlUsdt"] = float(normalized.get("grossRealizedPnlUsdt", 0.0) or 0.0)
    normalized["realizedPnlUsdt"] = normalized["grossRealizedPnlUsdt"] - normalized["feesPaidUsdt"]
    normalized["trades"] = int(normalized.get("trades", 0) or 0)
    normalized["wins"] = int(normalized.get("wins", 0) or 0)
    normalized["losses"] = int(normalized.get("losses", 0) or 0)
    return normalized


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
    if sqlite_store.has_snapshot("runtime_state"):
        return sqlite_store.read_snapshot("runtime_state")
    if not os.path.exists(RUNTIME_PATH):
        return {}
    with open(RUNTIME_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_runtime_state(payload: dict) -> None:
    sqlite_store.write_snapshot("runtime_state", payload)
    _atomic_json_write(RUNTIME_PATH, payload)


@dataclass
class _SnapshotDecision:
    should_persist: bool
    reason: str
    critical_signature: str
    market_signature: str
    market_values: dict
    now_monotonic: float


@dataclass
class _SnapshotChangeGate:
    min_interval_seconds: float
    max_interval_seconds: float
    market_change_bps: float
    last_critical_signature: str | None = None
    last_market_signature: str | None = None
    last_market_values: dict | None = None
    last_persist_monotonic: float | None = None

    def evaluate(
        self,
        *,
        critical_signature: str,
        market_signature: str = "",
        market_values: dict | None = None,
        force: bool = False,
        now_monotonic: float | None = None,
    ) -> _SnapshotDecision:
        now_value = time.monotonic() if now_monotonic is None else now_monotonic
        market_payload = dict(market_values or {})
        reason = "unchanged"
        should_persist = False

        if force:
            should_persist = True
            reason = "forced"
        elif self.last_critical_signature is None:
            should_persist = True
            reason = "initial"
        else:
            elapsed = now_value - (self.last_persist_monotonic or 0.0)
            if critical_signature != self.last_critical_signature:
                should_persist = True
                reason = "state_changed"
            elif elapsed >= self.max_interval_seconds:
                should_persist = True
                reason = "refresh_interval"
            elif elapsed >= self.min_interval_seconds:
                if market_signature != (self.last_market_signature or ""):
                    should_persist = True
                    reason = "market_window_changed"
                elif _market_values_moved(self.last_market_values or {}, market_payload, self.market_change_bps):
                    should_persist = True
                    reason = "market_moved"
            else:
                reason = "throttled"

        return _SnapshotDecision(
            should_persist=should_persist,
            reason=reason,
            critical_signature=critical_signature,
            market_signature=market_signature,
            market_values=market_payload,
            now_monotonic=now_value,
        )

    def record(self, decision: _SnapshotDecision) -> None:
        self.last_critical_signature = decision.critical_signature
        self.last_market_signature = decision.market_signature
        self.last_market_values = dict(decision.market_values)
        self.last_persist_monotonic = decision.now_monotonic


def _stable_signature(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _round_snapshot_value(value, digits: int = 10):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {k: _round_snapshot_value(v, digits) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_round_snapshot_value(v, digits) for v in value]
    return value


def _float_or_none(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _relative_bps_moved(previous, current, bps: float) -> bool:
    prev = _float_or_none(previous)
    cur = _float_or_none(current)
    if prev is None and cur is None:
        return False
    if prev is None or cur is None:
        return True
    if bps <= 0:
        return cur != prev
    if abs(prev) < 1e-12:
        return abs(cur - prev) > 1e-12
    return (abs(cur - prev) / abs(prev)) * 10_000.0 >= bps


def _market_values_moved(previous: dict, current: dict, bps: float) -> bool:
    keys = set(previous.keys()) | set(current.keys())
    return any(_relative_bps_moved(previous.get(k), current.get(k), bps) for k in keys)


def _ai_control_signature(ai_signal: dict | None) -> dict:
    ai = ai_signal or {}
    keys = [
        "enabled",
        "source",
        "stale",
        "decisionId",
        "provider",
        "model",
        "riskAction",
        "confidence",
        "breakoutRisk",
        "gridAllowed",
        "pauseNewBuys",
        "allowSellsOnly",
        "flattenRecommended",
        "reduceExposure",
        "riskBudgetPct",
        "recommendedSpacingPct",
        "recommendedLevels",
        "recommendedMaxExposurePct",
        "recommendedMode",
        "dryRun",
        "shadowMode",
    ]
    return _round_snapshot_value({k: ai.get(k) for k in keys if k in ai}, 8)


def _runtime_critical_payload(runtime_payload: dict) -> dict:
    stats = runtime_payload.get("stats") or {}
    stats_keys = [
        "day",
        "trades",
        "closedTrades",
        "entries",
        "exits",
        "hasOpenPosition",
        "wins",
        "losses",
        "pnl_usdt",
        "cooldown_until",
    ]
    return {
        "enginePid": runtime_payload.get("enginePid"),
        "paper": _round_snapshot_value(runtime_payload.get("paper") or {}, 10),
        "stats": _round_snapshot_value({k: stats.get(k) for k in stats_keys if k in stats}, 10),
        "grid": _round_snapshot_value(runtime_payload.get("grid"), 10),
        "ai": _ai_control_signature(runtime_payload.get("ai") or {}),
    }


def _runtime_snapshot_change_inputs(runtime_payload: dict) -> tuple[str, str, dict]:
    market = runtime_payload.get("market") or {}
    candle = market.get("candle") or {}
    stats = runtime_payload.get("stats") or {}
    critical = _runtime_critical_payload(runtime_payload)
    market_window = {
        "openTimeMs": candle.get("openTimeMs"),
        "closeTimeMs": candle.get("closeTimeMs"),
    }
    market_values = {
        "price": market.get("price"),
        "candleOpen": candle.get("open"),
        "candleHigh": candle.get("high"),
        "candleLow": candle.get("low"),
        "candleClose": candle.get("close"),
        "maxDrawdownPct": stats.get("max_drawdown_pct"),
        "peakEquity": stats.get("peak_equity"),
    }
    return _stable_signature(critical), _stable_signature(market_window), market_values


def _build_runtime_market_payload(kl: list, close: list[float], *, price: float, candle_hi: float, candle_lo: float) -> dict:
    return {
        "price": price,
        "candle": {
            "open": float(kl[-1][1]) if kl and len(kl[-1]) > 1 else (close[-2] if len(close) >= 2 else price),
            "high": candle_hi,
            "low": candle_lo,
            "close": price,
            "volumeBase": float(kl[-1][5]) if kl and len(kl[-1]) > 5 else 0.0,
            "volumeUsdt": float(kl[-1][7]) if kl and len(kl[-1]) > 7 else 0.0,
            "openTimeMs": int(kl[-1][0]) if kl and len(kl[-1]) > 0 else None,
            "closeTimeMs": int(kl[-1][6]) if kl and len(kl[-1]) > 6 else None,
        },
    }


def _maybe_write_runtime_state(gate: _SnapshotChangeGate, runtime_payload: dict, *, force: bool = False) -> tuple[bool, str]:
    critical_signature, market_signature, market_values = _runtime_snapshot_change_inputs(runtime_payload)
    decision = gate.evaluate(
        critical_signature=critical_signature,
        market_signature=market_signature,
        market_values=market_values,
        force=force,
    )
    if not decision.should_persist:
        return False, decision.reason
    _write_runtime_state(runtime_payload)
    gate.record(decision)
    _log(f"RUNTIME_STATE_WRITE reason={decision.reason} savedAt={runtime_payload.get('savedAt')} ai_model={((runtime_payload.get('ai') or {}).get('model'))} has_ai={('ai' in runtime_payload)}")
    return True, decision.reason


def _should_emit_heartbeat_log(last_logged_monotonic: float | None, interval_seconds: float, *, now_monotonic: float | None = None) -> bool:
    if interval_seconds <= 0:
        return False
    now_value = time.monotonic() if now_monotonic is None else now_monotonic
    if last_logged_monotonic is None:
        return True
    return (now_value - last_logged_monotonic) >= interval_seconds


def _required(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _read_ai_signal() -> dict:
    if sqlite_store.has_snapshot("ai_signal"):
        return sqlite_store.read_snapshot("ai_signal")
    if not os.path.exists(AI_SIGNAL_PATH):
        return {}
    try:
        with open(AI_SIGNAL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_ai_signal(payload: dict) -> None:
    sqlite_store.write_snapshot("ai_signal", payload)
    _atomic_json_write(AI_SIGNAL_PATH, payload)


def _load_trade_events() -> list[dict]:
    if TRADES_PATH == DEFAULT_TRADES_PATH:
        rows = sqlite_store.list_events()
        if rows:
            return rows
    if not os.path.exists(TRADES_PATH):
        return []
    rows: list[dict] = []
    with open(TRADES_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _reconcile_accounting_from_trade_log(start_usdt: float, start_btc: float) -> dict:
    usdt = float(start_usdt)
    btc = float(start_btc)
    realized = 0.0
    gross_realized = 0.0
    fees = 0.0
    wins = 0
    losses = 0
    open_cost_basis = 0.0
    anomalies: list[str] = []
    active_engine_start_seen = False

    for ev in _load_trade_events():
        kind = ev.get("event")
        qty = float(ev.get("qtyBtc") or 0.0)
        notional = float(ev.get("notionalUsdt") or 0.0)
        fee = float(ev.get("feeUsdt") or 0.0)

        if kind == "ENGINE_START":
            if active_engine_start_seen:
                anomalies.append(f"duplicate_engine_start:{ev.get('tsUtc')}")
            active_engine_start_seen = True
            continue

        active_engine_start_seen = False

        if kind == "ENTER":
            fees += fee
            usdt -= (notional + fee)
            btc += qty
            open_cost_basis += (notional + fee)
            continue
        fee = float(ev.get("feeUsdt") or 0.0)

        if kind == "EXIT":
            if qty <= 0:
                continue
            if qty > btc + 1e-12:
                anomalies.append(f"oversell:{ev.get('tsUtc')} qty={qty} btc_before={btc}")
                continue

            fees += fee
            usdt += (notional - fee)
            btc_before = btc
            btc -= qty

            basis_sold = 0.0
            if btc_before > 0 and open_cost_basis > 0:
                basis_sold = open_cost_basis * (qty / btc_before)
                open_cost_basis -= basis_sold
            gross = float(ev.get("grossRealizedPnlUsdt") or (notional - basis_sold))
            pnl = float(ev.get("realizedPnlUsdt") or (notional - fee - basis_sold))
            gross_realized += gross
            realized += pnl
            if pnl >= 0:
                wins += 1
            else:
                losses += 1

    if abs(btc) < 1e-12:
        btc = 0.0
    if open_cost_basis < 0 and abs(open_cost_basis) < 1e-9:
        open_cost_basis = 0.0

    return {
        "paper": {"usdt": usdt, "btc": btc},
        "cumulative": {
            "sinceUtc": None,
            "realizedPnlUsdt": realized,
            "grossRealizedPnlUsdt": gross_realized,
            "feesPaidUsdt": fees,
            "trades": wins + losses,
            "wins": wins,
            "losses": losses,
        },
        "openCostBasisUsdt": open_cost_basis,
        "anomalies": anomalies,
    }


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
    signal = dict(signal)

    def _num(name: str, default: float, lo: float, hi: float) -> float:
        try:
            value = signal.get(name, default)
            if isinstance(value, list) and value:
                value = value[0]
            value = float(value)
        except Exception:
            value = default
        return _clamp(value, lo, hi)

    def _bool(name: str, default: bool = False) -> bool:
        value = signal.get(name, default)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    signal["confidence"] = _num("confidence", 0.0, 0.0, 1.0)
    signal["breakoutRisk"] = _num("breakoutRisk", 0.0, 0.0, 1.0)
    signal["recommendedSpacingPct"] = _num("recommendedSpacingPct", float(state.get("gridSpacingPct", 0.008) or 0.008), 0.003, 0.03)
    signal["recommendedLevels"] = int(_num("recommendedLevels", float(state.get("gridLevels", 12) or 12), 4.0, 24.0))
    signal["recommendedMaxExposurePct"] = _num("recommendedMaxExposurePct", float(state.get("gridMaxExposurePct", 0.35) or 0.35), 0.05, 0.60)
    signal["riskBudgetPct"] = _num("riskBudgetPct", signal["recommendedMaxExposurePct"], 0.0, 1.0)
    signal["gridAllowed"] = bool(signal.get("gridAllowed", True))
    risk_action = signal.get("riskAction", "allow_grid")
    if risk_action not in {"allow_grid", "pause_new_buys", "sells_only", "reduce_exposure", "flatten", "hold"}:
        risk_action = "hold"
    signal["riskAction"] = risk_action
    signal["pauseNewBuys"] = _bool("pauseNewBuys", risk_action in {"pause_new_buys", "sells_only", "reduce_exposure", "flatten"})
    signal["allowSellsOnly"] = _bool("allowSellsOnly", risk_action in {"sells_only", "reduce_exposure", "flatten"})
    signal["flattenRecommended"] = _bool("flattenRecommended", risk_action == "flatten")
    signal["reduceExposure"] = _bool("reduceExposure", risk_action in {"reduce_exposure", "flatten"})
    signal["dryRun"] = _bool("dryRun", False)
    signal["shadowMode"] = _bool("shadowMode", False)
    mode = signal.get("recommendedMode")
    if mode not in SUPPORTED_GRID_MODES:
        signal["recommendedMode"] = state.get("gridMode", "scalpy")
    return signal


def _ai_controls_active(ai_signal: dict) -> bool:
    return (
        bool(ai_signal.get("enabled"))
        and not bool(ai_signal.get("stale"))
        and not bool(ai_signal.get("dryRun"))
        and not bool(ai_signal.get("shadowMode"))
    )


def _attach_ai_event_fields(event: dict, ai_signal: dict) -> dict:
    if not ai_signal:
        return event
    if ai_signal.get("decisionId"):
        event["aiDecisionId"] = ai_signal.get("decisionId")
    if ai_signal.get("riskAction"):
        event["aiRiskAction"] = ai_signal.get("riskAction")
    if ai_signal.get("promptVersion"):
        event["aiPromptVersion"] = ai_signal.get("promptVersion")
    if ai_signal.get("confidence") is not None:
        event["aiConfidence"] = ai_signal.get("confidence")
    if ai_signal.get("model"):
        event["aiModel"] = ai_signal.get("model")
    return event


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


def _roll_stats_if_new_day(stats: Stats, now: datetime, equity: float) -> Stats:
    day = _day_key(now)
    if day == stats.day:
        return stats
    return Stats(day=day, peak_equity=equity)


def _grid_mode_error(mode: object) -> ValueError:
    allowed = ", ".join(sorted(SUPPORTED_GRID_MODES))
    return ValueError(f"unsupported gridMode {mode!r}; gridMode must be one of: {allowed}")


def _resolve_grid_mode(state: dict, ai_signal: dict, ai_live: bool) -> str:
    grid_mode = state.get("gridMode", "scalpy")
    if grid_mode not in SUPPORTED_GRID_MODES:
        raise _grid_mode_error(grid_mode)
    if ai_live and ai_signal.get("recommendedMode") in SUPPORTED_GRID_MODES:
        return str(ai_signal.get("recommendedMode"))
    return str(grid_mode)


def _grid_telemetry(
    *,
    state: dict,
    ai_signal: dict,
    effective_mode: str | None,
    spacing_pct=None,
    levels=None,
    open_orders: int = 0,
    **extra,
) -> dict:
    payload = {
        "mode": effective_mode if effective_mode is not None else state.get("gridMode"),
        "configuredMode": state.get("gridMode"),
        "aiRecommendedMode": ai_signal.get("recommendedMode") if ai_signal else None,
        "spacingPct": spacing_pct,
        "levels": levels,
        "openOrders": open_orders,
    }
    payload.update(extra)
    return payload


def _status_stats_payload(
    *,
    stats: Stats,
    cum: dict | None = None,
    entries_count: int | None = None,
    exits_count: int | None = None,
    has_open_position: bool | None = None,
    trend_strength: float | None = None,
    grid_payload: dict | None = None,
    ai_signal: dict | None = None,
    include_cooldown: bool = False,
) -> dict:
    trades = int(cum.get("trades", stats.trades)) if cum is not None else stats.trades
    wins = int(cum.get("wins", stats.wins)) if cum is not None else stats.wins
    losses = int(cum.get("losses", stats.losses)) if cum is not None else stats.losses
    pnl_usdt = float(cum.get("realizedPnlUsdt", stats.pnl_usdt)) if cum is not None else stats.pnl_usdt
    include_detail_counts = entries_count is not None or exits_count is not None or has_open_position is not None

    payload = {
        "day": stats.day,
        "trades": trades,
    }
    if include_detail_counts:
        payload["closedTrades"] = trades
    if entries_count is not None:
        payload["entries"] = entries_count
    if exits_count is not None:
        payload["exits"] = exits_count
    if has_open_position is not None:
        payload["hasOpenPosition"] = has_open_position
    payload.update({
        "wins": wins,
        "losses": losses,
        "pnlUsdt": pnl_usdt,
        "maxDrawdownPct": stats.max_drawdown_pct,
    })
    if trend_strength is not None:
        payload["trendStrength"] = trend_strength
    if include_cooldown:
        payload["cooldownUntil"] = stats.cooldown_until.isoformat() if stats.cooldown_until else None
    if grid_payload is not None:
        payload["grid"] = grid_payload
    if ai_signal is not None:
        payload["ai"] = ai_signal
    return payload


def _status_payload(
    *,
    state: dict,
    symbol: str,
    interval: str,
    price: float,
    paper: PaperAccount,
    position_payload: dict | None,
    stats_payload: dict,
    last_event: str,
) -> dict:
    return {
        "tsUtc": _utc_now().isoformat(),
        "mode": state.get("mode"),
        "symbol": symbol,
        "interval": interval,
        "price": price,
        "equityUsdt": paper.equity(price),
        "usdt": paper.usdt,
        "btc": paper.btc,
        "position": position_payload,
        "stats": stats_payload,
        "lastEvent": last_event,
    }


def _runtime_payload(
    *,
    engine_pid: int,
    paper: PaperAccount,
    stats: Stats,
    entries_count: int,
    exits_count: int,
    has_open_position: bool,
    market_payload: dict,
    grid: GridState | None,
    ai_signal: dict,
    cum: dict | None = None,
    saved_at: str | None = None,
) -> dict:
    if cum is not None:
        trades = int(cum.get("trades", stats.trades or 0))
        wins = int(cum.get("wins", stats.wins))
        losses = int(cum.get("losses", stats.losses))
        pnl_usdt = float(cum.get("realizedPnlUsdt", stats.pnl_usdt))
    else:
        trades = stats.trades
        wins = stats.wins
        losses = stats.losses
        pnl_usdt = stats.pnl_usdt

    return {
        "enginePid": engine_pid,
        "paper": {
            "usdt": paper.usdt,
            "btc": paper.btc,
        },
        "stats": {
            "day": stats.day,
            "trades": trades,
            "closedTrades": trades,
            "entries": entries_count,
            "exits": exits_count,
            "hasOpenPosition": has_open_position,
            "wins": wins,
            "losses": losses,
            "pnl_usdt": pnl_usdt,
            "max_drawdown_pct": stats.max_drawdown_pct,
            "peak_equity": stats.peak_equity,
            "cooldown_until": stats.cooldown_until.isoformat() if stats.cooldown_until else None,
        },
        "market": market_payload,
        "grid": _serialize_grid(grid),
        "ai": ai_signal,
        "savedAt": saved_at if saved_at is not None else _utc_now().isoformat(),
    }


def _position_payload(paper: PaperAccount, grid: GridState | None, price: float) -> dict | None:
    if paper.btc <= 0:
        return None
    unreal = 0.0
    unreal_pct = 0.0
    if grid and grid.cost_basis_usdt > 0:
        mkt_value = paper.btc * price
        unreal = mkt_value - grid.cost_basis_usdt
        avg_cost = grid.cost_basis_usdt / paper.btc if paper.btc else 0.0
        unreal_pct = (price / avg_cost - 1.0) if avg_cost else 0.0
    return {
        "entryPrice": (grid.cost_basis_usdt / paper.btc) if (grid and paper.btc > 0) else None,
        "qtyBtc": paper.btc,
        "stop": float((grid.__dict__.get("trail_stop", 0.0) or 0.0)) if grid else None,
        "tp": None,
        "entryTimeUtc": grid.last_recenter_utc if grid else None,
        "unrealizedPnlUsdt": unreal,
        "unrealizedPnlPct": unreal_pct,
    }


def _spacing_for_mode(mode: str | None, atr: float, price: float, *, min_scalpy: float, min_fatty: float) -> tuple[float, int]:
    # Return (spacing_pct, levels)
    # NOTE: With 10bps fees, a full cycle (buy+sell) costs ~20bps, so spacing must be well above 0.20%.
    atr_pct = atr / price if price else 0.0
    if mode == "scalpy":
        spacing_pct = max(min_scalpy, 0.8 * atr_pct)
        levels = 14
        return spacing_pct, levels
    if mode == "fatty":
        spacing_pct = max(min_fatty, 1.4 * atr_pct)
        levels = 8
        return spacing_pct, levels

    raise _grid_mode_error(mode)


def _compute_grid_plan(
    state: dict,
    ai_signal: dict,
    ai_live: bool,
    *,
    grid_mode: str,
    atr: float,
    price: float,
) -> dict:
    min_scalpy = float(state.get("gridMinSpacingPctScalpy", 0.006))
    min_fatty = float(state.get("gridMinSpacingPctFatty", 0.010))
    spacing_pct, levels = _spacing_for_mode(
        grid_mode,
        atr=atr,
        price=price,
        min_scalpy=min_scalpy,
        min_fatty=min_fatty,
    )
    if ai_live:
        spacing_pct = _clamp(
            float(ai_signal.get("recommendedSpacingPct", spacing_pct) or spacing_pct),
            max(min_scalpy / 2, 0.003),
            0.03,
        )
        levels = int(_clamp(float(ai_signal.get("recommendedLevels", levels) or levels), 4, 24))
    max_expo = float(state.get("gridMaxExposurePct", 0.10))
    if ai_live:
        max_expo = _clamp(float(ai_signal.get("recommendedMaxExposurePct", max_expo) or max_expo), 0.05, 0.60)
        if ai_signal.get("reduceExposure"):
            risk_budget_pct = ai_signal.get("riskBudgetPct")
            risk_budget_pct = max_expo if risk_budget_pct is None else float(risk_budget_pct)
            max_expo = min(
                max_expo,
                _clamp(risk_budget_pct, 0.0, 0.60),
            )

    return {
        "spacing_pct": spacing_pct,
        "levels": levels,
        "max_expo": max_expo,
        "min_scalpy": min_scalpy,
        "min_fatty": min_fatty,
    }


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


def _select_crossed_grid_orders(
    orders: list[GridOrder],
    *,
    candle_lo: float,
    candle_hi: float,
    price: float,
    paper: PaperAccount,
    ai_pause_new_buys: bool,
    fee_rate: float,
) -> list[GridOrder]:
    filled: list[GridOrder] = []
    for o in orders:
        # A limit order only fills if the candle traded through that price.
        if not (candle_lo <= o.price <= candle_hi):
            continue

        if o.side == "BUY":
            if ai_pause_new_buys:
                continue
            # Must have enough USDT to buy AND pay the fee.
            est_total = o.qty_btc * o.price * (1 + fee_rate)
            if est_total <= paper.usdt:
                filled.append(o)

        elif o.side == "SELL":
            # Must actually have BTC to sell; otherwise we'd log fake 0-qty "fills".
            if o.qty_btc > 0 and paper.btc >= o.qty_btc:
                filled.append(o)

    return filled


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

    gross_realized = gross - basis_sold
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
        "grossRealizedPnlUsdt": gross_realized,
        "realizedPnlUsdt": realized,
        "paper": True,
    }


def main():
    engine_pid, is_fresh_start = _acquire_engine_lock()
    base_url = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")
    # Use real (prod) market data by default; testnet klines can be garbage (spike wicks).
    md_url = os.getenv("BINANCE_MARKETDATA_URL", "https://api.binance.com")

    api_key = _required("BINANCE_API_KEY")
    api_secret = _required("BINANCE_API_SECRET")

    state = _read_json(STATE_PATH)
    symbol = state.get("symbol", os.getenv("BINANCE_SYMBOL", "BTCUSDT"))
    interval = state.get("interval", "15m")

    # Trading client (testnet/prod depending on env)
    _client = BinanceSpotREST(base_url=base_url, api_key=api_key, api_secret=api_secret)
    # Market-data client (prod by default)
    md = BinanceSpotREST(base_url=md_url, api_key=api_key, api_secret=api_secret)

    runtime_state = _read_runtime_state()
    runtime_snapshot_gate = _SnapshotChangeGate(
        min_interval_seconds=RUNTIME_SNAPSHOT_MIN_SECONDS,
        max_interval_seconds=RUNTIME_SNAPSHOT_MAX_SECONDS,
        market_change_bps=RUNTIME_SNAPSHOT_MARKET_CHANGE_BPS,
    )
    last_heartbeat_log_monotonic: float | None = None
    reconciled = _reconcile_accounting_from_trade_log(
        start_usdt=float(state.get("paperStartUsdt", 10000.0)),
        start_btc=float(state.get("paperStartBtc", 0.0)),
    )
    paper_state = runtime_state.get("paper") or {}
    paper = PaperAccount(
        usdt=float(reconciled["paper"].get("usdt", paper_state.get("usdt", state.get("paperStartUsdt", 10000.0)))),
        btc=float(reconciled["paper"].get("btc", paper_state.get("btc", state.get("paperStartBtc", 0.0)))),
    )

    stats_state = runtime_state.get("stats") or {}
    reconciled_cum = dict(reconciled["cumulative"])
    stats = Stats(
        day=stats_state.get("day", _day_key(_utc_now())),
        trades=int(reconciled_cum.get("trades", 0)),
        wins=int(reconciled_cum.get("wins", 0)),
        losses=int(reconciled_cum.get("losses", 0)),
        pnl_usdt=float(reconciled_cum.get("realizedPnlUsdt", 0.0)),
        max_drawdown_pct=float(stats_state.get("max_drawdown_pct", 0.0)),
        peak_equity=float(stats_state.get("peak_equity", 0.0)),
        cooldown_until=datetime.fromisoformat(stats_state["cooldown_until"]) if stats_state.get("cooldown_until") else None,
    )

    cum = _read_cum()
    reconciled_cum["sinceUtc"] = cum.get("sinceUtc") or _utc_now().isoformat()
    changed_cum = any(cum.get(k) != reconciled_cum.get(k) for k in reconciled_cum.keys())
    cum = _normalize_cumulative(reconciled_cum)
    if changed_cum:
        _write_cum(cum)

    if reconciled.get("anomalies"):
        _log(f"ACCOUNTING_RECONCILE anomalies={'; '.join(reconciled['anomalies'])}")

    grid: GridState | None = _deserialize_grid(runtime_state.get("grid"))
    if grid is not None:
        grid.cost_basis_usdt = float(reconciled.get("openCostBasisUsdt", grid.cost_basis_usdt) or 0.0)
        if paper.btc <= 0:
            grid.active = False
            grid.orders = []
            grid.reserved_btc = 0.0
            grid.cost_basis_usdt = 0.0

    has_open_position = paper.btc > 0 or bool(grid and grid.active)
    if is_fresh_start:
        startup_event_name = "ENGINE_RESUME" if has_open_position else "ENGINE_START"
        _log(f"{startup_event_name} mode={state.get('mode')} symbol={symbol} interval={interval} paper_equity_init_usdt={paper.usdt} paper_btc_init={paper.btc}")
        start_event = {
            "tsUtc": _utc_now().isoformat(),
            "event": startup_event_name,
            "mode": state.get("mode"),
            "symbol": symbol,
            "paper": True,
            "enginePid": engine_pid,
            "hasOpenPosition": has_open_position,
        }
        _append_trade(start_event)

    while True:
        state = _read_json(STATE_PATH)

        now = _utc_now()

        kl = md.klines(symbol=symbol, interval=interval, limit=210)
        close = [float(k[4]) for k in kl]
        high = [float(k[2]) for k in kl]
        low = [float(k[3]) for k in kl]

        price = close[-1]
        candle_hi = high[-1]
        candle_lo = low[-1]

        eq = paper.equity(price)
        stats = _roll_stats_if_new_day(stats, now, eq)
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
            time.sleep(1)
            continue

        atr = _atr(high, low, close, period=14)
        ema20 = _ema(close[-60:], period=20)
        ema50 = _ema(close[-120:], period=50)
        trend_strength = abs(ema20 - ema50) / price
        ai_signal = _read_ai_decision_for_engine(state)
        ai_live = _ai_controls_active(ai_signal)
        ai_pause_new_buys = ai_live and (ai_signal.get("pauseNewBuys") or not ai_signal.get("gridAllowed", True))
        ai_sells_only = ai_live and (ai_signal.get("allowSellsOnly") or ai_signal.get("riskAction") in {"sells_only", "flatten"})
        try:
            grid_mode = _resolve_grid_mode(state, ai_signal, ai_live)
        except ValueError as exc:
            _log(f"GRID_CONFIG_INVALID {exc}")
            event_rows = _load_trade_events()
            entries_count = sum(1 for ev in event_rows if ev.get("event") == "ENTER")
            exits_count = sum(1 for ev in event_rows if ev.get("event") == "EXIT")
            has_open_position = paper.btc > 0
            cum = _read_cum()
            _write_status(_status_payload(
                state=state,
                symbol=symbol,
                interval=interval,
                price=price,
                paper=paper,
                position_payload=_position_payload(paper, grid, price),
                stats_payload=_status_stats_payload(
                    stats=stats,
                    cum=cum,
                    entries_count=entries_count,
                    exits_count=exits_count,
                    has_open_position=has_open_position,
                    trend_strength=trend_strength,
                    grid_payload=_grid_telemetry(
                        state=state,
                        ai_signal=ai_signal,
                        effective_mode=None,
                        spacing_pct=grid.spacing_pct if grid else None,
                        levels=grid.levels if grid else None,
                        open_orders=len(grid.orders) if grid else 0,
                        skipped=True,
                        skipReason="unsupported_grid_mode",
                        error=str(exc),
                    ),
                    ai_signal=ai_signal,
                ),
                last_event="GRID_CONFIG_INVALID",
            ))
            time.sleep(1)
            continue

        inactive_reason = "paused" if state.get("paused") else None
        if inactive_reason is None and stats.cooldown_until and now < stats.cooldown_until:
            inactive_reason = "cooldown_after_loss"

        # Keep dashboard status fresh while inactive; runtime snapshots are gated below.
        if inactive_reason:
            event_rows = _load_trade_events()
            entries_count = sum(1 for ev in event_rows if ev.get("event") == "ENTER")
            exits_count = sum(1 for ev in event_rows if ev.get("event") == "EXIT")
            has_open_position = paper.btc > 0
            cum = _read_cum()
            status_payload = _status_payload(
                state=state,
                symbol=symbol,
                interval=interval,
                price=price,
                paper=paper,
                position_payload=_position_payload(paper, grid, price),
                stats_payload=_status_stats_payload(
                    stats=stats,
                    cum=cum,
                    entries_count=entries_count,
                    exits_count=exits_count,
                    has_open_position=has_open_position,
                    trend_strength=trend_strength,
                    include_cooldown=True,
                    grid_payload=_grid_telemetry(
                        state=state,
                        ai_signal=ai_signal,
                        effective_mode=grid_mode,
                        spacing_pct=grid.spacing_pct if grid else None,
                        levels=grid.levels if grid else None,
                        open_orders=len(grid.orders) if grid else 0,
                        skipped=True,
                        skipReason=inactive_reason,
                    ),
                    ai_signal=ai_signal,
                ),
                last_event="PAUSED" if inactive_reason == "paused" else "COOLDOWN",
            )
            _write_status(status_payload)
            runtime_payload = _runtime_payload(
                engine_pid=os.getpid(),
                paper=paper,
                stats=stats,
                entries_count=entries_count,
                exits_count=exits_count,
                has_open_position=has_open_position,
                market_payload=_build_runtime_market_payload(
                    kl,
                    close,
                    price=price,
                    candle_hi=candle_hi,
                    candle_lo=candle_lo,
                ),
                grid=grid,
                ai_signal=ai_signal,
                cum=cum,
            )
            _maybe_write_runtime_state(runtime_snapshot_gate, runtime_payload)
            time.sleep(1)
            continue

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
                gross_realized = gross - grid.cost_basis_usdt
                realized = proceeds - grid.cost_basis_usdt

                # Guardrail: avoid exits that look green gross but end red net after fees/slippage.
                # Only allow a net-loss trail exit when breakout risk is genuinely strong enough to justify an escape.
                min_profit_pct = float(state.get("gridTrailMinNetProfitPct", 0.0010))  # 0.10%
                force_exit_trend = float(state.get("gridTrailForceExitTrendStrength", 0.02))
                want_profit = (avg_cost > 0) and (price >= avg_cost * (1 + min_profit_pct))
                gross_positive_net_negative = (gross_realized > 0.0 and realized < 0.0)
                strong_escape = trend_strength >= force_exit_trend
                if gross_positive_net_negative and not strong_escape:
                    time.sleep(1)
                    continue
                if (realized < 0) and (not strong_escape) and (not want_profit):
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
                cum["grossRealizedPnlUsdt"] = float(cum.get("grossRealizedPnlUsdt", 0.0)) + gross_realized
                stats.trades += 1
                if realized >= 0:
                    cum["wins"] = int(cum.get("wins", 0)) + 1
                    stats.wins += 1
                else:
                    cum["losses"] = int(cum.get("losses", 0)) + 1
                    stats.losses += 1
                    mins = int(state.get("cooldownMinutesAfterLoss", 20))
                    stats.cooldown_until = now + timedelta(minutes=mins)
                _write_cum(cum)
                stats.pnl_usdt = float(cum.get("realizedPnlUsdt", 0.0))

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
                    "grossRealizedPnlUsdt": gross_realized,
                    "realizedPnlUsdt": realized,
                    "paper": True,
                }
                _attach_ai_event_fields(exit_event, ai_signal)
                _append_trade(exit_event)

                event_rows = _load_trade_events()
                entries_count = sum(1 for ev in event_rows if ev.get("event") == "ENTER")
                exits_count = sum(1 for ev in event_rows if ev.get("event") == "EXIT")
                _write_status(_status_payload(
                    state=state,
                    symbol=symbol,
                    interval=interval,
                    price=price,
                    paper=paper,
                    position_payload=None,
                    stats_payload=_status_stats_payload(
                        stats=stats,
                        cum=cum,
                        entries_count=entries_count,
                        exits_count=exits_count,
                        has_open_position=False,
                        trend_strength=trend_strength,
                        grid_payload=_grid_telemetry(
                            state=state,
                            ai_signal=ai_signal,
                            effective_mode=grid_mode,
                            spacing_pct=grid.spacing_pct if grid else None,
                            levels=grid.levels if grid else None,
                            open_orders=0,
                        ),
                        ai_signal=ai_signal,
                    ),
                    last_event="EXIT",
                ))
                _write_runtime_state(_runtime_payload(
                    engine_pid=os.getpid(),
                    paper=paper,
                    stats=stats,
                    entries_count=entries_count,
                    exits_count=exits_count,
                    has_open_position=False,
                    market_payload=_build_runtime_market_payload(
                        kl,
                        close,
                        price=price,
                        candle_hi=candle_hi,
                        candle_lo=candle_lo,
                    ),
                    grid=grid,
                    ai_signal=ai_signal,
                    cum=cum,
                ))

                time.sleep(1)
                continue

        if ai_live and ai_signal.get("flattenRecommended") and paper.btc > 0 and grid and grid.cost_basis_usdt > 0:
            min_flatten_conf = max(float(state.get("aiMinConfidence", 0.55) or 0.55), 0.75)
            if float(ai_signal.get("confidence", 0.0) or 0.0) >= min_flatten_conf:
                fee_rate = float(state.get("feeBps", 10)) / 10_000.0
                market_slip_bps = float(state.get("paperMarketSlipBps", 12.0))
                qty = paper.btc
                effective_exit_price = price * (1 - (market_slip_bps / 10_000.0))
                gross = qty * effective_exit_price
                fee = gross * fee_rate
                proceeds = gross - fee
                gross_realized = gross - grid.cost_basis_usdt
                realized = proceeds - grid.cost_basis_usdt

                paper.btc = 0.0
                paper.usdt += proceeds
                grid.cost_basis_usdt = 0.0
                grid.active = False
                grid.orders = []

                cum = _read_cum()
                cum["trades"] = int(cum.get("trades", 0)) + 1
                cum["feesPaidUsdt"] = float(cum.get("feesPaidUsdt", 0.0)) + fee
                cum["grossRealizedPnlUsdt"] = float(cum.get("grossRealizedPnlUsdt", 0.0)) + gross_realized
                stats.trades += 1
                if realized >= 0:
                    cum["wins"] = int(cum.get("wins", 0)) + 1
                    stats.wins += 1
                else:
                    cum["losses"] = int(cum.get("losses", 0)) + 1
                    stats.losses += 1
                    mins = int(state.get("cooldownMinutesAfterLoss", 20))
                    stats.cooldown_until = now + timedelta(minutes=mins)
                _write_cum(cum)
                stats.pnl_usdt = float(cum.get("realizedPnlUsdt", 0.0))

                exit_event = {
                    "tsUtc": _utc_now().isoformat(),
                    "event": "EXIT",
                    "side": "SELL",
                    "reason": "AI_FLATTEN",
                    "type": "PAPER_MARKET",
                    "symbol": symbol,
                    "qtyBtc": qty,
                    "price": effective_exit_price,
                    "quote": "USDT",
                    "notionalUsdt": gross,
                    "feeUsdt": fee,
                    "slippageBps": market_slip_bps,
                    "grossRealizedPnlUsdt": gross_realized,
                    "realizedPnlUsdt": realized,
                    "paper": True,
                }
                _attach_ai_event_fields(exit_event, ai_signal)
                _append_trade(exit_event)
                _log(f"AI_FLATTEN decision={ai_signal.get('decisionId')} price={price:.2f} pnl={realized:.2f}")

        # IMPORTANT: do NOT auto-reinitialize the grid on tiny ATR/spacing drift.
        # That was causing repeated re-buys and corrupt cost basis / unrealized PnL.
        grid_plan = _compute_grid_plan(
            state,
            ai_signal,
            ai_live,
            grid_mode=grid_mode,
            atr=atr,
            price=price,
        )
        spacing_pct = grid_plan["spacing_pct"]
        levels = grid_plan["levels"]
        max_expo = grid_plan["max_expo"]

        if ai_pause_new_buys and (grid is None or not grid.active):
            _write_status(_status_payload(
                state=state,
                symbol=symbol,
                interval=interval,
                price=price,
                paper=paper,
                position_payload=None if paper.btc <= 0 else {
                    "entryPrice": (grid.cost_basis_usdt / paper.btc) if (grid and paper.btc > 0) else None,
                    "qtyBtc": paper.btc,
                    "stop": float((grid.__dict__.get("trail_stop", 0.0) or 0.0)) if grid else None,
                    "tp": None,
                    "entryTimeUtc": grid.last_recenter_utc if grid else None,
                    "unrealizedPnlUsdt": 0.0,
                    "unrealizedPnlPct": 0.0,
                },
                stats_payload=_status_stats_payload(
                    stats=stats,
                    trend_strength=trend_strength,
                    grid_payload=_grid_telemetry(
                        state=state,
                        ai_signal=ai_signal,
                        effective_mode=grid_mode,
                        spacing_pct=spacing_pct,
                        levels=levels,
                        open_orders=len(grid.orders) if grid else 0,
                        skipped=True,
                        skipReason="ai_grid_disallowed",
                    ),
                    ai_signal=ai_signal,
                ),
                last_event="AI_SKIP",
            ))
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
                _write_status(_status_payload(
                    state=state,
                    symbol=symbol,
                    interval=interval,
                    price=price,
                    paper=paper,
                    position_payload=None,
                    stats_payload=_status_stats_payload(
                        stats=stats,
                        trend_strength=trend_strength,
                        grid_payload=_grid_telemetry(
                            state=state,
                            ai_signal=ai_signal,
                            effective_mode=grid_mode,
                            spacing_pct=spacing_pct,
                            levels=levels,
                            open_orders=0,
                            skipped=True,
                            skipReason="spacing_below_fee_floor",
                            requiredMinSpacingPct=min_edge_spacing,
                        ),
                        ai_signal=ai_signal,
                    ),
                    last_event="GRID_SKIP",
                ))
                time.sleep(1)
                continue

            # convert ~50% reserve to BTC so sells are possible
            # IMPORTANT: this is a real buy (even in paper mode) and MUST be journaled,
            # otherwise later accounting views can show sells without matching buys.
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
                _attach_ai_event_fields(enter_event, ai_signal)
                _append_trade(enter_event)
                cum = _read_cum()
                cum["feesPaidUsdt"] = float(cum.get("feesPaidUsdt", 0.0)) + init_fee
                _write_cum(cum)

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
            _attach_ai_event_fields(grid_init_event, ai_signal)
            _append_trade(grid_init_event)

        # Fill logic: if candle crosses order price.
        fee_rate = float(state.get("feeBps", 10)) / 10_000.0
        limit_slip_bps = float(state.get("paperLimitSlipBps", 3.0))

        filled = _select_crossed_grid_orders(
            grid.orders,
            candle_lo=candle_lo,
            candle_hi=candle_hi,
            price=price,
            paper=paper,
            ai_pause_new_buys=ai_pause_new_buys,
            fee_rate=fee_rate,
        )

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
            _attach_ai_event_fields(ev, ai_signal)
            _append_trade(ev)

            cum = _read_cum()
            cum["feesPaidUsdt"] = float(cum.get("feesPaidUsdt", 0.0)) + float(ev.get("feeUsdt") or 0.0)
            if ev.get("event") == "EXIT":
                pnl = float(ev.get("realizedPnlUsdt") or 0.0)
                gross_pnl = float(ev.get("grossRealizedPnlUsdt") or (pnl + float(ev.get("feeUsdt") or 0.0)))
                cum["trades"] = int(cum.get("trades", 0)) + 1
                cum["grossRealizedPnlUsdt"] = float(cum.get("grossRealizedPnlUsdt", 0.0)) + gross_pnl
                stats.trades += 1
                if pnl >= 0:
                    cum["wins"] = int(cum.get("wins", 0)) + 1
                else:
                    cum["losses"] = int(cum.get("losses", 0)) + 1
            _write_cum(cum)
            stats.pnl_usdt = float(cum.get("realizedPnlUsdt", 0.0))

            # place opposite order one level away
            if o.side == "BUY":
                new_px = o.price * (1 + grid.spacing_pct)
                grid.orders.append(GridOrder(side="SELL", price=new_px, qty_btc=o.qty_btc))
            else:
                if not (ai_pause_new_buys or ai_sells_only):
                    new_px = o.price * (1 - grid.spacing_pct)
                    grid.orders.append(GridOrder(side="BUY", price=new_px, qty_btc=o.qty_btc))

        # Update status every tick
        event_rows = _load_trade_events()
        entries_count = sum(1 for ev in event_rows if ev.get("event") == "ENTER")
        exits_count = sum(1 for ev in event_rows if ev.get("event") == "EXIT")
        has_open_position = paper.btc > 0

        status_payload = _status_payload(
            state=state,
            symbol=symbol,
            interval=interval,
            price=price,
            paper=paper,
            position_payload=_position_payload(paper, grid, price),
            stats_payload=_status_stats_payload(
                stats=stats,
                entries_count=entries_count,
                exits_count=exits_count,
                has_open_position=has_open_position,
                trend_strength=trend_strength,
                grid_payload=_grid_telemetry(
                    state=state,
                    ai_signal=ai_signal,
                    effective_mode=grid_mode,
                    spacing_pct=grid.spacing_pct if grid else None,
                    levels=grid.levels if grid else None,
                    open_orders=len(grid.orders) if grid else 0,
                ),
                ai_signal=ai_signal,
            ),
            last_event="TICK",
        )
        _write_status(status_payload)

        runtime_payload = _runtime_payload(
            engine_pid=os.getpid(),
            paper=paper,
            stats=stats,
            entries_count=entries_count,
            exits_count=exits_count,
            has_open_position=has_open_position,
            market_payload=_build_runtime_market_payload(
                kl,
                close,
                price=price,
                candle_hi=candle_hi,
                candle_lo=candle_lo,
            ),
            grid=grid,
            ai_signal=ai_signal,
        )
        _maybe_write_runtime_state(runtime_snapshot_gate, runtime_payload)

        now_monotonic = time.monotonic()
        if _should_emit_heartbeat_log(last_heartbeat_log_monotonic, HEARTBEAT_LOG_SECONDS, now_monotonic=now_monotonic):
            _log(f"HEARTBEAT price={price:.2f} equity={paper.equity(price):.4f} orders={len(grid.orders) if grid else 0}")
            last_heartbeat_log_monotonic = now_monotonic
        time.sleep(1)


if __name__ == "__main__":
    main()
