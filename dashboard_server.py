import json
import html as html_lib
import hashlib
import math
import os
import subprocess
import time
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from binance_client import BinanceSpotREST
from dashboard_contracts import (
    SCHEMA_VERSION as DASHBOARD_SCHEMA_VERSION,
    validate_chart_tick_payload,
    validate_dashboard_payload as _validate_dashboard_payload,
    validate_market_payload,
)
from dashboard_data import DashboardDataAdapter
from dotenv import load_dotenv
import requests
import sqlite_store

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)

STATUS_PATH = BASE_DIR / "engine_status.json"
STATE_PATH = BASE_DIR / "state.json"
RUNTIME_PATH = BASE_DIR / "runtime_state.json"
CUM_PATH = BASE_DIR / "cumulative.json"
TRADES_PATH = BASE_DIR / "trades.jsonl"
HISTORY_PATH = BASE_DIR / "dashboard_history.json"
INTELLIGENCE_PATH = BASE_DIR / "dashboard_intelligence.json"
AI_SIGNAL_PATH = BASE_DIR / "ai_signal.json"
AI_DECISIONS_PATH = BASE_DIR / "ai_decisions.jsonl"
STATIC_DIR = BASE_DIR / "dashboard" / "static"
GST = timezone(timedelta(hours=4), "GST")
HOST = os.getenv("TRADEBOT_DASHBOARD_HOST", "0.0.0.0")
PORT = int(os.getenv("TRADEBOT_DASHBOARD_PORT", "8844"))
DASHBOARD_TOKEN = os.getenv("TRADEBOT_DASHBOARD_TOKEN", "").strip()
REFRESH_MS = max(1000, int(os.getenv("TRADEBOT_DASHBOARD_REFRESH_MS", "1000")))
DASHBOARD_REFRESH_MS = max(60_000, int(os.getenv("TRADEBOT_DASHBOARD_HEAVY_REFRESH_MS", str(30 * 60 * 1000))))
EVENT_SNAPSHOT_LIMIT = max(50, int(os.getenv("TRADEBOT_DASHBOARD_EVENT_SNAPSHOT_LIMIT", "200")))

_md_clients: dict[str, BinanceSpotREST] = {}
_ohlcv_cache: dict[tuple[str, str, int, int], list[dict]] = {}
_ohlcv_cache_at: dict[tuple[str, str, int, int], float] = {}
_intelligence_lock = threading.Lock()
_intelligence_refreshing = False
SUPPORTED_INTERVALS = {
    "1s": {"binance": "1s", "label": "1 Second", "default_limit": 240},
    "1m": {"binance": "1m", "label": "1 Minute", "default_limit": 180},
    "5m": {"binance": "5m", "label": "5 Minutes", "default_limit": 180},
    "30m": {"binance": "30m", "label": "30 Minutes", "default_limit": 180},
    "1h": {"binance": "1h", "label": "1 Hour", "default_limit": 240},
    "1d": {"binance": "1d", "label": "1 Day", "default_limit": 180},
    "1w": {"binance": "1w", "label": "1 Week", "default_limit": 120},
    "1M": {"binance": "1M", "label": "1 Month", "default_limit": 120},
}
MAX_OHLCV_LIMIT = 1000
INTELLIGENCE_REFRESH_SECONDS = 30 * 60
NEWS_SOURCES = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("The Block", "https://www.theblock.co/rss.xml"),
]
NEWS_CARD_LIMIT = 5
MACRO_CALENDAR_TEMPLATE = [
    {
        "title": "Asia Liquidity Open",
        "hour": 8,
        "minute": 0,
        "impact": 2,
        "color": "#1767c2",
        "upcoming": "Watch Asia liquidity, early dollar tone, and grid spread pressure.",
        "completed": "Asia session set the initial liquidity tone for BTC spread and inventory risk.",
    },
    {
        "title": "Europe Macro / Yields Check",
        "hour": 12,
        "minute": 0,
        "impact": 2,
        "color": "#f7931a",
        "upcoming": "Watch EUR/US yields and risk appetite before the US data window.",
        "completed": "Europe macro flow updated rate-pressure context for the active grid.",
    },
    {
        "title": "US Data Window",
        "hour": 16,
        "minute": 30,
        "impact": 3,
        "color": "#0d8a2f",
        "upcoming": "Watch scheduled US releases and liquidity reaction around the event.",
        "completed": "US data window passed; confirm whether volatility expanded or faded.",
    },
    {
        "title": "US Cash Open / ETF Flow",
        "hour": 17,
        "minute": 30,
        "impact": 3,
        "color": "#d54545",
        "upcoming": "Watch ETF flow, equity beta, and headline reaction during the cash open.",
        "completed": "US cash open flow is in; reassess BTC trend pressure and exposure.",
    },
    {
        "title": "Daily Close Risk Review",
        "hour": 23,
        "minute": 45,
        "impact": 2,
        "color": "#13a7b4",
        "upcoming": "Review realized PnL, open exposure, and overnight grid risk.",
        "completed": "Daily risk review completed; carry only exposure justified by the regime.",
    },
]
AI_ENDPOINTS = [
    {"key": "local", "label": "Local", "provider": "ollama", "baseUrl": "http://127.0.0.1:11434/v1"},
    {"key": "battlestation_gpu", "label": "Battlestation GPU", "provider": "ollama", "baseUrl": "http://192.168.1.20:11435/v1"},
    {"key": "battlestation_cpu", "label": "Battlestation CPU", "provider": "ollama", "baseUrl": "http://192.168.1.20:11436/v1"},
    {"key": "custom", "label": "Custom", "provider": "ollama", "baseUrl": ""},
]
AI_ENDPOINT_BY_KEY = {item["key"]: item for item in AI_ENDPOINTS}
AI_MODEL_CACHE_SECONDS = 30
_ai_model_cache: dict[str, tuple[float, list[str]]] = {}
_sequence_lock = threading.Lock()
_sequences: dict[str, int] = {"dashboard": 0, "status": 0, "chart": 0}
SERVER_INSTANCE_ID = f"{int(time.time() * 1000)}-{os.getpid()}"
DATA_ADAPTER = DashboardDataAdapter()
AI_RESTART_FIELDS = {
    "aiBaseUrl",
    "aiDeepModel",
    "aiEndpointKey",
    "aiFallbackModel",
    "aiModel",
    "aiProvider",
    "aiQuickModel",
}
EDITABLE_STATE_FIELDS = {
    "aiBaseUrl": str,
    "aiDeepModel": str,
    "aiDryRun": bool,
    "aiEnabled": bool,
    "aiEndpointKey": str,
    "aiFallbackModel": str,
    "aiMinConfidence": float,
    "aiModel": str,
    "aiPollSeconds": float,
    "aiProvider": str,
    "aiQuickModel": str,
    "aiShadowMode": bool,
    "aiTemperature": float,
    "aiTimeoutSeconds": float,
    "allowLiveOrders": bool,
    "cooldownMinutesAfterLoss": int,
    "feeBps": int,
    "gridLevels": int,
    "gridMaxExposurePct": float,
    "gridMinPerLevelUsdt": float,
    "gridMinSpacingPctFatty": float,
    "gridMinSpacingPctScalpy": float,
    "gridMode": str,
    "gridSpacingPct": float,
    "gridTrailActive": bool,
    "gridTrailArmAfterAtr": float,
    "gridTrailArmTrendStrength": float,
    "gridTrailAtrMult": float,
    "gridTrailForceExitTrendStrength": float,
    "gridTrailMinNetProfitPct": float,
    "gridTrendEscapeStrength": float,
    "hourlySummary": bool,
    "interval": str,
    "maxDailyLossPct": float,
    "maxTradesPerDay": int,
    "paperStartBtc": float,
    "paperStartUsdt": float,
    "positionCapPct": float,
    "riskPerTradePct": float,
    "symbol": str,
}
STATE_FIELD_BOUNDS = {
    "aiMinConfidence": (0.0, 1.0),
    "aiPollSeconds": (2.0, 3600.0),
    "aiTemperature": (0.0, 2.0),
    "aiTimeoutSeconds": (2.0, 3600.0),
    "cooldownMinutesAfterLoss": (0, 1440),
    "feeBps": (0, 500),
    "gridLevels": (1, 100),
    "gridMaxExposurePct": (0.0, 1.0),
    "gridMinPerLevelUsdt": (0.0, 1_000_000.0),
    "gridMinSpacingPctFatty": (0.0, 1.0),
    "gridMinSpacingPctScalpy": (0.0, 1.0),
    "gridSpacingPct": (0.0, 1.0),
    "gridTrailArmAfterAtr": (0.0, 100.0),
    "gridTrailArmTrendStrength": (0.0, 1.0),
    "gridTrailAtrMult": (0.0, 100.0),
    "gridTrailForceExitTrendStrength": (0.0, 1.0),
    "gridTrailMinNetProfitPct": (0.0, 1.0),
    "gridTrendEscapeStrength": (0.0, 1.0),
    "maxDailyLossPct": (0.0, 1.0),
    "maxTradesPerDay": (0, 10_000),
    "paperStartBtc": (0.0, 1_000_000.0),
    "paperStartUsdt": (0.0, 1_000_000_000.0),
    "positionCapPct": (0.0, 1.0),
    "riskPerTradePct": (0.0, 1.0),
}
STATE_FIELD_CHOICES = {
    "aiEndpointKey": set(AI_ENDPOINT_BY_KEY.keys()),
    "gridMode": {"scalpy", "fatty"},
    "interval": set(SUPPORTED_INTERVALS.keys()),
    "mode": {"paper", "testnet-live"},
}

HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Tradebot Live Dashboard</title>
  <link rel="stylesheet" href="/static/dashboard.v1.css?v=12">
