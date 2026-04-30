import json
import os
import time
from datetime import datetime, timezone

from engine import (
    LOG_PATH,
    AI_SIGNAL_PATH,
    RUNTIME_PATH,
    STATE_PATH,
    _clamp,
    _read_ai_signal,
    _read_json,
    _write_ai_signal,
)
import requests


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] AI_SIDECAR {msg}"
    print(line, flush=True)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line + "\n")


def _build_payload(state: dict, runtime: dict) -> dict | None:
    market = runtime.get("market") or {}
    grid = runtime.get("grid") or {}
    stats = (runtime.get("stats") or {})
    price = market.get("price")
    candle = market.get("candle") or {}

    if price is None:
        # Fallback for older/runtime-light payload shapes.
        try:
            status = _read_json(os.path.join(os.path.dirname(RUNTIME_PATH), "engine_status.json"))
        except Exception:
            status = {}
        price = status.get("price")
        if not candle:
            candle = {
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
        if not stats:
            stats = status.get("stats") or {}
        if not grid:
            grid = ((runtime.get("grid") or {}) if isinstance(runtime.get("grid"), dict) else {})
    if price is None:
        return None
    open_px = candle.get("open") or price
    close_px = candle.get("close") or price
    high_px = candle.get("high") or price
    low_px = candle.get("low") or price
    atr_pct = ((high_px - low_px) / price) if price else 0.0
    trend_strength = abs(close_px - open_px) / price if price else 0.0
    return {
        "symbol": state.get("symbol", "BTCUSDT"),
        "interval": state.get("interval", "1m"),
        "price": price,
        "atrPct": atr_pct,
        "trendStrength": trend_strength,
        "priceChangePct20": ((close_px / open_px) - 1.0) if open_px else 0.0,
        "equityUsdt": (runtime.get("paper") or {}).get("usdt", 0.0) + ((runtime.get("paper") or {}).get("btc", 0.0) * price),
        "usdt": (runtime.get("paper") or {}).get("usdt", 0.0),
        "btc": (runtime.get("paper") or {}).get("btc", 0.0),
        "gridActive": bool(grid.get("active")),
        "gridSpacingPct": grid.get("spacing_pct"),
        "gridLevels": grid.get("levels"),
        "openOrders": len(grid.get("orders") or []),
        "dayTrades": stats.get("trades", 0),
        "maxDrawdownPct": stats.get("max_drawdown_pct", 0.0),
    }


def _extract_signal_result(*, state: dict, payload: dict, provider: str, model: str, parsed: dict) -> dict:
    min_conf = float(state.get("aiMinConfidence", 0.55) or 0.55)
    conf = float(parsed.get("confidence", 0.0) or 0.0)
    model_grid_allowed = bool(parsed.get("gridAllowed", True))
    default_grid_allowed = True
    if conf < min_conf and not model_grid_allowed:
        default_grid_allowed = False
    if payload.get("openOrders", 0) <= 0 and float(payload.get("btc", 0.0) or 0.0) <= 0.0:
        default_grid_allowed = True
    return {
        "enabled": True,
        "provider": provider,
        "model": model,
        "tsUtc": _utc_now(),
        "symbol": payload["symbol"],
        "interval": payload["interval"],
        "regime": parsed.get("regime", "range"),
        "directionBias": parsed.get("directionBias", "neutral"),
        "confidence": conf,
        "breakoutRisk": float(parsed.get("breakoutRisk", 0.0) or 0.0),
        "modelGridAllowed": model_grid_allowed,
        "gridAllowed": default_grid_allowed,
        "recommendedSpacingPct": _clamp(float(parsed.get("recommendedSpacingPct", state.get("gridSpacingPct", 0.008)) or state.get("gridSpacingPct", 0.008)), 0.003, 0.03),
        "recommendedLevels": int(_clamp(float(parsed.get("recommendedLevels", state.get("gridLevels", 12)) or state.get("gridLevels", 12)), 4, 24)),
        "recommendedMaxExposurePct": _clamp(float(parsed.get("recommendedMaxExposurePct", state.get("gridMaxExposurePct", 0.35)) or state.get("gridMaxExposurePct", 0.35)), 0.05, 0.60),
        "recommendedMode": parsed.get("recommendedMode", state.get("gridMode", "scalpy")),
        "note": parsed.get("note", ""),
        "raw": payload,
    }


def _query_one_model(*, state: dict, payload: dict, model: str) -> dict:
    host = state.get("aiBaseUrl") or os.getenv("TRADEBOT_AI_BASE_URL") or "http://127.0.0.1:11435/v1"
    provider = state.get("aiProvider") or "ollama3090"
    timeout_s = float(state.get("aiTimeoutSeconds", 12.0) or 12.0)
    prompt = (
        "You are a grid trading risk controller. Return JSON only with keys: "
        "regime, directionBias, confidence, breakoutRisk, gridAllowed, recommendedSpacingPct, recommendedLevels, recommendedMaxExposurePct, recommendedMode, note. "
        "Keep regime in [range, trend, breakout_risk, high_vol]. Keep directionBias in [bullish, bearish, neutral]. "
        "recommendedMode must be scalpy or fatty. Confidence and breakoutRisk must be 0..1. "
        f"Input market snapshot: {json.dumps(payload, sort_keys=True)}"
    )
    started = time.time()
    _log(f"REQUEST_START model={model} symbol={payload['symbol']} interval={payload['interval']} price={payload['price']}")
    r = requests.post(
        host.rstrip("/") + "/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "Return strict JSON only. No markdown."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 220,
            "stream": False,
        },
        headers={"Authorization": "Bearer local", "Content-Type": "application/json"},
        timeout=timeout_s,
    )
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object in model response")
    parsed = json.loads(content[start:end + 1])
    _log(f"REQUEST_OK model={model} elapsed={time.time() - started:.3f}s")
    return _extract_signal_result(state=state, payload=payload, provider=provider, model=model, parsed=parsed)