</head>
<body>
<div class="wrap">
  <div class="boot-error" id="boot-error"></div>
  <div class="topbar">
    <div class="brand">
      <div class="btc-mark">B</div>
      <div class="title">
        <h1>BTC Grid Intelligence</h1>
        <p><span class="live-dot"></span> <span id="fresh-label">Waiting for data</span></p>
      </div>
    </div>
    <div class="pillrow">
      <div class="pill">Server Time <span id="server-time">--</span></div>
      <button class="btn" type="button">BTC/USDT</button>
      <button class="btn" type="button" id="top-timeframe">1m</button>
      <button class="icon-btn btn" id="bot-toggle-btn" type="button" title="Pause / play bot">⏸</button>
      <button class="btn" id="ai-toggle-btn" type="button" title="Pause / resume AI assist">AI On</button>
      <button class="theme-switch" id="theme-toggle" type="button" title="Toggle theme">
        <span class="track-icons"><span>L</span><span>D</span></span>
        <span class="knob"></span>
      </button>
      <button class="btn" id="reset-layout-btn" type="button">Reset</button>
    </div>
  </div>

  <div class="dashboard" id="dashboard">
    <section class="card summary" id="summary-card" data-default-col="1" data-default-span="24">
      <div class="card-body">
        <div class="command-strip">
          <div class="trading-state-main">
            <div class="state-eyebrow">Trading State</div>
            <div class="state-value" id="trading-state-label">LIVE / AI-GATED</div>
          </div>
          <div class="sticky-summary" id="sticky-summary"></div>
        </div>
        <div class="control-strip">
          <div class="mini-control"><span class="mini-icon">M</span><div><div class="label">Mode</div><strong id="state-mode">Grid + Local AI</strong></div></div>
          <div class="mini-control"><span class="mini-icon">R</span><div><div class="label">Risk</div><strong id="state-risk" class="positive">Normal</strong></div></div>
          <div class="mini-control"><span class="mini-icon">E</span><div><div class="label">Exposure</div><strong id="state-exposure">--</strong></div></div>
          <div class="mini-control"><span class="mini-icon">N</span><div><div class="label">Next Action</div><strong id="state-action">Wait</strong></div></div>
        </div>
      </div>
    </section>

    <section class="card chart-card" id="market-card" data-default-col="1" data-default-span="16">
      <div class="card-head"><div class="chart-head-group"><h2>BTC/USD · 1H · INDEX</h2><div class="chart-price-pill" id="chart-price-pill"><span class="label">BTC Price</span><span>--</span></div></div><div class="card-actions"><span class="stream-status" id="chart-stream-status">seed</span></div></div>
      <div class="card-body">
        <div class="legend" id="market-legend"></div>
        <div class="pillrow" id="timeframe-controls" style="margin-bottom:10px"></div>
        <div class="candle-details" id="hover-ohlcv"><strong>Candle</strong><span>--</span></div>
        <div class="chart-wrap">
          <canvas id="market-chart"></canvas>
        </div>
      </div>
    </section>

    <section class="card intelligence-card" id="intelligence-card" data-default-col="17" data-default-span="8">
      <div class="card-head"><h2>News & Macro Intelligence</h2><div class="card-actions"><span class="footer-note">View All</span></div></div>
      <div class="card-body"><div class="news-stack" id="news-stack"></div></div>
    </section>

    <section class="card regime-card" id="regime-card" data-default-col="1" data-default-span="16">
      <div class="card-head"><h2>Market Regime Signals</h2><div class="card-actions"><span class="footer-note" id="regime-updated">live</span></div></div>
      <div class="card-body">
        <div class="regime-grid">
          <div class="signal-table" id="signal-table"></div>
          <div class="radar-wrap"><div><div class="footer-note">Regime Radar</div><canvas id="regime-radar"></canvas></div></div>
          <div class="final-regime">
            <div>
              <div class="final-icon">~</div>
              <div class="final-title" id="final-regime-title">Range Consolidation</div>
              <div class="final-copy" id="final-regime-copy">Choppy price action within established range. Maintain grid discipline and capital efficiency.</div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="card macro-card" id="macro-card" data-default-col="17" data-default-span="8">
      <div class="card-head"><h2>Macro Calendar</h2><div class="card-actions"><span class="footer-note">Crypto impact</span></div></div>
      <div class="card-body"><div class="calendar-list" id="macro-calendar"></div></div>
    </section>

    <section class="card events-card" id="events-card" data-default-col="1" data-default-span="12">
      <div class="card-head"><h2>Events</h2><div class="card-actions"><span class="footer-note">latest fills</span></div></div>
      <div class="card-body">
        <div class="pager">
          <div class="pager-controls">
            <button class="btn" id="events-first-btn" type="button">First</button>
            <button class="btn" id="events-prev-btn" type="button">Prev</button>
            <button class="btn" id="events-next-btn" type="button">Next</button>
            <button class="btn" id="events-last-btn" type="button">Last</button>
          </div>
          <div class="page-indicator" id="events-page-indicator">Page 1 / 1</div>
        </div>
        <div class="table-wrap"><table><thead><tr><th>Time</th><th>Event</th><th>Price</th><th>Qty</th></tr></thead><tbody id="events-body"></tbody></table></div>
      </div>
    </section>

    <section class="card ai-decisions-card" id="ai-decisions-card" data-default-col="1" data-default-span="24">
      <div class="card-head"><h2>AI Decisions</h2><div class="card-actions"><span class="footer-note">read-only agent memory</span></div></div>
      <div class="card-body">
        <div class="ai-decision-chat-grid">
          <div class="ai-decision-pane">
            <div class="pager">
              <div class="pager-controls">
                <button class="btn" id="ai-decisions-first-btn" type="button">First</button>
                <button class="btn" id="ai-decisions-prev-btn" type="button">Prev</button>
                <button class="btn" id="ai-decisions-next-btn" type="button">Next</button>
                <button class="btn" id="ai-decisions-last-btn" type="button">Last</button>
              </div>
              <div class="page-indicator" id="ai-decisions-page-indicator">Page 1 / 1</div>
            </div>
            <div class="ai-decision-detail" id="ai-decisions-body"></div>
          </div>
          <div class="agent-chat-pane">
            <div class="agent-chat-toolbar">
              <select id="agent-select" title="Choose which agent report to inspect."></select>
              <select id="agent-thread-select" title="Read-only discussion placeholder."><option>New discussion</option></select>
              <button class="btn" id="agent-configure-btn" type="button" title="Agent configuration is not enabled in this read-only recovery.">Configure Agent</button>
            </div>
            <div class="agent-chat-messages" id="agent-chat-messages"></div>
            <div class="agent-proposals" id="agent-proposals"></div>
            <div class="agent-composer">
              <textarea id="agent-chat-input" rows="3" placeholder="Message the agent..." title="Agent chat is not enabled in this read-only recovery."></textarea>
              <button class="btn" id="agent-chat-send-btn" type="button" title="Agent chat is not enabled in this read-only recovery.">Send</button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="card config-card" id="config-card" data-default-col="13" data-default-span="12">
      <div class="card-head"><h2>Bot Configuration</h2><div class="card-actions"><span class="drag-hint">editable</span></div></div>
      <div class="card-body">
        <div class="config-grid" id="config-form-grid"></div>
        <div class="config-actions">
          <button class="btn" id="config-save-btn" type="button">Save configuration</button>
        </div>
      </div>
    </section>

    <section class="card orders-card" id="orders-card" data-default-col="1" data-default-span="24">
      <div class="card-head"><h2>Orders</h2><div class="card-actions"><span class="footer-note">open grid and trade history</span></div></div>
      <div class="card-body">
        <div class="tabbar">
          <button class="btn active-filter" id="orders-tab-open-btn" type="button">Open Orders</button>
          <button class="btn" id="orders-tab-history-btn" type="button">Trade History</button>
        </div>
        <div class="pager">
          <div class="pager-controls">
            <button class="btn buy-filter" id="orders-filter-buy-btn" type="button">Buy</button>
            <button class="btn sell-filter" id="orders-filter-sell-btn" type="button">Sell</button>
            <button class="btn" id="orders-first-btn" type="button">First</button>
            <button class="btn" id="orders-prev-btn" type="button">Prev</button>
            <button class="btn" id="orders-next-btn" type="button">Next</button>
            <button class="btn" id="orders-last-btn" type="button">Last</button>
          </div>
          <div class="page-indicator" id="orders-page-indicator">Page 1 / 1</div>
        </div>
        <div class="table-wrap"><table><thead><tr><th>Side</th><th>Price</th><th>Vs Market</th><th>Amount</th><th>Total</th><th>Type</th></tr></thead><tbody id="orders-body"></tbody></table></div>
      </div>
    </section>

    <section class="card status-footer" id="status-card" data-default-col="1" data-default-span="24">
      <div class="card-head"><h2>Status Footer</h2><div class="card-actions"><span class="footer-note">runtime and bot state</span></div></div>
      <div class="card-body"><div class="kv-grid" id="status-list"></div></div>
    </section>
  </div>
</div>
<script src="/static/dashboard.v1.js?v=12"></script>
</body>
</html>'''


def read_json(path: Path) -> dict:
    return DATA_ADAPTER.read_json(path)


def write_json(path: Path, payload: dict) -> None:
    DATA_ADAPTER.write_json(path, payload)


def update_state_locked(updater) -> dict:
    return DATA_ADAPTER.update_state(STATE_PATH, updater)


def next_sequence(channel: str) -> int:
    with _sequence_lock:
        _sequences[channel] = int(_sequences.get(channel, 0)) + 1
        return _sequences[channel]


def format_gst_datetime(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    gst_now = current.astimezone(GST)
    hour = gst_now.hour % 12 or 12
    ampm = "AM" if gst_now.hour < 12 else "PM"
    return f"{gst_now:%b} {gst_now.day} {gst_now.year}, {hour}:{gst_now:%M:%S} {ampm} GST"


def _state_ai_models(state: dict) -> list[str]:
    models = []
    for key in ("aiModel", "aiQuickModel", "aiDeepModel", "aiFallbackModel"):
        value = str(state.get(key) or "").strip()
        if value and value not in models:
            models.append(value)
    return models


def _stop_local_ollama_models(state: dict) -> None:
    endpoint = active_ai_endpoint(state)
    base_url = _normalize_ai_base_url(endpoint.get("baseUrl") or state.get("aiBaseUrl") or "")
    if "127.0.0.1:" not in base_url and "localhost:" not in base_url:
        return
    for model in _state_ai_models(state):
        try:
            subprocess.run(["ollama", "stop", model], cwd=str(BASE_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            continue


def _sync_ai_sidecar_for_state(state: dict, patch: dict) -> None:
    if "aiEnabled" in patch and patch["aiEnabled"] is False:
        try:
            subprocess.run([str(BASE_DIR / ".venv" / "bin" / "python"), str(BASE_DIR / "run_ai_sidecar_detached.py"), "stop"], cwd=str(BASE_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass
        write_json(AI_SIGNAL_PATH, {"enabled": False, "source": "disabled", "tsUtc": datetime.now(timezone.utc).isoformat()})
        _stop_local_ollama_models(state)
        return
    if patch.get("aiEnabled") is True:
        try:
            subprocess.run([str(BASE_DIR / ".venv" / "bin" / "python"), str(BASE_DIR / "run_ai_sidecar_detached.py"), "start"], cwd=str(BASE_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass
        return
    if state.get("aiEnabled", False) and any(key in patch for key in AI_RESTART_FIELDS):
        try:
            subprocess.run([str(BASE_DIR / ".venv" / "bin" / "python"), str(BASE_DIR / "run_ai_sidecar_detached.py"), "restart"], cwd=str(BASE_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass


def coerce_state_patch(body: dict) -> dict:
    patch = {}
    for key, caster in EDITABLE_STATE_FIELDS.items():
        if key not in body:
            continue
        raw = body[key]
        if caster is bool:
            value = str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}
        elif caster is int:
            value = int(float(raw))
        elif caster is float:
            value = float(raw)
        elif caster is str:
            value = str(raw).strip()
        else:
            continue
        if key in STATE_FIELD_BOUNDS:
            lo, hi = STATE_FIELD_BOUNDS[key]
            if value < lo or value > hi:
                raise ValueError(f'{key} outside allowed range {lo}..{hi}')
        if key in STATE_FIELD_CHOICES and value not in STATE_FIELD_CHOICES[key]:
            allowed = ', '.join(sorted(STATE_FIELD_CHOICES[key]))
            raise ValueError(f'{key} must be one of: {allowed}')
        patch[key] = value
    endpoint_key = patch.get("aiEndpointKey")
    if endpoint_key:
        endpoint = AI_ENDPOINT_BY_KEY.get(endpoint_key)
        if endpoint and endpoint_key != "custom":
            patch["aiProvider"] = endpoint["provider"]
            patch["aiBaseUrl"] = endpoint["baseUrl"]
        elif endpoint_key == "custom" and not patch.get("aiProvider"):
            patch["aiProvider"] = "ollama"
    elif "aiBaseUrl" in patch:
        patch["aiEndpointKey"] = infer_ai_endpoint_key({"aiBaseUrl": patch["aiBaseUrl"]})
    return patch


def _normalize_ai_base_url(base_url: str) -> str:
    return str(base_url or "").strip().rstrip("/")


def infer_ai_endpoint_key(state: dict) -> str:
    selected = state.get("aiEndpointKey")
    if selected in AI_ENDPOINT_BY_KEY:
        if selected != "custom":
            selected_url = _normalize_ai_base_url(AI_ENDPOINT_BY_KEY[selected]["baseUrl"])
            state_url = _normalize_ai_base_url(state.get("aiBaseUrl") or "")
            if state_url and state_url != selected_url:
                return "custom"
        return str(selected)
    base_url = _normalize_ai_base_url(state.get("aiBaseUrl") or os.getenv("TRADEBOT_AI_BASE_URL") or "")
    for endpoint in AI_ENDPOINTS:
        if endpoint["key"] == "custom":
            continue
        if _normalize_ai_base_url(endpoint["baseUrl"]) == base_url:
            return endpoint["key"]
    return "custom"


def active_ai_endpoint(state: dict) -> dict:
    key = infer_ai_endpoint_key(state)
    endpoint = dict(AI_ENDPOINT_BY_KEY.get(key) or AI_ENDPOINT_BY_KEY["custom"])
    if key == "custom":
        endpoint["baseUrl"] = _normalize_ai_base_url(state.get("aiBaseUrl") or os.getenv("TRADEBOT_AI_BASE_URL") or "")
        endpoint["provider"] = str(state.get("aiProvider") or endpoint.get("provider") or "ollama")
    return endpoint


def _ai_model_candidates(base_url: str) -> list[str]:
    base_url = _normalize_ai_base_url(base_url)
    if not base_url:
        return []
    candidates = [f"{base_url}/models"]
    if base_url.endswith('/v1'):
        candidates.append(f"{base_url[:-3]}/api/tags")
    else:
        candidates.append(f"{base_url}/v1/models")
        candidates.append(f"{base_url}/api/tags")
    return candidates


def _extract_ai_model_names(payload: dict) -> list[str]:
    items = payload.get('data') or payload.get('models') or []
    names = []
    for item in items:
        if isinstance(item, dict):
            name = item.get('id') or item.get('name') or item.get('model')
            if name:
                names.append(str(name))
        elif item:
            names.append(str(item))
    seen = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def fetch_ai_models(base_url: str, *, force: bool = False) -> list[str]:
    base_url = _normalize_ai_base_url(base_url)
    if not base_url:
        return []
    now = time.time()
    cached = _ai_model_cache.get(base_url)
    if cached and not force and now - cached[0] < AI_MODEL_CACHE_SECONDS:
        return cached[1]
    models: list[str] = []
    for url in _ai_model_candidates(base_url):
        try:
            resp = requests.get(url, timeout=(0.35, 0.8))
            resp.raise_for_status()
            models = _extract_ai_model_names(resp.json())
            break
        except Exception:
            continue
    _ai_model_cache[base_url] = (now, models)
    return models


def get_ai_endpoint_models(state: dict) -> dict[str, list[str]]:
    models: dict[str, list[str]] = {}
    for endpoint in AI_ENDPOINTS:
        if endpoint["key"] == "custom":
            base_url = _normalize_ai_base_url(state.get("aiBaseUrl") or "")
            models[endpoint["key"]] = fetch_ai_models(base_url) if infer_ai_endpoint_key(state) == "custom" else []
        else:
            models[endpoint["key"]] = fetch_ai_models(endpoint["baseUrl"])
    return models


def get_ai_models(state: dict) -> list[str]:
    endpoint = active_ai_endpoint(state)
    return fetch_ai_models(endpoint.get("baseUrl", ""))


def ai_endpoint_payload(state: dict) -> tuple[dict, dict[str, list[str]]]:
    endpoint = active_ai_endpoint(state)
    models_by_endpoint = get_ai_endpoint_models(state)
    return endpoint, models_by_endpoint


def _safe_num(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        return default
    return number if math.isfinite(number) else default


def _pct_delta(now, before) -> float | None:
    before_num = _safe_num(before, 0.0)
    if before_num == 0:
        return None
    return (_safe_num(now, before_num) / before_num) - 1.0


def _safe_text(value, *, max_chars: int = 800) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_chars]


def _safe_text_list(value, *, limit: int = 4, max_chars: int = 300) -> list[str]:
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    items: list[str] = []
    for item in raw_items:
        text = _safe_text(item, max_chars=max_chars)
        if text:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _agent_report_summary(role: str, report: dict) -> dict:
    report = report or {}
    raw = report.get("raw") if isinstance(report.get("raw"), dict) else {}
    evidence = report.get("evidence") or raw.get("evidence") or []
    if isinstance(evidence, dict):
        evidence_items = [f"{key}: {value}" for key, value in evidence.items()]
    elif isinstance(evidence, list):
        evidence_items = evidence
    elif evidence:
        evidence_items = [evidence]
    else:  # pragma: no cover - evidence is normalized to [] before this defensive fallback.
        evidence_items = []
    return {
        "role": _safe_text(role, max_chars=80) or "",
        "recommendation": _safe_text(
            report.get("recommendation") or raw.get("recommendation") or "--",
            max_chars=120,
        ),
        "riskScore": report.get("risk_score", raw.get("riskScore")),
        "confidence": report.get("confidence", raw.get("confidence")),
        "summary": _safe_text(report.get("summary") or raw.get("summary") or "", max_chars=500) or "",
        "evidence": _safe_text_list(evidence_items, limit=4, max_chars=300),
        "latencySeconds": raw.get("_latencySeconds"),
    }


def _projected_impact(decision: dict) -> str:
    action = str(decision.get("riskAction") or "hold")
    spacing = _safe_num(decision.get("recommendedSpacingPct"), 0.0) * 100.0
    exposure = _safe_num(decision.get("recommendedMaxExposurePct"), 0.0) * 100.0
    levels = int(_safe_num(decision.get("recommendedLevels"), 0))
    if action == "allow_grid":
        return f"Keep grid active with {levels} levels, {spacing:.2f}% spacing, max exposure {exposure:.1f}%."
    if action == "pause_new_buys":
        return "Stop opening new buy levels while existing sells can still work out of inventory."
    if action == "sells_only":
        return "Let sell-side exits continue and block new accumulation until risk cools."
    if action == "reduce_exposure":
        return f"Lower exposure budget toward {exposure:.1f}% and avoid adding inventory."
    if action == "flatten":
        return "Recommend exiting the open paper position if confidence clears the flatten threshold."
    return "Hold current deterministic grid settings unless risk gates say otherwise."


def _realized_impact(decision: dict, status: dict, cumulative: dict) -> dict:
    raw = decision.get("raw") if isinstance(decision.get("raw"), dict) else {}
    start_equity = raw.get("equityUsdt") if raw else decision.get("equityUsdt")
    start_price = raw.get("price") if raw else decision.get("price")
    start_trades = raw.get("closedTrades") if raw else decision.get("closedTrades")
    now_equity = status.get("equityUsdt")
    now_price = status.get("price")
    trades_now = cumulative.get("trades")
    equity_delta = None
    if start_equity is not None and now_equity is not None:
        equity_delta = _safe_num(now_equity) - _safe_num(start_equity)
    trade_delta = None
    if start_trades is not None and trades_now is not None:
        trade_delta = int(_safe_num(trades_now)) - int(_safe_num(start_trades))
    return {
        "equityDeltaUsdt": equity_delta,
        "equityDeltaPct": _pct_delta(now_equity, start_equity) if equity_delta is not None else None,
        "priceDeltaPct": _pct_delta(now_price, start_price)
        if start_price is not None and now_price is not None
        else None,
        "tradeDelta": trade_delta,
        "currentEquityUsdt": now_equity,
        "startEquityUsdt": start_equity,
    }


def _strategy_profile_summary(value: dict | None) -> dict | None:
    if not isinstance(value, dict):
        return None
    summary = {
        "name": _safe_text(value.get("name"), max_chars=160),
        "mode": _safe_text(value.get("mode"), max_chars=80),
        "spacingPct": value.get("spacingPct"),
        "levels": value.get("levels"),
        "maxExposurePct": value.get("maxExposurePct"),
        "perLevelUsdt": value.get("perLevelUsdt"),
    }
    return {key: val for key, val in summary.items() if val is not None} or None


def _validation_report_summary(value: dict | None) -> dict | None:
    if not isinstance(value, dict):
        return None
    summary = {
        "passed": value.get("passed"),
        "mode": _safe_text(value.get("mode"), max_chars=80),
        "sampleCount": value.get("sampleCount"),
        "error": _safe_text(value.get("error"), max_chars=500),
    }
    return {key: val for key, val in summary.items() if val is not None} or None


def _normalized_ai_decision_row(item: dict, status: dict, cumulative: dict) -> dict | None:
    decision = item.get("decision") if isinstance(item.get("decision"), dict) else item
    if not isinstance(decision, dict):
        return None
    useful_keys = {
        "decisionId",
        "tsUtc",
        "model",
        "riskAction",
        "confidence",
        "recommendedMode",
        "reports",
        "note",
        "error",
        "keyRisks",
    }
    if not useful_keys.intersection(decision.keys()) and not useful_keys.intersection(item.keys()):
        return None
    reports = decision.get("reports") or item.get("reports") or {}
    if not isinstance(reports, dict):
        reports = {}
    profile = decision.get("strategyProfile")
    validation = decision.get("validationReport")
    return {
        "decisionId": _safe_text(decision.get("decisionId") or item.get("decisionId"), max_chars=120),
        "tsUtc": _safe_text(decision.get("tsUtc") or item.get("tsUtc"), max_chars=80),
        "model": _safe_text(decision.get("model") or item.get("model"), max_chars=120),
        "source": _safe_text(decision.get("source"), max_chars=80),
        "riskAction": _safe_text(decision.get("riskAction"), max_chars=80),
        "gridAllowed": decision.get("gridAllowed"),
        "pauseNewBuys": decision.get("pauseNewBuys"),
        "allowSellsOnly": decision.get("allowSellsOnly"),
        "flattenRecommended": decision.get("flattenRecommended"),
        "reduceExposure": decision.get("reduceExposure"),
        "confidence": decision.get("confidence"),
        "regime": _safe_text(decision.get("regime"), max_chars=120),
        "directionBias": _safe_text(decision.get("directionBias"), max_chars=120),
        "recommendedMode": _safe_text(decision.get("recommendedMode"), max_chars=80),
        "recommendedSpacingPct": decision.get("recommendedSpacingPct"),
        "recommendedLevels": decision.get("recommendedLevels"),
        "recommendedMaxExposurePct": decision.get("recommendedMaxExposurePct"),
        "strategyProfileId": _safe_text(decision.get("strategyProfileId"), max_chars=120),
        "strategyProfileName": _safe_text(decision.get("strategyProfileName"), max_chars=160),
        "strategyProfileStatus": _safe_text(decision.get("strategyProfileStatus"), max_chars=80),
        "strategyProfile": _strategy_profile_summary(profile),
        "researchSnapshotId": _safe_text(decision.get("researchSnapshotId"), max_chars=120),
        "validationReport": _validation_report_summary(validation),
        "dryRun": decision.get("dryRun"),
        "shadowMode": decision.get("shadowMode"),
        "stale": decision.get("stale"),
        "latencySeconds": decision.get("latencySeconds") or item.get("latencySeconds"),
        "note": _safe_text(decision.get("note"), max_chars=1200),
        "keyRisks": _safe_text_list(decision.get("keyRisks"), limit=4, max_chars=300),
        "projectedImpact": _projected_impact(decision),
        "realizedImpact": _realized_impact(decision, status, cumulative),
        "agents": [_agent_report_summary(role, report) for role, report in reports.items() if isinstance(report, dict)],
        "error": _safe_text(decision.get("error"), max_chars=500),
    }


def read_ai_decisions(status: dict | None = None, cumulative: dict | None = None, limit: int = 30) -> list[dict]:
    try:
        safe_limit = max(0, min(50, int(limit)))
    except Exception:
        safe_limit = 30
    if safe_limit <= 0 or not AI_DECISIONS_PATH.exists() or not AI_DECISIONS_PATH.is_file():
        return []
    status = status or {}
    cumulative = cumulative or {}
    try:
        lines = AI_DECISIONS_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    rows: list[dict] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        row = _normalized_ai_decision_row(item, status, cumulative)
        if row is None:
            continue
        rows.append(row)
        if len(rows) >= safe_limit:
            break
    return rows


def validate_dashboard_payload(payload: dict) -> dict:
    if "aiDecisions" not in payload:
        payload = dict(payload)
        payload["aiDecisions"] = read_ai_decisions(payload.get("status") or {}, payload.get("cumulative") or {})
    return _validate_dashboard_payload(payload)


def read_events() -> list[dict]:
    return DATA_ADAPTER.read_events(TRADES_PATH, limit=EVENT_SNAPSHOT_LIMIT)


def event_cursor(events: list[dict]) -> int:
    cursor = 0
    for event in events or []:
        try:
            cursor = max(cursor, int(event.get("_eventId") or event.get("eventId") or 0))
        except Exception:
            continue
    return cursor


def build_event_patch(events: list[dict], last_cursor: int, *, snapshot: bool = False) -> dict:
    cursor = event_cursor(events)
    if snapshot:
        return {"mode": "snapshot", "cursor": cursor, "items": events or []}
    items: list[dict] = []
    for event in events or []:
        try:
            event_id = int(event.get("_eventId") or event.get("eventId") or 0)
        except Exception:
            event_id = 0
        if event_id > last_cursor:
            items.append(event)
    return {"mode": "delta", "cursor": cursor, "items": items}


def order_patch_key(order: dict) -> str:
    body = {
        "side": order.get("side"),
        "price": order.get("price"),
        "qty_btc": order.get("qty_btc") or order.get("qtyBtc"),
        "total": order.get("total") or order.get("notionalUsdt"),
        "type": order.get("type"),
    }
    explicit = order.get("id") or order.get("orderId") or order.get("clientOrderId")
    if explicit:
        body["id"] = explicit
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def normalize_order_patch_items(orders: list[dict]) -> list[dict]:
    items = []
    for order in orders or []:
        item = dict(order)
        item["_orderKey"] = item.get("_orderKey") or order_patch_key(item)
        items.append(item)
    return items


def order_signature(orders: list[dict]) -> str:
    items = normalize_order_patch_items(orders)
    raw = json.dumps(sorted(items, key=lambda item: str(item.get("_orderKey"))), sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build_order_patch(orders: list[dict], previous: dict[str, dict] | None, *, snapshot: bool = False) -> tuple[dict, dict[str, dict]]:
    items = normalize_order_patch_items(orders)
    current = {str(item["_orderKey"]): item for item in items}
    signature = order_signature(items)
    if snapshot or previous is None:
        return {"mode": "snapshot", "signature": signature, "items": items, "ops": []}, current
    ops = []
    for key, item in current.items():
        if previous.get(key) != item:
            ops.append({"op": "upsert", "key": key, "item": item})
    for key in previous:
        if key not in current:
            ops.append({"op": "remove", "key": key})
    return {"mode": "delta", "signature": signature, "items": [], "ops": ops}, current


def strip_orders_from_runtime(runtime: dict) -> dict:
    payload = dict(runtime or {})
    grid = dict(payload.get("grid") or {})
    grid.pop("orders", None)
    payload["grid"] = grid
    return payload


def _backfill_history_from_logs(items: deque) -> None:
    if len(items) >= 180:
        return
    log_path = BASE_DIR / "engine.log"
    if not log_path.exists():
        return
    existing_ts = {item.get("ts") for item in items}
    price_by_ts: dict[str, float] = {}
    grid_re = re.compile(r"^\[(.*?) UTC\] GRID_INIT .* anchor=([0-9.]+)$")
    trail_re = re.compile(r"^\[(.*?) UTC\] GRID_TRAIL_STOP hit price=([0-9.]+)")
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]
    for line in lines:
        m = grid_re.match(line)
        if m:
            ts = m.group(1).replace(' ', 'T') + '+00:00'
            price_by_ts[ts] = float(m.group(2))
            continue
        m = trail_re.match(line)
        if m:
            ts = m.group(1).replace(' ', 'T') + '+00:00'
            price_by_ts[ts] = float(m.group(2))
    for ts in sorted(price_by_ts.keys()):
        if ts in existing_ts:
            continue
        items.append({"ts": ts, "price": price_by_ts[ts], "equity": 500.0})


def update_history(status: dict) -> dict:
    history = sqlite_store.read_history(limit=5000)
    if not history.get("items") and HISTORY_PATH.exists():
        history = read_json(HISTORY_PATH) or {}
        sqlite_store.import_history_items(history.get("items", []))
    items = deque(history.get("items", []), maxlen=5000)
    _backfill_history_from_logs(items)
    price = status.get("price")
    equity = status.get("equityUsdt")
    ts = status.get("tsUtc")
    if isinstance(price, (int, float)) and isinstance(equity, (int, float)) and ts:
        if not items or items[-1].get("ts") != ts:
            items.append({"ts": ts, "price": price, "equity": equity})
        sqlite_store.upsert_history_item(ts, price, equity)
    payload = {"items": list(items)}
    sqlite_store.import_history_items(payload["items"])
    HISTORY_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def freshness_seconds(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
    except Exception:
        return None


def normalize_interval(interval: str | None) -> str:
    if not interval:
        return "1m"
    return interval if interval in SUPPORTED_INTERVALS else "1m"


def get_md_client(interval: str = "1m"):
    interval = normalize_interval(interval)
    base_url = os.getenv("BINANCE_MARKETDATA_URL", "https://api.binance.com")
    if interval == "1s":
        base_url = os.getenv("BINANCE_MARKETDATA_1S_URL", base_url)
    if interval not in _md_clients:
        _md_clients[interval] = BinanceSpotREST(
            base_url=base_url,
            api_key=os.getenv("BINANCE_API_KEY", "x"),
            api_secret=os.getenv("BINANCE_API_SECRET", "y"),
        )
    return _md_clients[interval]


def merge_live_price_into_ohlcv(rows: list[dict], live_price: float | None) -> list[dict]:
    payload = [dict(row) for row in (rows or [])]
    if not payload or live_price is None:
        return payload
    try:
        last = payload[-1]
        last['close'] = float(live_price)
        last['high'] = max(float(last.get('high', live_price) or live_price), float(live_price))
        last['low'] = min(float(last.get('low', live_price) or live_price), float(live_price))
    except Exception:
        return payload
    return payload


def latest_ohlcv_price(rows: list[dict]) -> float | None:
    if not rows:
        return None
    try:
        return float(rows[-1].get("close"))
    except Exception:
        return None


def apply_market_price_to_status(status: dict, market_price: float | None) -> dict:
    payload = dict(status or {})
    if market_price is None:
        return payload
    try:
        price = float(market_price)
    except Exception:
        return payload
    payload["price"] = price
    payload["marketDataTsUtc"] = datetime.now(timezone.utc).isoformat()
    try:
        usdt = float(payload.get("usdt") or 0.0)
        btc = float(payload.get("btc") or 0.0)
        payload["equityUsdt"] = usdt + (btc * price)
    except Exception:
        pass
    position = dict(payload.get("position") or {})
    try:
        qty = float(position.get("qtyBtc") or payload.get("btc") or 0.0)
        entry = float(position.get("entryPrice") or 0.0)
        if qty and entry:
            unrealized = (price - entry) * qty
            position["unrealizedPnlUsdt"] = unrealized
            position["unrealizedPnlPct"] = (price / entry) - 1.0
            payload["position"] = position
    except Exception:
        payload["position"] = position
    return payload


def interval_duration_ms(interval: str) -> int:
    interval = normalize_interval(interval)
    return {
        "1s": 1_000,
        "1m": 60_000,
        "5m": 5 * 60_000,
        "30m": 30 * 60_000,
        "1h": 60 * 60_000,
        "1d": 24 * 60 * 60_000,
        "1w": 7 * 24 * 60 * 60_000,
        "1M": 31 * 24 * 60 * 60_000,
    }[interval]


def _iso_to_ms(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.astimezone(timezone.utc).timestamp() * 1000)
    except Exception:
        return None


def apply_status_price_to_ohlcv(ohlcv: list[dict], status: dict, interval: str) -> list[dict]:
    rows = [dict(row) for row in (ohlcv or [])]
    if not rows:
        return rows
    status_age = freshness_seconds(status.get("tsUtc"))
    if status_age is None or status_age > max(10.0, (REFRESH_MS / 1000.0) * 3.0):
        return rows
    try:
        price = float(status.get("price"))
    except Exception:
        return rows
    if not price:
        return rows
    duration = interval_duration_ms(interval)
    ts_ms = _iso_to_ms(status.get("tsUtc")) or int(datetime.now(timezone.utc).timestamp() * 1000)
    open_ms = (ts_ms // duration) * duration
    close_ms = open_ms + duration - 1
    last = dict(rows[-1])
    last_open = int(last.get("openTimeMs") or 0)
    symbol = status.get("symbol") or last.get("symbol") or "BTCUSDT"
    normalized_interval = normalize_interval(interval)
    if open_ms < last_open:
        return rows
    if open_ms == last_open:
        last["close"] = price
        last["high"] = max(float(last.get("high") or price), price)
        last["low"] = min(float(last.get("low") or price), price)
        last["closeTimeMs"] = int(last.get("closeTimeMs") or close_ms)
        rows[-1] = last
        return rows
    rows.append({
        "openTimeMs": open_ms,
        "open": float(last.get("close") or price),
        "high": max(float(last.get("close") or price), price),
        "low": min(float(last.get("close") or price), price),
        "close": price,
        "volumeBase": 0.0,
        "closeTimeMs": close_ms,
        "volumeUsdt": 0.0,
        "symbol": symbol,
        "interval": normalized_interval,
    })
    return rows[-MAX_OHLCV_LIMIT:]


def get_ohlcv(symbol: str, interval: str, limit: int = 120, offset: int = 0) -> list[dict]:
    interval = normalize_interval(interval)
    limit = max(30, min(MAX_OHLCV_LIMIT, int(limit)))
    offset = max(0, int(offset))
    key = (symbol, interval, limit, offset)
    ttl = 0.5 if interval == "1s" else (20 if interval == "1m" else 300)
    if key in _ohlcv_cache and (time.time() - _ohlcv_cache_at.get(key, 0.0)) < ttl:
        return _ohlcv_cache[key]
    try:
        binance_interval = SUPPORTED_INTERVALS[interval]["binance"]
        fetch_limit = min(MAX_OHLCV_LIMIT, limit + offset)
        rows = get_md_client(interval).klines(symbol=symbol, interval=binance_interval, limit=fetch_limit)
        if offset:
            rows = rows[:-offset] if offset < len(rows) else []
        rows = rows[-limit:]
        payload = [{
            "openTimeMs": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volumeBase": float(r[5]),
            "closeTimeMs": int(r[6]),
            "volumeUsdt": float(r[7]),
            "symbol": symbol,
            "interval": interval,
        } for r in rows]
        _ohlcv_cache[key] = payload
        _ohlcv_cache_at[key] = time.time()
        return payload
    except Exception:
        return _ohlcv_cache.get(key, [])


def _fmt_num(value, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return "--"


def _fmt_money(value) -> str:
    return f"${_fmt_num(value, 2)}"


def _fmt_pct(value) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return "--"


def _signed_class(value) -> str:
    try:
        value = float(value)
    except Exception:
        return ""
    return "positive" if value > 0 else ("negative" if value < 0 else "")


def dashboard_mode_label(state: dict) -> str:
    ai_enabled = state.get("aiEnabled", True) is not False
    suffix = "Local AI" if ai_enabled else "Rules"
    mode = str(state.get("gridMode") or "").strip().lower()
    if mode in {"scalpy", "fatty"}:
        return f"{mode.capitalize()} + {suffix}"
    if mode in {"flexy", "ai_optimized"}:
        return "Optimized AI" if ai_enabled else "Rules"
    return f"Grid + {suffix}"


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _parse_json_object(text: str) -> dict:
    if not text:
        raise ValueError("empty local AI response")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in local AI response")
    return json.loads(text[start:end + 1])


def _fetch_news_items(limit: int = 12) -> list[dict]:
    items: list[dict] = []
    for source, url in NEWS_SOURCES:
        try:
            resp = requests.get(url, timeout=12, headers={"User-Agent": "tradebot-dashboard/1.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for node in root.findall(".//item")[:8]:
                title = _strip_html(node.findtext("title") or "")
                link = (node.findtext("link") or "").strip()
                desc = _strip_html(node.findtext("description") or "")
                pub_raw = node.findtext("pubDate") or ""
                published = ""
                sort_ts = 0.0
                try:
                    dt = parsedate_to_datetime(pub_raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    dt = dt.astimezone(timezone.utc)
                    published = dt.isoformat()
                    sort_ts = dt.timestamp()
                except Exception:
                    published = pub_raw
                if not title:
                    continue
                haystack = f"{title} {desc}".lower()
                score = 0
                for word in ("bitcoin", "btc", "crypto", "etf", "fed", "inflation", "dollar", "rates", "liquidity", "macro"):
                    if word in haystack:
                        score += 1
                items.append({
                    "source": source,
                    "title": title[:180],
                    "url": link,
                    "summary": desc[:260],
                    "publishedUtc": published,
                    "sortTs": sort_ts,
                    "score": score,
                })
        except Exception:
            continue
    deduped: list[dict] = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda x: (x.get("score", 0), x.get("sortTs", 0)), reverse=True):
        key = item["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    for item in deduped:
        item.pop("sortTs", None)
        item.pop("score", None)
    return deduped


def _deterministic_regime_rows(status: dict, ohlcv: list[dict], intelligence_error: str = "") -> tuple[list[dict], dict]:
    first, last = (ohlcv[0] if ohlcv else None), (ohlcv[-1] if ohlcv else None)
    change = (float(last.get("close") or 0) / float(first.get("open") or 1) - 1) if first and last else 0.0
    ranges = [float(c.get("high") or 0) - float(c.get("low") or 0) for c in (ohlcv or [])]
    avg_range = sum(ranges) / len(ranges) if ranges else 0.0
    price = float(status.get("price") or (last or {}).get("close") or 0.0)
    vol_pct = avg_range / price if price > 0 else 0.0
    equity = float(status.get("equityUsdt") or 0.0)
    exposure = (float(status.get("btc") or 0.0) * price / equity) if equity > 0 else 0.0
    trend_status = "Range" if abs(change) < 0.015 else ("Uptrend" if change > 0 else "Downtrend")
    rows = [
        {"signal": "Trend", "status": trend_status, "score": 0.68 if trend_status == "Range" else 0.78, "note": "Price oscillating inside range" if trend_status == "Range" else f"{change * 100:.2f}% move over visible candles", "tone": "blue"},
        {"signal": "Liquidity", "status": "Improving" if exposure < 0.55 else "Tight", "score": 0.74 if exposure < 0.55 else 0.52, "note": "Capital available for grid" if exposure < 0.55 else "Exposure using more capital", "tone": "green" if exposure < 0.55 else "orange"},
        {"signal": "Volatility", "status": "Elevated" if vol_pct > 0.012 else "Calm", "score": 0.62 if vol_pct > 0.012 else 0.48, "note": "ATR proxy from live candles", "tone": "orange"},
        {"signal": "ETF Demand", "status": "Watch", "score": 0.60, "note": "News flow refreshed every 30 minutes", "tone": "green"},
        {"signal": "Macro Pressure", "status": "Tight", "score": 0.58, "note": "Rates and USD sensitivity", "tone": "orange"},
        {"signal": "Execution Quality", "status": "Watch" if intelligence_error else "Good", "score": 0.45 if intelligence_error else 0.78, "note": intelligence_error[:80] if intelligence_error else "Local checks healthy", "tone": "orange" if intelligence_error else "green"},
    ]
    final = {
        "title": "Range Consolidation" if trend_status == "Range" else trend_status,
        "copy": "Choppy price action within established range. Maintain grid discipline and capital efficiency."
        if trend_status == "Range"
        else "Directional pressure is rising. Keep exits fee-aware and let AI gating manage exposure.",
    }
    return rows, final


def _ai_config_for_dashboard(state: dict) -> tuple[str, str, float]:
    base_url = str(state.get("aiBaseUrl") or os.getenv("TRADEBOT_AI_BASE_URL") or "http://127.0.0.1:11434/v1").rstrip("/")
    model = str(state.get("aiModel") or state.get("aiQuickModel") or os.getenv("TRADEBOT_AI_MODEL") or "local")
    timeout = min(120.0, max(10.0, float(state.get("aiTimeoutSeconds") or 60.0)))
    return base_url, model, timeout


def _local_ai_assess_intelligence(state: dict, status: dict, ohlcv: list[dict], news_items: list[dict]) -> dict:
    base_url, model, timeout = _ai_config_for_dashboard(state)
    prompt_payload = {
        "task": "Assess Bitcoin/crypto news and market regime for a grid trading dashboard. Return strict JSON only.",
        "schema": {
            "newsCards": [{"title": "string", "source": "string", "age": "string", "sentiment": "Bullish|Neutral|Bearish", "impact": "1-8 integer"}],
            "regimeSignals": [{"signal": "Trend|Liquidity|Volatility|ETF Demand|Macro Pressure|Execution Quality", "status": "string", "score": "0-1 number", "note": "string", "tone": "green|orange|blue"}],
            "finalRegime": {"title": "string", "copy": "string"},
        },
        "market": {
            "symbol": status.get("symbol", "BTCUSDT"),
            "price": status.get("price"),
            "equityUsdt": status.get("equityUsdt"),
            "position": status.get("position"),
            "recentCandles": (ohlcv or [])[-12:],
        },
        "news": news_items[:8],
    }
    messages = [
        {"role": "system", "content": "You are a local crypto market analyst. Return one valid JSON object only."},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]
    if "ollama" in str(state.get("aiProvider", "")).lower() or "11434" in base_url:
        native_base = base_url[:-3] if base_url.endswith("/v1") else base_url
        response = requests.post(
            f"{native_base}/api/chat",
            timeout=timeout,
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "format": "json",
                "think": False,
                "options": {"temperature": 0.1, "num_predict": 700},
            },
        )
        response.raise_for_status()
        data = response.json()
        content = ((data.get("message") or {}).get("content") or "").strip()
    else:
        response = requests.post(
            f"{base_url}/chat/completions",
            timeout=timeout,
            headers={"Authorization": "Bearer local", "Content-Type": "application/json"},
            json={"model": model, "temperature": 0.1, "max_tokens": 700, "stream": False, "messages": messages},
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _parse_json_object(content)
    parsed["source"] = "local_ai"
    parsed["model"] = model
    return parsed


def _news_sentiment(title_l: str) -> str:
    if any(w in title_l for w in ("surge", "inflow", "rally", "rise", "gain")):
        return "Bullish"
    if any(w in title_l for w in ("fall", "drop", "outflow", "hack", "loss")):
        return "Bearish"
    return "Neutral"


def _fallback_intelligence(status: dict, ohlcv: list[dict], news_items: list[dict], error: str = "") -> dict:
    rows, final = _deterministic_regime_rows(status, ohlcv, error)
    cards = []
    for item in (news_items or [])[:NEWS_CARD_LIMIT]:
        title_l = item.get("title", "").lower()
        sentiment = _news_sentiment(title_l)
        cards.append({
            "title": item.get("title", "Crypto market update"),
            "source": item.get("source", "RSS"),
            "age": item.get("publishedUtc", "latest")[:16].replace("T", " "),
            "sentiment": sentiment,
            "impact": 6 if "bitcoin" in title_l or "btc" in title_l else 4,
            "url": item.get("url", ""),
        })
    while len(cards) < NEWS_CARD_LIMIT:
        cards.append({"title": "Awaiting fresh crypto headlines", "source": "Local", "age": "30m refresh", "sentiment": "Neutral", "impact": 3, "url": ""})
    return {"newsCards": cards, "regimeSignals": rows, "finalRegime": final, "source": "deterministic_fallback", "model": "", "error": error}


def refresh_intelligence(state: dict, status: dict, ohlcv: list[dict]) -> dict:
    started = datetime.now(timezone.utc)
    news_items = _fetch_news_items()
    error = ""
    try:
        if state.get("aiEnabled", True):
            assessed = _local_ai_assess_intelligence(state, status, ohlcv, news_items)
        else:
            assessed = _fallback_intelligence(status, ohlcv, news_items, "local AI disabled")
    except Exception as exc:
        error = str(exc)
        assessed = _fallback_intelligence(status, ohlcv, news_items, error)
    payload = {
        "generatedAtUtc": started.isoformat(),
        "nextRefreshAtUtc": datetime.fromtimestamp(started.timestamp() + INTELLIGENCE_REFRESH_SECONDS, timezone.utc).isoformat(),
        "rawNews": news_items,
        **assessed,
    }
    write_json(INTELLIGENCE_PATH, payload)
    return payload


def get_intelligence(state: dict, status: dict, ohlcv: list[dict], force: bool = False) -> dict:
    global _intelligence_refreshing
    cached = read_json(INTELLIGENCE_PATH)
    def _start_background_refresh() -> None:
        global _intelligence_refreshing
        if _intelligence_refreshing:
            return
        def _worker():
            global _intelligence_refreshing
            try:
                refresh_intelligence(state, status, ohlcv)
            finally:
                with _intelligence_lock:
                    _intelligence_refreshing = False
        with _intelligence_lock:
            if not _intelligence_refreshing:
                _intelligence_refreshing = True
                threading.Thread(target=_worker, daemon=True).start()

    if not force and cached.get("generatedAtUtc"):
        age = freshness_seconds(cached.get("generatedAtUtc"))
        if age is not None and age < INTELLIGENCE_REFRESH_SECONDS:
            return cached
        _start_background_refresh()
        cached["refreshing"] = True
        return cached
    if not force:
        news_items = _fetch_news_items()
        payload = _fallback_intelligence(status, ohlcv, news_items, "local AI refresh starting in background")
        now = datetime.now(timezone.utc)
        payload["generatedAtUtc"] = now.isoformat()
        payload["nextRefreshAtUtc"] = datetime.fromtimestamp(now.timestamp() + INTELLIGENCE_REFRESH_SECONDS, timezone.utc).isoformat()
        payload["rawNews"] = news_items
        payload["refreshing"] = True
        _start_background_refresh()
        return payload
    return refresh_intelligence(state, status, ohlcv)


def _render_server_chart_svg(rows: list[dict], live_price: float | None) -> str:
    rows = merge_live_price_into_ohlcv(rows[-180:], live_price)
    if not rows:
        return '<div class="server-chart-fallback"></div>'
    width, height = 1200, 520
    pad_l, pad_r, pad_t, pad_b = 48, 78, 28, 58
    price_h = 380
    chart_w = width - pad_l - pad_r
    highs = [float(r.get("high") or 0) for r in rows]
    lows = [float(r.get("low") or 0) for r in rows]
    vols = [float(r.get("volumeUsdt") or 0) for r in rows]
    max_p, min_p = max(highs), min(lows)
    span_p = max(1e-9, max_p - min_p)
    max_v = max(max(vols), 1)

    def x_for(i: int) -> float:
        return pad_l + (chart_w * i / max(1, len(rows) - 1))

    def y_for(price: float) -> float:
        return pad_t + ((max_p - price) / span_p) * (price_h - pad_t)

    bands = []
    band_w = chart_w / 3
    for i, (label, color, text_color) in enumerate([
        ("DISTRIBUTION", "#fff3ee", "#d54545"),
        ("RANGE CONSOLIDATION", "#eef6ff", "#1767c2"),
        ("ACCUMULATION", "#f1fbf4", "#0d8a2f"),
    ]):
        x = pad_l + (band_w * i)
        bands.append(f'<rect x="{x:.1f}" y="{pad_t}" width="{band_w:.1f}" height="{price_h-pad_t}" fill="{color}"/>')
        bands.append(f'<text x="{x + band_w/2:.1f}" y="{pad_t+28}" text-anchor="middle" font-size="13" font-weight="800" fill="{text_color}">{label}</text>')
    grid = []
    for i in range(6):
        y = pad_t + ((price_h - pad_t) * i / 5)
        price = max_p - ((max_p - min_p) * i / 5)
        grid.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}" stroke="#ded8cc" stroke-width="1"/>')
        grid.append(f'<text x="{width-pad_r+12}" y="{y+4:.1f}" font-size="13" fill="#706d64">{_fmt_num(price, 0)}</text>')
    line_pts = " ".join(f"{x_for(i):.1f},{y_for(float(r.get('close') or 0)):.1f}" for i, r in enumerate(rows))
    candles = []
    candle_w = max(2.0, chart_w / max(1, len(rows)) * 0.55)
    for i, r in enumerate(rows):
        x = x_for(i)
        open_p = float(r.get("open") or 0)
        close_p = float(r.get("close") or 0)
        high_p = float(r.get("high") or 0)
        low_p = float(r.get("low") or 0)
        color = "#4dbb92" if close_p >= open_p else "#33302a"
        y_high, y_low = y_for(high_p), y_for(low_p)
        y_open, y_close = y_for(open_p), y_for(close_p)
        body_y = min(y_open, y_close)
        body_h = max(2.0, abs(y_close - y_open))
        vol_h = (float(r.get("volumeUsdt") or 0) / max_v) * 76
        candles.append(f'<line x1="{x:.1f}" y1="{y_high:.1f}" x2="{x:.1f}" y2="{y_low:.1f}" stroke="{color}" stroke-width="1"/>')
        candles.append(f'<rect x="{x-candle_w/2:.1f}" y="{body_y:.1f}" width="{candle_w:.1f}" height="{body_h:.1f}" fill="{color}" opacity="0.88"/>')
        candles.append(f'<rect x="{x-candle_w/2:.1f}" y="{height-pad_b-vol_h:.1f}" width="{candle_w:.1f}" height="{vol_h:.1f}" fill="{color}" opacity="0.34"/>')
    latest = rows[-1]
    latest_price = float(latest.get("close") or 0)
    latest_y = y_for(latest_price)
    return (
        '<div class="server-chart-fallback">'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="BTC price chart">'
        '<rect width="1200" height="520" fill="#fffdf8"/>'
        + "".join(bands)
        + "".join(grid)
        + "".join(candles)
        + f'<polyline points="{line_pts}" fill="none" stroke="#f7931a" stroke-width="3"/>'
        + f'<line x1="{pad_l}" y1="{latest_y:.1f}" x2="{width-pad_r}" y2="{latest_y:.1f}" stroke="#f7931a" stroke-dasharray="4 5" opacity="0.55"/>'
        + f'<rect x="{width-pad_r-2}" y="{latest_y-15:.1f}" width="74" height="30" rx="6" fill="#f7931a"/><text x="{width-pad_r+35}" y="{latest_y+5:.1f}" text-anchor="middle" font-size="13" font-weight="800" fill="#fff">{_fmt_num(latest_price, 0)}</text>'
        + '</svg></div>'
    )


def _render_server_events(events: list[dict]) -> str:
    rows = list(reversed(events or []))[:8]
    if not rows:
        return '<tr><td colspan="4">No events yet</td></tr>'
    out = []
    for ev in rows:
        out.append(
            "<tr>"
            f"<td>{html_lib.escape(str(ev.get('tsUtc') or '--'))}</td>"
            f"<td>{html_lib.escape(str(ev.get('event') or '--'))}</td>"
            f"<td>{_fmt_num(ev.get('price'), 2)}</td>"
            f"<td>{_fmt_num(ev.get('qtyBtc'), 6)}</td>"
            "</tr>"
        )
    return "".join(out)


def _render_server_orders(orders: list[dict], price: float) -> str:
    rows = (orders or [])[:12]
    if not rows:
        return '<tr><td colspan="6">No open orders</td></tr>'
    out = []
    for order in rows:
        order_price = float(order.get("price") or 0)
        total = float(order.get("total") or (float(order.get("qty_btc") or 0) * order_price))
        side = str(order.get("side") or "--")
        rel = "--"
        if price > 0 and order_price > 0:
            delta = abs((order_price / price) - 1)
            rel = ("Below" if order_price < price else "Above") + f" market {_fmt_pct(delta)}"
        out.append(
            "<tr>"
            f'<td class="order-side {side.lower()}">{html_lib.escape(side)}</td>'
            f"<td>{_fmt_num(order_price, 2)}</td>"
            f"<td>{html_lib.escape(rel)}</td>"
            f"<td>{_fmt_num(order.get('qty_btc'), 6)}</td>"
            f"<td>{_fmt_money(total)}</td>"
            f"<td>{html_lib.escape(str(order.get('type') or 'LIMIT'))}</td>"
            "</tr>"
        )
    return "".join(out)


def _render_server_config(state: dict) -> str:
    preferred = [
        "symbol", "interval", "feeBps", "gridMode", "gridLevels", "gridSpacingPct", "gridMaxExposurePct",
        "riskPerTradePct", "positionCapPct", "maxDailyLossPct", "aiEnabled", "aiProvider", "aiBaseUrl",
        "aiModel", "aiMinConfidence", "aiPollSeconds", "allowLiveOrders", "paused",
    ]
    keys = [k for k in preferred if k in state] + [k for k in sorted(state.keys()) if k not in preferred]
    fields = []
    for key in keys:
        value = state.get(key)
        fields.append(
            '<div class="config-field">'
            f'<label for="cfg-{html_lib.escape(str(key))}">{html_lib.escape(str(key))}</label>'
            f'<input id="cfg-{html_lib.escape(str(key))}" data-key="{html_lib.escape(str(key))}" type="text" value="{html_lib.escape(str(value))}">'
            '</div>'
        )
    return "".join(fields)


def _render_impact_bars(level, color: str = "orange", size: int = 8) -> str:
    try:
        level_i = max(0, min(size, int(float(level))))
    except Exception:
        level_i = 3
    cls = "green" if color == "green" else "orange"
    return '<div class="impact-bars">' + "".join(f'<span class="{"on " + cls if i < level_i else ""}"></span>' for i in range(size)) + "</div>"


def _macro_calendar_events(now: datetime | None = None) -> list[dict]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    gst_now = current.astimezone(GST)
    events = []
    for item in MACRO_CALENDAR_TEMPLATE:
        event_gst = gst_now.replace(
            hour=int(item["hour"]),
            minute=int(item["minute"]),
            second=0,
            microsecond=0,
        )
        completed = gst_now >= event_gst
        hour = event_gst.hour % 12 or 12
        ampm = "AM" if event_gst.hour < 12 else "PM"
        events.append({
            "title": item["title"],
            "date": f"{event_gst:%b} {event_gst.day}",
            "time": f"{hour}:{event_gst:%M} {ampm} GST",
            "impact": item["impact"],
            "color": item["color"],
            "status": "Completed" if completed else "Upcoming",
            "summary": item["completed"] if completed else item["upcoming"],
        })
    return events


def _render_macro_calendar(now: datetime | None = None) -> str:
    rows = []
    for idx, event in enumerate(_macro_calendar_events(now), start=1):
        status = str(event["status"])
        status_cls = "good" if status == "Completed" else "warn"
        dots = "".join(
            f'<span class="{"on" if i < int(event["impact"]) else ""}"></span>'
            for i in range(3)
        )
        rows.append(
            f'<div class="calendar-row {status.lower()}">'
            f'<div class="calendar-icon" style="background:{html_lib.escape(str(event["color"]))}">{idx}</div>'
            '<div class="calendar-main">'
            f'<strong>{html_lib.escape(str(event["title"]))}</strong>'
            f'<div class="calendar-summary">{html_lib.escape(status)} - {html_lib.escape(str(event["summary"]))}</div>'
            "</div>"
            f'<span class="calendar-meta">{html_lib.escape(str(event["date"]))}<br>{html_lib.escape(str(event["time"]))}</span>'
            f'<span class="status-chip {status_cls}">{html_lib.escape(status)}</span>'
            '<div class="calendar-impact">'
            '<div class="calendar-meta" style="margin-bottom:5px">Impact</div>'
            f'<div class="impact-dots">{dots}</div>'
            "</div>"
            "</div>"
        )
    return "".join(rows)


def _normalized_news_cards(intelligence: dict) -> list[dict]:
    cards = [
        dict(card)
        for card in (intelligence.get("newsCards") or [])
        if isinstance(card, dict)
    ]
    seen_titles = {str(card.get("title") or "").strip().lower() for card in cards}
    for item in [
        raw
        for raw in (intelligence.get("rawNews") or [])
        if isinstance(raw, dict)
    ]:
        title = str(item.get("title") or "Crypto market update").strip()
        title_key = title.lower()
        if len(cards) < NEWS_CARD_LIMIT and title_key and title_key not in seen_titles:
            title_l = title.lower()
            cards.append({
                "title": title,
                "source": item.get("source") or "RSS",
                "age": str(item.get("publishedUtc") or "latest")[:16].replace("T", " "),
                "sentiment": _news_sentiment(title_l),
                "impact": 6 if "bitcoin" in title_l or "btc" in title_l else 4,
                "url": item.get("url") or "",
            })
            seen_titles.add(title_key)
    while len(cards) < NEWS_CARD_LIMIT:
        cards.append({
            "title": "Awaiting fresh crypto headlines",
            "source": "Local",
            "age": "30m refresh",
            "sentiment": "Neutral",
            "impact": 3,
            "url": "",
        })
    return cards[:NEWS_CARD_LIMIT]


def _render_server_news(intelligence: dict) -> str:
    cards = _normalized_news_cards(intelligence)
    out = []
    for card in cards:
        sentiment = str(card.get("sentiment") or "Neutral")
        color = "green" if sentiment.lower() == "bullish" else "orange"
        title = html_lib.escape(str(card.get("title") or "Crypto market update"))
        source = html_lib.escape(str(card.get("source") or "Local AI"))
        age = html_lib.escape(str(card.get("age") or "30m refresh"))
        url = html_lib.escape(str(card.get("url") or ""))
        title_html = f'<a href="{url}" target="_blank" rel="noreferrer">{title}</a>' if url else title
        out.append(
            '<div class="news-card">'
            f'<div class="news-title">{title_html}</div>'
            f'<div class="news-row"><div><span class="source-chip">{source}</span> <span class="news-meta">{age}</span></div>'
            f'<span class="sentiment-chip {"bullish" if color == "green" else "neutral"}">{html_lib.escape(sentiment)}</span></div>'
            '<div class="news-meta" style="margin-bottom:6px">Impact</div>'
            f'{_render_impact_bars(card.get("impact", 4), color)}'
            '</div>'
        )
    return "".join(out)


def _render_server_signals(intelligence: dict) -> str:
    rows = intelligence.get("regimeSignals") or []
    out = ['<div class="signal-row header"><div>Signal</div><div>Status</div><div>Trend</div><div>Notes</div></div>']
    for row in rows[:8]:
        tone = str(row.get("tone") or "orange")
        chip = "good" if tone == "green" else "warn"
        color = "var(--green)" if tone == "green" else ("var(--blue)" if tone == "blue" else "var(--btc)")
        out.append(
            '<div class="signal-row">'
            f'<strong>{html_lib.escape(str(row.get("signal") or "--"))}</strong>'
            f'<span class="status-chip {chip}">{html_lib.escape(str(row.get("status") or "--"))}</span>'
            f'<span class="spark" style="color:{color}"></span>'
            f'<span>{html_lib.escape(str(row.get("note") or ""))}</span>'
            '</div>'
        )
    return "".join(out)


def render_initial_dashboard_html(interval_override: str | None = None) -> str:
    status = read_json(STATUS_PATH)
    state = read_json(STATE_PATH)
    runtime = read_json(RUNTIME_PATH)
    cumulative = read_json(CUM_PATH)
    events = read_events()
    symbol = status.get("symbol") or state.get("symbol", "BTCUSDT")
    interval = normalize_interval(interval_override or status.get("interval") or state.get("interval", "1m"))
    ohlcv = get_ohlcv(symbol, interval, limit=SUPPORTED_INTERVALS[interval]["default_limit"], offset=0)
    ohlcv = apply_status_price_to_ohlcv(ohlcv, status, interval)
    status = apply_market_price_to_status(status, latest_ohlcv_price(ohlcv))
    intelligence = get_intelligence(state, status, ohlcv)
    orders = ((runtime.get("grid") or {}).get("orders") or [])
    position = status.get("position") or {}
    equity = float(status.get("equityUsdt") or 0.0)
    price = float(status.get("price") or 0.0)
    btc_value = float(status.get("btc") or 0.0) * price
    exposure = (btc_value / equity) if equity > 0 else 0.0
    realized = float(cumulative.get("realizedPnlUsdt") or 0.0)
    unreal = float(position.get("unrealizedPnlUsdt") or 0.0)
    fees = float(cumulative.get("feesPaidUsdt") or 0.0)
    freshness = freshness_seconds(status.get("tsUtc"))
    fresh_label = f"Live payload - {freshness:.2f}s" if freshness is not None else "No timestamp"
    state_label = "PAUSED" if state.get("paused") else ("LIVE / AI-GATED" if state.get("aiEnabled", True) else "LIVE / GRID")
    risk = "High" if exposure > 0.85 else ("Normal" if exposure > 0.55 else "Light")
    mode = dashboard_mode_label(state)
    metrics_html = "".join(
        f'<div class="metric" data-summary-key="{key}"><div class="label">{label}</div><div class="value {cls}" id="summary-{key}">{value}</div></div>'
        for key, label, value, cls in [
            ("equity", "Total Equity", _fmt_money(equity), ""),
            ("realized", "Current Net PnL", _fmt_money(realized), _signed_class(realized)),
            ("unrealized", "Unrealized Net PnL", _fmt_money(unreal), _signed_class(unreal)),
            ("fees", "Total Fees Paid", _fmt_money(fees), "negative" if fees > 0 else ""),
        ]
    )
    news_html = _render_server_news(intelligence)
    signal_html = _render_server_signals(intelligence)
    final_regime = intelligence.get("finalRegime") or {}
    calendar_html = _render_macro_calendar()
    status_html = "".join(
        f'<div class="kv"><div class="k">{label}</div><div class="v">{html_lib.escape(str(value))}</div></div>'
        for label, value in [
            ("Status timestamp", status.get("tsUtc") or "--"),
            ("Runtime saved", runtime.get("savedAt") or "--"),
            ("Grid status", "Active" if ((runtime.get("grid") or {}).get("orders") or []) else "Idle"),
            ("AI model", (status.get("stats") or {}).get("ai", {}).get("model") or state.get("aiModel") or "--"),
        ]
    )
    timeframe_html = "".join(
        f'<a class="btn {"active-timeframe" if key == interval else ""}" href="/?interval={html_lib.escape(key)}">{html_lib.escape(meta["label"])}</a>'
        for key, meta in SUPPORTED_INTERVALS.items()
    )
    final_title = html_lib.escape(str(final_regime.get("title") or "Range Consolidation"))
    final_copy = html_lib.escape(str(final_regime.get("copy") or "Choppy price action within established range. Maintain grid discipline and capital efficiency."))
    generated = intelligence.get("generatedAtUtc") or ""
    next_refresh = intelligence.get("nextRefreshAtUtc") or ""
    intel_note = f'{html_lib.escape(str(intelligence.get("source") or "local"))} - next {html_lib.escape(str(next_refresh)[11:16] or "30m")}'
    page = HTML
    replacements = {
        'id="fresh-label">Waiting for data</span>': f'id="fresh-label">{html_lib.escape(fresh_label)}</span>',
        'id="server-time">--</span>': f'id="server-time">{html_lib.escape(format_gst_datetime())}</span>',
        '<button class="btn" type="button" id="top-timeframe">1m</button>': f'<a class="btn" id="top-timeframe" href="/?interval={html_lib.escape(interval)}">{html_lib.escape(SUPPORTED_INTERVALS[interval]["label"])}</a>',
        'id="trading-state-label">LIVE / AI-GATED</div>': f'id="trading-state-label">{html_lib.escape(state_label)}</div>',
        'id="sticky-summary"></div>': f'id="sticky-summary">{metrics_html}</div>',
        'id="state-mode">Grid + Local AI</strong>': f'id="state-mode">{html_lib.escape(mode)}</strong>',
        'id="state-risk" class="positive">Normal</strong>': f'id="state-risk" class="{_signed_class(1 if risk != "High" else -1)}">{html_lib.escape(risk)}</strong>',
        'id="state-exposure">--</strong>': f'id="state-exposure">{_fmt_pct(exposure)}</strong>',
        '<h2>BTC/USD · 1H · INDEX</h2>': f'<h2>BTC/USD · {html_lib.escape(SUPPORTED_INTERVALS[interval]["label"])} · INDEX</h2>',
        'id="chart-price-pill"><span class="label">BTC Price</span><span>--</span></div>': f'id="chart-price-pill"><span class="label">BTC Price</span><span>{_fmt_num(price, 2)}</span></div>',
        '<div class="pillrow" id="timeframe-controls" style="margin-bottom:10px"></div>': f'<div class="pillrow" id="timeframe-controls" style="margin-bottom:10px">{timeframe_html}</div>',
        '<canvas id="market-chart"></canvas>': f'<canvas id="market-chart"></canvas>{_render_server_chart_svg(ohlcv, price)}',
        '<tbody id="events-body"></tbody>': f'<tbody id="events-body">{_render_server_events(events)}</tbody>',
        '<tbody id="orders-body"></tbody>': f'<tbody id="orders-body">{_render_server_orders(orders, price)}</tbody>',
        '<div class="config-grid" id="config-form-grid"></div>': f'<div class="config-grid" id="config-form-grid">{_render_server_config(state)}</div>',
        'id="news-stack"></div>': f'id="news-stack">{news_html}</div>',
        '<span class="footer-note">View All</span>': f'<span class="footer-note">{intel_note}</span>',
        'id="signal-table"></div>': f'id="signal-table">{signal_html}</div>',
        'id="regime-updated">live</span>': f'id="regime-updated">AI refresh {html_lib.escape(str(generated)[11:16] or "now")}</span>',
        'id="final-regime-title">Range Consolidation</div>': f'id="final-regime-title">{final_title}</div>',
        'id="final-regime-copy">Choppy price action within established range. Maintain grid discipline and capital efficiency.</div>': f'id="final-regime-copy">{final_copy}</div>',
        'id="macro-calendar"></div>': f'id="macro-calendar">{calendar_html}</div>',
        'id="status-list"></div>': f'id="status-list">{status_html}</div>',
    }
    for old, new in replacements.items():
        page = page.replace(old, new)
    return page


def build_market_payload(qs: dict[str, list[str]]) -> dict:
    status = read_json(STATUS_PATH)
    state = read_json(STATE_PATH)
    runtime = read_json(RUNTIME_PATH)
    cumulative = read_json(CUM_PATH)
    symbol = status.get('symbol') or state.get('symbol', 'BTCUSDT')
    requested_interval = normalize_interval(qs.get('interval', [status.get('interval') or state.get('interval', '1m')])[0])
    raw_limit = qs.get('limit', [SUPPORTED_INTERVALS[requested_interval]['default_limit']])[0]
    raw_offset = qs.get('offset', [0])[0]
    try:
        limit = max(30, min(MAX_OHLCV_LIMIT, int(raw_limit)))
    except Exception:
        limit = SUPPORTED_INTERVALS[requested_interval]['default_limit']
    try:
        offset = max(0, int(raw_offset))
    except Exception:
        offset = 0
    include_ohlcv = qs.get('ohlcv', ['1'])[0] not in {'0', 'false', 'no'}
    ohlcv = get_ohlcv(symbol, requested_interval, limit=limit, offset=offset) if include_ohlcv else []
    if include_ohlcv:
        ohlcv = apply_status_price_to_ohlcv(ohlcv, status, requested_interval)
    status_payload = apply_market_price_to_status(status, latest_ohlcv_price(ohlcv))
    state_payload = dict(state)
    state_payload['aiEndpointKey'] = infer_ai_endpoint_key(state)
    ai_endpoint = AI_ENDPOINT_BY_KEY.get(state_payload['aiEndpointKey']) or AI_ENDPOINT_BY_KEY['custom']
    payload = {
        'schemaVersion': DASHBOARD_SCHEMA_VERSION,
        'serverInstanceId': SERVER_INSTANCE_ID,
        'channel': 'status',
        'seq': next_sequence('status'),
        'status': status_payload,
        'state': state_payload,
        'runtime': runtime,
        'cumulative': cumulative,
        'events': read_events(),
        'aiDecisions': read_ai_decisions(status_payload, cumulative),
        'ohlcv': ohlcv,
        'chartInterval': requested_interval,
        'chartLimit': limit,
        'chartOffset': offset,
        'supportedIntervals': list(SUPPORTED_INTERVALS.keys()),
        'aiEndpointKey': ai_endpoint['key'],
        'aiEndpointLabel': ai_endpoint['label'],
        'freshnessSeconds': freshness_seconds(status.get('tsUtc')),
        'serverTimeUtc': datetime.now(timezone.utc).isoformat(),
        'refreshMs': REFRESH_MS,
    }
    return validate_market_payload(payload)


def build_chart_tick_payload(qs: dict[str, list[str]]) -> dict:
    status = read_json(STATUS_PATH)
    state = read_json(STATE_PATH)
    symbol = status.get('symbol') or state.get('symbol', 'BTCUSDT')
    requested_interval = normalize_interval(qs.get('interval', [status.get('interval') or state.get('interval', '1m')])[0])
    ohlcv = get_ohlcv(symbol, requested_interval, limit=1, offset=0)
    ohlcv = apply_status_price_to_ohlcv(ohlcv, status, requested_interval)
    status_payload = apply_market_price_to_status(status, latest_ohlcv_price(ohlcv))
    payload = {
        'schemaVersion': DASHBOARD_SCHEMA_VERSION,
        'serverInstanceId': SERVER_INSTANCE_ID,
        'channel': 'chart',
        'seq': next_sequence('chart'),
        'serverTimeUtc': datetime.now(timezone.utc).isoformat(),
        'chartInterval': requested_interval,
        'bar': ohlcv[-1] if ohlcv else None,
        'status': status_payload,
        'freshnessSeconds': freshness_seconds(status.get('tsUtc')),
        'refreshMs': REFRESH_MS,
    }
    return validate_chart_tick_payload(payload)


# Import after dashboard globals are defined; dashboard_routes binds them lazily.
from dashboard_routes import Handler  # noqa: E402


if __name__ == '__main__':  # pragma: no cover - daemon server entrypoint
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'Dashboard listening on http://{HOST}:{PORT}', flush=True)
    server.serve_forever()