def _query_model(state: dict, payload: dict) -> dict:
    primary = state.get("aiModel") or os.getenv("TRADEBOT_AI_MODEL") or "qwen3.6:35b"
    fallback = state.get("aiFallbackModel") or ""
    try:
        return _query_one_model(state=state, payload=payload, model=primary)
    except Exception as e:
        _log(f"REQUEST_ERROR model={primary} error={e}")
        if fallback and fallback != primary:
            try:
                result = _query_one_model(state=state, payload=payload, model=fallback)
                result["fallbackFrom"] = primary
                return result
            except Exception as fallback_error:
                _log(f"REQUEST_ERROR model={fallback} error={fallback_error}")
                raise fallback_error
        raise e


def main() -> None:
    _log("BOOT")
    while True:
        state = _read_json(STATE_PATH)
        poll_s = max(2.0, float(state.get("aiPollSeconds", 15.0) or 15.0))
        if not bool(state.get("aiEnabled", False)):
            _write_ai_signal({"enabled": False, "source": "disabled", "tsUtc": _utc_now()})
            _log("DISABLED")
            time.sleep(2)
            continue

        runtime = _read_json(RUNTIME_PATH)
        payload = _build_payload(state, runtime)
        if not payload:
            _log("NO_PAYLOAD")
            time.sleep(2)
            continue

        try:
            result = _query_model(state, payload)
            _write_ai_signal(result)
            _log(f"SIGNAL_WRITTEN model={result.get('model')} stale={result.get('stale', False)} gridAllowed={result.get('gridAllowed')}")
        except Exception as e:
            fallback = _read_ai_signal() or {}
            fallback.update({
                "enabled": True,
                "provider": state.get("aiProvider") or "ollama3090",
                "model": state.get("aiModel") or "qwen3.6:35b",
                "tsUtc": _utc_now(),
                "error": str(e),
                "stale": True,
            })
            _write_ai_signal(fallback)
            _log(f"REQUEST_ERROR model={fallback.get('model')} error={e}")
        time.sleep(poll_s)


if __name__ == "__main__":
    main()
