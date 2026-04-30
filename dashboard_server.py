import json
import os
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from math import ceil
import re

from bot import BinanceSpotREST
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
STATUS_PATH = BASE_DIR / "engine_status.json"
STATE_PATH = BASE_DIR / "state.json"
RUNTIME_PATH = BASE_DIR / "runtime_state.json"
CUM_PATH = BASE_DIR / "cumulative.json"
TRADES_PATH = BASE_DIR / "trades.jsonl"
HISTORY_PATH = BASE_DIR / "dashboard_history.json"
HOST = os.getenv("TRADEBOT_DASHBOARD_HOST", "0.0.0.0")
PORT = int(os.getenv("TRADEBOT_DASHBOARD_PORT", "8844"))

load_dotenv(BASE_DIR / ".env", override=False)
_md_clients: dict[str, BinanceSpotREST] = {}
_ohlcv_cache: dict[tuple[str, str, int, int], list[dict]] = {}
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

HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Tradebot Live Dashboard</title>
  <style>
    :root {
      --bg: #0b1020;
      --panel: rgba(18, 24, 45, 0.92);
      --panel-2: rgba(30, 38, 68, 0.82);
      --panel-3: rgba(11, 16, 32, 0.96);
      --text: #f4f7fb;
      --muted: #98a2b3;
      --line: rgba(255,255,255,0.08);
      --green: #29d391;
      --red: #ff6b81;
      --amber: #f7b955;
      --blue: #5ea6ff;
      --shadow: 0 18px 50px rgba(0,0,0,.28);
      --radius: 18px;
    }
    body.light {
      --bg: #eef3fb;
      --panel: rgba(255, 255, 255, 0.92);
      --panel-2: rgba(242, 246, 252, 0.96);
      --panel-3: rgba(248, 250, 254, 0.99);
      --text: #132033;
      --muted: #5b6980;
      --line: rgba(19,32,51,0.08);
      --shadow: 0 18px 50px rgba(39,68,114,.12);
    }
    * { box-sizing: border-box; }
    html, body { margin:0; min-height:100%; font-family: Inter, system-ui, sans-serif; background: var(--bg); color: var(--text); }
    .wrap { padding: 14px; }
    .topbar {
      display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:14px;
      position:sticky; top:0; z-index:30; background:linear-gradient(180deg, rgba(11,16,32,.96), rgba(11,16,32,.2)); padding:8px 0;
      backdrop-filter: blur(10px);
    }
    body.light .topbar { background:linear-gradient(180deg, rgba(238,243,251,.96), rgba(238,243,251,.2)); }
    .title h1 { margin:0; font-size:1.6rem; }
    .title p { margin:4px 0 0; color:var(--muted); }
    .pillrow { display:flex; flex-wrap:wrap; gap:8px; }
    .pill, .btn {
      background: rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.1); color:var(--text); border-radius:999px;
      padding:9px 12px; font:inherit;
    }
    .btn.playing { border-color: rgba(41,211,145,.45); box-shadow: inset 0 0 0 1px rgba(41,211,145,.18); }
    .btn.paused { border-color: rgba(247,185,85,.45); box-shadow: inset 0 0 0 1px rgba(247,185,85,.18); }
    .pager { display:flex; gap:8px; flex-wrap:wrap; align-items:center; justify-content:space-between; }
    .pager-controls { display:flex; gap:8px; flex-wrap:wrap; }
    .page-indicator { color:var(--text); background:var(--panel-2); border:1px solid var(--line); padding:8px 12px; border-radius:999px; font-size:.84rem; font-weight:700; }
    body.light .pill, body.light .btn { background: rgba(255,255,255,.8); border-color: rgba(19,32,51,.12); }
    .btn { cursor:pointer; border-radius:12px; transition:background .16s ease, border-color .16s ease, box-shadow .16s ease, transform .16s ease; touch-action: manipulation; -webkit-tap-highlight-color: transparent; }
    .btn:hover { transform:translateY(-1px); }
    .btn.active-timeframe {
      background:linear-gradient(180deg, rgba(94,166,255,.24), rgba(94,166,255,.14));
      border-color:rgba(94,166,255,.48);
      box-shadow:inset 0 0 0 1px rgba(94,166,255,.18), 0 8px 20px rgba(94,166,255,.12);
      color:#fff;
    }
    body.light .btn.active-timeframe { color:var(--text); }
    .dashboard {
      display:grid; grid-template-columns: repeat(24, minmax(0, 1fr)); gap:14px; position:relative;
      grid-auto-flow:dense;
      align-items:start;
    }
    .card {
      position:relative; grid-column: span 8; min-height:180px; background:linear-gradient(180deg, var(--panel), rgba(9,14,28,.96));
      border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); padding:14px; overflow:hidden;
      transition: box-shadow .18s ease, transform .18s ease, border-color .18s ease;
    }
    body.light .card { background:linear-gradient(180deg, var(--panel), rgba(240,246,252,.98)); }
    .card:hover { box-shadow:0 24px 60px rgba(0,0,0,.34); border-color:rgba(94,166,255,.26); }
    .card.dragging {
      opacity:.96; transform:scale(1.02); box-shadow:0 28px 70px rgba(0,0,0,.42); z-index:40;
      pointer-events:none;
    }
    .card.drop-target { border-color:rgba(247,185,85,.55); box-shadow:0 0 0 1px rgba(247,185,85,.25), 0 24px 60px rgba(0,0,0,.34); }
    .card.reflowing { transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease; }
    .card-head { display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:10px; cursor:move; }
    .card-actions { display:flex; align-items:center; gap:8px; color:var(--muted); }
    .card h2 { margin:0; font-size:1rem; }
    .card-body { display:flex; flex-direction:column; gap:10px; min-height:120px; height:calc(100% - 36px); }
    .summary { grid-column: span 24; min-height:120px; }
    .chart-card { grid-column: span 16; min-height:480px; }
    .events-card, .orders-card { grid-column: span 12; min-height:300px; }
    .wide { grid-column: span 8; }
    .sticky-summary { display:grid; grid-template-columns: repeat(6, minmax(0,1fr)); gap:10px; }
    .metric, .kv { background:var(--panel-2); border:1px solid var(--line); border-radius:14px; padding:12px; }
    .metric .label, .kv .k { color:var(--muted); font-size:.78rem; text-transform:uppercase; letter-spacing:.08em; }
    .metric .value { font-size:1.35rem; font-weight:700; margin-top:8px; }
    .kv .v { margin-top:5px; font-weight:600; }
    .kv.changed .v, .metric.changed .value { animation: pulse 0.8s ease; }
    @keyframes pulse { 0% { color: var(--amber); } 100% { color: inherit; } }
    .kv-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap:8px; }
    .positive { color: var(--green); }
    .negative { color: var(--red); }
    .chart-wrap { position:relative; flex:1; min-height:260px; border-radius:16px; border:1px solid var(--line); background:linear-gradient(180deg, rgba(9,14,28,.98), rgba(9,14,28,.88)); overflow:hidden; }
    body.light .chart-wrap { background:linear-gradient(180deg, rgba(248,250,254,.98), rgba(239,244,251,.92)); }
    .legend { display:flex; flex-wrap:wrap; gap:10px; color:var(--muted); font-size:.82rem; }
    .legend b { color:var(--text); }
    canvas { width:100%; height:100%; display:block; }
    .chart-overlay {
      position:absolute; top:10px; left:10px; right:10px; display:flex; justify-content:space-between; gap:8px; pointer-events:none; font-size:.82rem;
    }
    .overlay-box {
      background:linear-gradient(180deg, rgba(5,8,18,.82), rgba(5,8,18,.62));
      border:1px solid rgba(255,255,255,.10);
      padding:10px 12px;
      border-radius:12px;
      box-shadow:0 10px 25px rgba(0,0,0,.22);
      backdrop-filter: blur(10px);
    }
    body.light .overlay-box { background:linear-gradient(180deg, rgba(255,255,255,.94), rgba(250,252,255,.84)); }
    .overlay-box strong { display:block; font-size:.72rem; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); margin-bottom:4px; }
    .table-wrap { flex:1; overflow:auto; border-radius:14px; border:1px solid var(--line); background:var(--panel-3); }
    table { width:100%; border-collapse:collapse; min-width:620px; }
    th, td { padding:10px 12px; border-bottom:1px solid var(--line); text-align:left; font-size:.88rem; }
    th { position:sticky; top:0; background:rgba(16,22,41,.98); }
    body.light th { background:rgba(245,248,252,.98); }
    .detail-box { white-space:pre-wrap; font-size:.84rem; background:var(--panel-2); border:1px solid var(--line); border-radius:14px; padding:12px; min-height:90px; }
    .drag-hint { color:var(--muted); font-size:.78rem; }
    .resize-handle {
      position:absolute; right:8px; bottom:8px; width:16px; height:16px; border-right:2px solid rgba(255,255,255,.28); border-bottom:2px solid rgba(255,255,255,.28);
      opacity:.8; cursor:nwse-resize; border-radius:0 0 10px 0;
    }
    body.light .resize-handle { border-color:rgba(19,32,51,.28); }
    .snap-badge {
      position:absolute; right:14px; top:14px; font-size:.72rem; color:var(--muted); background:rgba(255,255,255,.05); border:1px solid var(--line); padding:4px 8px; border-radius:999px;
    }
    @media (max-width: 1200px) {
      .chart-card, .events-card, .orders-card, .wide { grid-column: span 24; }
      .sticky-summary { grid-template-columns: repeat(2, minmax(0,1fr)); }
    }
    @media (max-width: 720px) {
      .sticky-summary { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="title">
      <h1>Tradebot Live Dashboard</h1>
      <p id="subtitle">Realtime dashboard with live cards, candlesticks, and movable panels</p>
    </div>
    <div class="pillrow">
      <div class="pill" id="fresh-label">Waiting for data</div>
      <div class="pill"><span id="pill-symbol">--</span> • <span id="pill-interval">--</span></div>
      <div class="pill">Server <span id="server-time">--</span></div>
      <button class="btn" id="bot-toggle-btn" type="button">Pause bot</button>
      <button class="btn" id="theme-toggle" type="button">Toggle theme</button>
      <button class="btn" id="refresh-btn" type="button">Refresh</button>
      <button class="btn" id="zoom-in-btn" type="button">Zoom in</button>
      <button class="btn" id="zoom-out-btn" type="button">Zoom out</button>
      <button class="btn" id="reset-layout-btn" type="button">Reset layout</button>
    </div>
  </div>

  <div class="dashboard" id="dashboard">
    <section class="card summary" id="summary-card" data-default-col="1" data-default-span="24">
      <div class="card-head"><h2>Top Summary</h2><div class="card-actions"><span class="drag-hint">drag, resize</span></div></div>
      <div class="snap-badge">24 cols</div><div class="card-body"><div class="sticky-summary" id="sticky-summary"></div></div><div class="resize-handle" aria-hidden="true"></div>
    </section>

    <section class="card chart-card" id="market-card" data-default-col="1" data-default-span="16">
      <div class="card-head"><h2>BTC/USDT Candles</h2><div class="card-actions"><span class="drag-hint">hover for OHLCV</span></div></div>
      <div class="snap-badge">16 cols</div>
      <div class="card-body">
        <div class="legend" id="market-legend"></div>
        <div class="pillrow" id="timeframe-controls" style="margin-bottom:10px"></div>
        <div class="chart-wrap">
          <canvas id="market-chart"></canvas>
          <div class="chart-overlay">
            <div class="overlay-box" id="hover-ohlcv"><strong>Cursor</strong><span>Move over a candle</span></div>
            <div class="overlay-box" id="latest-candle"><strong>Live candle</strong><span>--</span></div>
          </div>
        </div>
      </div><div class="resize-handle" aria-hidden="true"></div>
    </section>

    <section class="card wide" id="allocation-card" data-default-col="17" data-default-span="8">
      <div class="card-head"><h2>Allocation</h2><div class="card-actions"><span class="drag-hint">drag, resize</span></div></div>
      <div class="snap-badge">8 cols</div>
      <div class="card-body">
        <div class="chart-wrap" style="min-height:260px"><canvas id="allocation-chart"></canvas></div>
        <div class="detail-box" id="allocation-detail"></div>
      </div><div class="resize-handle" aria-hidden="true"></div>
    </section>

    <section class="card wide" id="status-card" data-default-col="1" data-default-span="8"><div class="card-head"><h2>Status</h2><div class="card-actions"><span class="drag-hint">drag</span></div></div><div class="snap-badge">8 cols</div><div class="card-body"><div class="kv-grid" id="status-list"></div></div><div class="resize-handle" aria-hidden="true"></div></section>
    <section class="card wide" id="position-card" data-default-col="9" data-default-span="8"><div class="card-head"><h2>Position</h2><div class="card-actions"><span class="drag-hint">drag</span></div></div><div class="snap-badge">8 cols</div><div class="card-body"><div class="kv-grid" id="position-list"></div></div><div class="resize-handle" aria-hidden="true"></div></section>
    <section class="card wide" id="grid-card" data-default-col="17" data-default-span="8"><div class="card-head"><h2>Grid</h2><div class="card-actions"><span class="drag-hint">drag</span></div></div><div class="snap-badge">8 cols</div><div class="card-body"><div class="kv-grid" id="grid-list"></div></div><div class="resize-handle" aria-hidden="true"></div></section>
    <section class="card wide" id="perf-card" data-default-col="1" data-default-span="8"><div class="card-head"><h2>Performance</h2><div class="card-actions"><span class="drag-hint">drag</span></div></div><div class="snap-badge">8 cols</div><div class="card-body"><div class="kv-grid" id="perf-list"></div></div><div class="resize-handle" aria-hidden="true"></div></section>
    <section class="card wide" id="risk-card" data-default-col="9" data-default-span="8"><div class="card-head"><h2>Risk</h2><div class="card-actions"><span class="drag-hint">drag</span></div></div><div class="snap-badge">8 cols</div><div class="card-body"><div class="kv-grid" id="risk-list"></div></div><div class="resize-handle" aria-hidden="true"></div></section>
    <section class="card wide" id="config-card" data-default-col="17" data-default-span="8"><div class="card-head"><h2>Config</h2><div class="card-actions"><span class="drag-hint">drag</span></div></div><div class="snap-badge">8 cols</div><div class="card-body"><div class="kv-grid" id="config-list"></div></div><div class="resize-handle" aria-hidden="true"></div></section>

    <section class="card events-card" id="events-card" data-default-col="1" data-default-span="12">
      <div class="card-head"><h2>Events</h2><div class="card-actions"><span class="drag-hint">drag, resize</span></div></div>
      <div class="snap-badge">12 cols</div>
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
      </div><div class="resize-handle" aria-hidden="true"></div>
    </section>

    <section class="card orders-card" id="orders-card" data-default-col="13" data-default-span="12">
      <div class="card-head"><h2>Open Orders</h2><div class="card-actions"><span class="drag-hint">drag, resize</span></div></div>
      <div class="snap-badge">12 cols</div>
      <div class="card-body">
        <div class="pager">
          <div class="pager-controls">
            <button class="btn" id="orders-filter-buy-btn" type="button">Buy only</button>
            <button class="btn" id="orders-filter-sell-btn" type="button">Sell only</button>
            <button class="btn" id="orders-first-btn" type="button">First</button>
            <button class="btn" id="orders-prev-btn" type="button">Prev</button>
            <button class="btn" id="orders-next-btn" type="button">Next</button>
            <button class="btn" id="orders-last-btn" type="button">Last</button>
          </div>
          <div class="page-indicator" id="orders-page-indicator">Page 1 / 1</div>
        </div>
        <div class="table-wrap"><table><thead><tr><th>Side</th><th>Price</th><th>Amount</th><th>Total</th></tr></thead><tbody id="orders-body"></tbody></table></div>
      </div><div class="resize-handle" aria-hidden="true"></div>
    </section>
  </div>
</div>
<script>
const GRID_COLS = 24;
const GRID_GAP = 14;
const MIN_SPAN = 6;
const TIMEFRAMES = ['1s', '1m', '5m', '30m', '1h', '1d', '1w', '1M'];
const DEFAULT_LIMITS = { '1s': 240, '1m': 180, '5m': 180, '30m': 180, '1h': 240, '1d': 180, '1w': 120, '1M': 120 };
const stateUi = {
  theme: localStorage.getItem('tradebot-theme') || 'dark',
  selectedEventIndex: 0,
  selectedOrderIndex: 0,
  lastSnapshot: {},
  lastEvents: [],
  eventPage: -1,
  eventPageSize: 5,
  lastOrders: [],
  orderPage: -1,
  orderPageSize: 5,
  orderFilter: 'all',
  lastOhlcv: [],
  drag: null,
  timeframe: localStorage.getItem('tradebot-chart-timeframe') || '1m',
  candleLimit: Number(localStorage.getItem('tradebot-chart-limit') || 180),
  historyOffset: Number(localStorage.getItem('tradebot-chart-history-offset') || 0),
  panOffset: Number(localStorage.getItem('tradebot-chart-pan-offset') || 0),
  visibleOhlcv: [],
};

function setTheme(theme) {
  document.body.classList.toggle('light', theme === 'light');
  stateUi.theme = theme;
  localStorage.setItem('tradebot-theme', theme);
}
setTheme(stateUi.theme);
document.getElementById('theme-toggle').addEventListener('click', () => setTheme(stateUi.theme === 'dark' ? 'light' : 'dark'));
document.getElementById('refresh-btn').addEventListener('click', refresh);
document.getElementById('bot-toggle-btn').addEventListener('click', toggleBotPause);
document.getElementById('events-first-btn').addEventListener('click', () => changeEventPage('first'));
document.getElementById('events-prev-btn').addEventListener('click', () => changeEventPage('prev'));
document.getElementById('events-next-btn').addEventListener('click', () => changeEventPage('next'));
document.getElementById('events-last-btn').addEventListener('click', () => changeEventPage('last'));
document.getElementById('orders-filter-buy-btn').addEventListener('click', () => setOrderFilter('BUY'));
document.getElementById('orders-filter-sell-btn').addEventListener('click', () => setOrderFilter('SELL'));
document.getElementById('orders-first-btn').addEventListener('click', () => changeOrderPage('first'));
document.getElementById('orders-prev-btn').addEventListener('click', () => changeOrderPage('prev'));
document.getElementById('orders-next-btn').addEventListener('click', () => changeOrderPage('next'));
document.getElementById('orders-last-btn').addEventListener('click', () => changeOrderPage('last'));
document.getElementById('zoom-in-btn').addEventListener('click', () => adjustZoom(-1));
document.getElementById('zoom-out-btn').addEventListener('click', () => adjustZoom(1));
document.getElementById('reset-layout-btn').addEventListener('click', () => { localStorage.removeItem(getLayoutKey()); applyDefaultLayout(); saveLayout(); });

function fmtNum(v, digits = 2) { if (v === null || v === undefined || Number.isNaN(Number(v))) return '--'; return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits }); }
function fmtMoney(v) { return v === null || v === undefined ? '--' : `$${fmtNum(v, 2)}`; }
function fmtPct(v) { return v === null || v === undefined ? '--' : `${(Number(v) * 100).toFixed(2)}%`; }
function fmtPrice(v) { return v === null || v === undefined ? '--' : fmtNum(v, 2); }
function fmtDate(v) { if (!v) return '--'; try { return new Date(v).toLocaleString(); } catch { return v; } }
function signedClass(v) { return Number(v) > 0 ? 'positive' : Number(v) < 0 ? 'negative' : ''; }
function humanAge(seconds) { if (seconds == null) return '--'; if (seconds < 60) return `${seconds.toFixed(2)}s`; if (seconds < 3600) return `${(seconds/60).toFixed(1)}m`; return `${(seconds/3600).toFixed(1)}h`; }

function buildKvHtml(label, value, extraClass='', changed=false) {
  return `<div class="kv ${changed ? 'changed' : ''}"><div class="k">${label}</div><div class="v ${extraClass}">${value}</div></div>`;
}

function renderKVs(targetId, rows) {
  const target = document.getElementById(targetId);
  target.innerHTML = rows.map(([k, v, extraClass, changed]) => buildKvHtml(k, v, extraClass, changed)).join('');
}

function normalizeTimeframe(tf) {
  return TIMEFRAMES.includes(tf) ? tf : '1m';
}

function setTimeframe(tf) {
  stateUi.timeframe = normalizeTimeframe(tf);
  stateUi.panOffset = 0;
  stateUi.historyOffset = 0;
  if (!stateUi.candleLimit || stateUi.candleLimit < 30) {
    stateUi.candleLimit = DEFAULT_LIMITS[stateUi.timeframe] || 180;
  }
  localStorage.setItem('tradebot-chart-timeframe', stateUi.timeframe);
  localStorage.setItem('tradebot-chart-pan-offset', '0');
  localStorage.setItem('tradebot-chart-history-offset', '0');
  renderTimeframeControls();
  refresh();
}

function adjustZoom(direction) {
  const current = Number(stateUi.candleLimit || DEFAULT_LIMITS[stateUi.timeframe] || 180);
  const next = direction > 0 ? Math.min(1000, Math.round(current * 1.5)) : Math.max(30, Math.round(current / 1.5));
  stateUi.candleLimit = next;
  stateUi.panOffset = 0;
  stateUi.historyOffset = 0;
  localStorage.setItem('tradebot-chart-limit', String(next));
  localStorage.setItem('tradebot-chart-pan-offset', '0');
  localStorage.setItem('tradebot-chart-history-offset', '0');
  refresh();
}

function panChart(delta) {
  const limit = Math.max(30, Number(stateUi.candleLimit || 180));
  const maxLocalOffset = Math.max(0, (stateUi.lastOhlcv || []).length - limit);
  const nextOffset = stateUi.panOffset + delta;
  if (nextOffset < 0) {
    stateUi.panOffset = 0;
    stateUi.historyOffset = Math.max(0, stateUi.historyOffset + nextOffset);
  } else if (nextOffset > maxLocalOffset) {
    stateUi.panOffset = 0;
    stateUi.historyOffset += Math.max(1, nextOffset - maxLocalOffset);
    refresh();
    return;
  } else {
    stateUi.panOffset = nextOffset;
  }
  localStorage.setItem('tradebot-chart-pan-offset', String(stateUi.panOffset));
  localStorage.setItem('tradebot-chart-history-offset', String(stateUi.historyOffset));
  drawCandles(stateUi.lastOhlcv || []);
}

function renderTimeframeControls() {
  const el = document.getElementById('timeframe-controls');
  el.innerHTML = TIMEFRAMES.map(tf => `<button class="btn ${stateUi.timeframe === tf ? 'active-timeframe' : ''}" type="button" data-tf="${tf}">${tf}</button>`).join('');
  el.querySelectorAll('[data-tf]').forEach(btn => btn.addEventListener('click', () => setTimeframe(btn.dataset.tf)));
}

function getChanged(path, value) {
  const old = stateUi.lastSnapshot[path];
  stateUi.lastSnapshot[path] = value;
  return old !== undefined && old !== value;
}

function renderStickySummary(status, cumulative, grid) {
  const target = document.getElementById('sticky-summary');
  const items = [
    ['Price', fmtPrice(status.price), getChanged('summary.price', status.price)],
    ['Equity', fmtMoney(status.equityUsdt), getChanged('summary.equity', status.equityUsdt)],
    ['USDT', fmtMoney(status.usdt), getChanged('summary.usdt', status.usdt)],
    ['BTC', fmtNum(status.btc, 6), getChanged('summary.btc', status.btc)],
    ['Open Orders', grid.openOrders ?? '--', getChanged('summary.orders', grid.openOrders)],
    ['Trades', cumulative.trades ?? '--', getChanged('summary.trades', cumulative.trades)]
  ];
  target.innerHTML = items.map(([label, value, changed]) => `<div class="metric ${changed ? 'changed' : ''}"><div class="label">${label}</div><div class="value">${value}</div></div>`).join('');
}

async function toggleBotPause() {
  const btn = document.getElementById('bot-toggle-btn');
  btn.disabled = true;
  try {
    const shouldPause = !(stateUi.lastState && stateUi.lastState.paused);
    const res = await fetch('/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paused: shouldPause }),
    });
    if (!res.ok) throw new Error(`control failed: ${res.status}`);
    await refresh();
  } catch (err) {
    console.error(err);
  } finally {
    btn.disabled = false;
  }
}

function changeEventPage(direction) {
  const events = stateUi.lastEvents || [];
  const totalPages = Math.max(1, Math.ceil(events.length / stateUi.eventPageSize));
  if (stateUi.eventPage < 0) stateUi.eventPage = totalPages - 1;
  if (direction === 'first') stateUi.eventPage = 0;
  if (direction === 'last') stateUi.eventPage = totalPages - 1;
  if (direction === 'prev') stateUi.eventPage = Math.max(0, stateUi.eventPage - 1);
  if (direction === 'next') stateUi.eventPage = Math.min(totalPages - 1, stateUi.eventPage + 1);
  renderEvents(events);
}

function renderEvents(events) {
  const ordered = (events || []).slice().reverse();
  stateUi.lastEvents = ordered;
  const totalPages = Math.max(1, Math.ceil(ordered.length / stateUi.eventPageSize));
  if (stateUi.eventPage < 0 || stateUi.eventPage >= totalPages) stateUi.eventPage = totalPages - 1;
  const start = stateUi.eventPage * stateUi.eventPageSize;
  const pageRows = ordered.slice(start, start + stateUi.eventPageSize);
  const body = document.getElementById('events-body');
  body.innerHTML = '';
  pageRows.forEach((ev) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${fmtDate(ev.tsUtc)}</td><td>${ev.event || '--'}</td><td>${fmtPrice(ev.price)}</td><td>${fmtNum(ev.qtyBtc, 6)}</td>`;
    body.appendChild(tr);
  });
  document.getElementById('events-page-indicator').textContent = `Page ${stateUi.eventPage + 1} / ${totalPages}`;
  document.getElementById('events-first-btn').disabled = stateUi.eventPage <= 0;
  document.getElementById('events-prev-btn').disabled = stateUi.eventPage <= 0;
  document.getElementById('events-next-btn').disabled = stateUi.eventPage >= totalPages - 1;
  document.getElementById('events-last-btn').disabled = stateUi.eventPage >= totalPages - 1;
}

function setOrderFilter(side) {
  const normalized = side === 'BUY' || side === 'SELL' ? side : 'all';
  stateUi.orderFilter = stateUi.orderFilter === normalized ? 'all' : normalized;
  stateUi.orderPage = -1;
  renderOrders(stateUi.lastOrders || [], stateUi.lastOrderGrid || {});
}

function getFilteredOrders(orders) {
  if (stateUi.orderFilter === 'BUY') return (orders || []).filter(order => order.side === 'BUY');
  if (stateUi.orderFilter === 'SELL') return (orders || []).filter(order => order.side === 'SELL');
  return (orders || []).slice();
}

function changeOrderPage(direction) {
  const orders = getFilteredOrders(stateUi.lastOrders || []);
  const totalPages = Math.max(1, Math.ceil(orders.length / stateUi.orderPageSize));
  if (stateUi.orderPage < 0) stateUi.orderPage = totalPages - 1;
  if (direction === 'first') stateUi.orderPage = 0;
  if (direction === 'last') stateUi.orderPage = totalPages - 1;
  if (direction === 'prev') stateUi.orderPage = Math.max(0, stateUi.orderPage - 1);
  if (direction === 'next') stateUi.orderPage = Math.min(totalPages - 1, stateUi.orderPage + 1);
  renderOrders(stateUi.lastOrders || [], stateUi.lastOrderGrid || {});
}

function renderOrders(orders, grid) {
  const ordered = (orders || []).slice();
  stateUi.lastOrders = ordered;
  stateUi.lastOrderGrid = grid || {};
  const filtered = getFilteredOrders(ordered);
  const totalPages = Math.max(1, Math.ceil(filtered.length / stateUi.orderPageSize));
  if (stateUi.orderPage < 0 || stateUi.orderPage >= totalPages) stateUi.orderPage = totalPages - 1;
  const start = stateUi.orderPage * stateUi.orderPageSize;
  const pageRows = filtered.slice(start, start + stateUi.orderPageSize);
  const body = document.getElementById('orders-body');
  body.innerHTML = '';
  pageRows.forEach((order) => {
    const total = Number(order.qty_btc || 0) * Number(order.price || 0);
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${order.side || '--'}</td><td>${fmtPrice(order.price)}</td><td>${fmtNum(order.qty_btc, 6)}</td><td>${fmtMoney(total)}</td>`;
    body.appendChild(tr);
  });
  document.getElementById('orders-page-indicator').textContent = `Page ${stateUi.orderPage + 1} / ${totalPages}`;
  document.getElementById('orders-first-btn').disabled = stateUi.orderPage <= 0;
  document.getElementById('orders-prev-btn').disabled = stateUi.orderPage <= 0;
  document.getElementById('orders-next-btn').disabled = stateUi.orderPage >= totalPages - 1;
  document.getElementById('orders-last-btn').disabled = stateUi.orderPage >= totalPages - 1;
  document.getElementById('orders-filter-buy-btn').classList.toggle('active-timeframe', stateUi.orderFilter === 'BUY');
  document.getElementById('orders-filter-sell-btn').classList.toggle('active-timeframe', stateUi.orderFilter === 'SELL');
}

function drawAllocation(usdt, btcValue, total) {
  const canvas = document.getElementById('allocation-chart');
  const ctx = canvas.getContext('2d');
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;
  ctx.scale(devicePixelRatio, devicePixelRatio);
  ctx.clearRect(0, 0, rect.width, rect.height);
  const cx = rect.width / 2, cy = rect.height / 2, r = Math.min(rect.width, rect.height) * 0.34;
  const frac = total > 0 ? usdt / total : 0;
  ctx.lineWidth = 26;
  ctx.strokeStyle = '#29d391';
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeStyle = '#5ea6ff';
  ctx.beginPath(); ctx.arc(cx, cy, r, -Math.PI/2, -Math.PI/2 + Math.PI * 2 * frac); ctx.stroke();
  ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--text');
  ctx.font = '600 16px Inter, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('Allocation', cx, cy - 8);
  ctx.font = '700 20px Inter, sans-serif';
  ctx.fillText(fmtMoney(total), cx, cy + 18);
  document.getElementById('allocation-detail').textContent = `USDT cash: ${fmtMoney(usdt)}\nBTC exposure: ${fmtMoney(btcValue)}\nTotal equity: ${fmtMoney(total)}`;
}

function getVisibleOhlcv(allOhlcv) {
  const rows = allOhlcv || [];
  const limit = Math.max(30, Number(stateUi.candleLimit || DEFAULT_LIMITS[stateUi.timeframe] || 180));
  const maxOffset = Math.max(0, rows.length - limit);
  stateUi.panOffset = Math.max(0, Math.min(maxOffset, Number(stateUi.panOffset || 0)));
  const end = rows.length - stateUi.panOffset;
  const start = Math.max(0, end - limit);
  const visible = rows.slice(start, end);
  stateUi.visibleOhlcv = visible;
  return visible;
}

function drawCandles(ohlcv) {
  const allRows = (ohlcv || []).map(row => ({ ...row }));
  const livePrice = Number((stateUi.lastStatus || {}).price);
  if (allRows.length && Number.isFinite(livePrice)) {
    const last = allRows[allRows.length - 1];
    last.close = livePrice;
    last.high = Math.max(Number(last.high || livePrice), livePrice);
    last.low = Math.min(Number(last.low || livePrice), livePrice);
  }
  const visibleRows = getVisibleOhlcv(allRows);
  const canvas = document.getElementById('market-chart');
  const ctx = canvas.getContext('2d');
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;
  ctx.scale(devicePixelRatio, devicePixelRatio);
  ctx.clearRect(0, 0, rect.width, rect.height);
  if (!visibleRows.length) return;

  const priceArea = rect.height * 0.76;
  const volumeTop = priceArea + 10;
  const padL = 60, padR = 20, padT = 16, padB = 24;
  const highs = visibleRows.map(c => c.high);
  const lows = visibleRows.map(c => c.low);
  const vols = visibleRows.map(c => c.volumeUsdt);
  const maxP = Math.max(...highs), minP = Math.min(...lows), spanP = Math.max(1e-9, maxP - minP);
  const maxV = Math.max(...vols, 1);
  const chartW = rect.width - padL - padR;
  const candleGap = chartW / Math.max(visibleRows.length, 1);
  const candleW = Math.max(5, candleGap * 0.62);

  const bgGrad = ctx.createLinearGradient(0, 0, 0, priceArea);
  bgGrad.addColorStop(0, 'rgba(94,166,255,0.08)');
  bgGrad.addColorStop(1, 'rgba(94,166,255,0.01)');
  ctx.fillStyle = bgGrad;
  ctx.fillRect(padL, padT, chartW, priceArea - padT - padB);

  ctx.strokeStyle = 'rgba(255,255,255,.06)';
  ctx.lineWidth = 1;
  for (let i = 0; i < 6; i++) {
    const y = padT + ((priceArea - padT - padB) * i / 5);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(rect.width - padR, y); ctx.stroke();
    const price = maxP - ((maxP - minP) * i / 5);
    ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--muted');
    ctx.font = '12px Inter';
    ctx.fillText(fmtPrice(price), 8, y + 4);
  }
  for (let i = 0; i <= visibleRows.length; i += Math.max(1, Math.floor(visibleRows.length / 6))) {
    const x = padL + candleGap * i;
    ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, priceArea - padB); ctx.stroke();
  }

  const hoverState = canvas.__hoverIndex;
  visibleRows.forEach((c, i) => {
    const x = padL + candleGap * (i + 0.5);
    const yHigh = padT + (maxP - c.high) / spanP * (priceArea - padT - padB);
    const yLow = padT + (maxP - c.low) / spanP * (priceArea - padT - padB);
    const yOpen = padT + (maxP - c.open) / spanP * (priceArea - padT - padB);
    const yClose = padT + (maxP - c.close) / spanP * (priceArea - padT - padB);
    const up = c.close >= c.open;
    const color = up ? '#22c55e' : '#f43f5e';
    const wick = up ? 'rgba(34,197,94,.95)' : 'rgba(244,63,94,.95)';
    ctx.strokeStyle = wick;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, yHigh); ctx.lineTo(x, yLow); ctx.stroke();
    const top = Math.min(yOpen, yClose), body = Math.max(2, Math.abs(yClose - yOpen));
    const grad = ctx.createLinearGradient(0, top, 0, top + body);
    grad.addColorStop(0, up ? 'rgba(52,211,153,.98)' : 'rgba(251,113,133,.98)');
    grad.addColorStop(1, up ? 'rgba(22,163,74,.98)' : 'rgba(225,29,72,.98)');
    ctx.fillStyle = grad;
    ctx.fillRect(x - candleW / 2, top, candleW, body);
    ctx.strokeStyle = up ? 'rgba(167,243,208,.34)' : 'rgba(254,205,211,.34)';
    ctx.strokeRect(x - candleW / 2, top, candleW, body);
    const volH = (c.volumeUsdt / maxV) * (rect.height - volumeTop - 18);
    ctx.globalAlpha = 0.25;
    ctx.fillStyle = color;
    ctx.fillRect(x - candleW / 2, rect.height - volH - 8, candleW, volH);
    ctx.globalAlpha = 1;
    if (hoverState === i) {
      ctx.strokeStyle = '#f7b955';
      ctx.lineWidth = 1.2;
      ctx.strokeRect(x - candleW / 2 - 2, top - 2, candleW + 4, body + 4);
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, rect.height - 8); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(padL, yClose); ctx.lineTo(rect.width - padR, yClose); ctx.stroke();
      ctx.lineWidth = 1;
    }
  });

  const latest = visibleRows[visibleRows.length - 1];
  const displayClose = Number.isFinite(livePrice) ? livePrice : latest.close;
  const liveTag = Number.isFinite(livePrice) ? ' • live' : '';
  document.getElementById('latest-candle').innerHTML = `<strong>Live candle</strong><span>O ${fmtPrice(latest.open)}  H ${fmtPrice(latest.high)}  L ${fmtPrice(latest.low)}  C ${fmtPrice(displayClose)}${liveTag}  Vol ${fmtMoney(latest.volumeUsdt)}</span>`;
  document.getElementById('market-legend').innerHTML = `<span><b>${latest.symbol || 'BTC/USDT'}</b></span><span>Open ${fmtPrice(latest.open)}</span><span>High ${fmtPrice(latest.high)}</span><span>Low ${fmtPrice(latest.low)}</span><span>Close ${fmtPrice(displayClose)}${liveTag}</span><span>Volume ${fmtMoney(latest.volumeUsdt)}</span>`;

  canvas.onmousemove = (ev) => {
    const r = canvas.getBoundingClientRect();
    const x = ev.clientX - r.left;
    const idx = Math.max(0, Math.min(visibleRows.length - 1, Math.floor((x - padL) / Math.max(1, candleGap))));
    canvas.__hoverIndex = idx;
    const c = visibleRows[idx];
    document.getElementById('hover-ohlcv').innerHTML = `<strong>Cursor</strong><span>${new Date(c.openTimeMs).toLocaleString()}  O ${fmtPrice(c.open)}  H ${fmtPrice(c.high)}  L ${fmtPrice(c.low)}  C ${fmtPrice(c.close)}  Vol ${fmtMoney(c.volumeUsdt)}</span>`;
    drawCandles(allRows);
  };
  canvas.onmouseleave = () => {
    canvas.__hoverIndex = null;
    document.getElementById('hover-ohlcv').innerHTML = '<strong>Cursor</strong><span>Move over a candle</span>';
    drawCandles(allRows);
  };
  canvas.onwheel = (ev) => {
    ev.preventDefault();
    adjustZoom(ev.deltaY > 0 ? 1 : -1);
  };
  let panStartX = null;
  canvas.onmousedown = (ev) => { panStartX = ev.clientX; };
  canvas.onmouseup = () => { panStartX = null; };
  canvas.onmouseleave = () => {
    if (panStartX !== null) panStartX = null;
    canvas.__hoverIndex = null;
    document.getElementById('hover-ohlcv').innerHTML = '<strong>Cursor</strong><span>Move over a candle</span>';
    drawCandles(allRows);
  };
  canvas.onmousemove = (ev) => {
    if (panStartX !== null && Math.abs(ev.clientX - panStartX) > 8) {
      const step = Math.round((panStartX - ev.clientX) / Math.max(8, candleGap));
      if (step !== 0) {
        panStartX = ev.clientX;
        panChart(step);
        return;
      }
    }
    const r = canvas.getBoundingClientRect();
    const x = ev.clientX - r.left;
    const idx = Math.max(0, Math.min(visibleRows.length - 1, Math.floor((x - padL) / Math.max(1, candleGap))));
    canvas.__hoverIndex = idx;
    const c = visibleRows[idx];
    document.getElementById('hover-ohlcv').innerHTML = `<strong>Cursor</strong><span>${new Date(c.openTimeMs).toLocaleString()}  O ${fmtPrice(c.open)}  H ${fmtPrice(c.high)}  L ${fmtPrice(c.low)}  C ${fmtPrice(c.close)}  Vol ${fmtMoney(c.volumeUsdt)}</span>`;
    drawCandles(allRows);
  };
}

function getLayoutKey() { return 'tradebot-layout-v3'; }

function updateSnapBadge(card) {
  const badge = card.querySelector('.snap-badge');
  if (!badge) return;
  badge.textContent = `${card.dataset.span || card.dataset.defaultSpan || 8} cols`;
}

function saveLayout() {
  const cards = [...document.querySelectorAll('.card')].map(card => ({
    id: card.id,
    order: [...card.parentElement.children].indexOf(card),
    span: card.dataset.span || card.dataset.defaultSpan || '8',
  }));
  localStorage.setItem(getLayoutKey(), JSON.stringify(cards));
}

function applyCardSpan(card, span) {
  const nextSpan = Math.max(MIN_SPAN, Math.min(GRID_COLS, Number(span) || Number(card.dataset.defaultSpan || 8)));
  card.dataset.span = String(nextSpan);
  card.style.gridColumn = `span ${nextSpan}`;
  updateSnapBadge(card);
}

function applyDefaultLayout() {
  localStorage.removeItem(getLayoutKey());
  const dashboard = document.getElementById('dashboard');
  [...document.querySelectorAll('.card')].sort((a, b) => Number(a.dataset.defaultCol || 1) - Number(b.dataset.defaultCol || 1)).forEach(card => {
    dashboard.appendChild(card);
    applyCardSpan(card, card.dataset.defaultSpan || 8);
  });
}

function loadLayout() {
  const raw = localStorage.getItem(getLayoutKey());
  if (!raw) {
    applyDefaultLayout();
    return;
  }
  try {
    const cards = JSON.parse(raw);
    const dashboard = document.getElementById('dashboard');
    cards.sort((a, b) => a.order - b.order).forEach(cfg => {
      const card = document.getElementById(cfg.id);
      if (!card) return;
      dashboard.appendChild(card);
      applyCardSpan(card, cfg.span || card.dataset.defaultSpan || 8);
    });
    [...document.querySelectorAll('.card')].forEach(card => { if (!card.dataset.span) applyCardSpan(card, card.dataset.defaultSpan || 8); });
  } catch {
    applyDefaultLayout();
  }
}

function pointerClientXY(ev) {
  const p = ev.touches?.[0] || ev.changedTouches?.[0] || ev;
  return { x: p.clientX, y: p.clientY };
}

function midpointOfCard(card) {
  const rect = card.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}

function findReorderTarget(cards, dragCard, x, y) {
  let best = null;
  let bestDistance = Infinity;
  cards.forEach(card => {
    if (card === dragCard) return;
    const mid = midpointOfCard(card);
    const dx = mid.x - x;
    const dy = mid.y - y;
    const dist = Math.hypot(dx, dy);
    if (dist < bestDistance) {
      best = card;
      bestDistance = dist;
    }
  });
  return best;
}

function reorderCardBefore(dashboard, dragCard, targetCard, pointerX) {
  if (!targetCard || targetCard === dragCard) return false;
  const rect = targetCard.getBoundingClientRect();
  const insertAfter = pointerX > rect.left + rect.width / 2;
  const referenceNode = insertAfter ? targetCard.nextSibling : targetCard;
  if (referenceNode === dragCard || (insertAfter && targetCard.nextSibling === dragCard)) return false;
  dashboard.insertBefore(dragCard, referenceNode);
  return true;
}

function enableDrag() {
  const dashboard = document.getElementById('dashboard');
  document.querySelectorAll('.card').forEach(card => {
    const head = card.querySelector('.card-head');
    const handle = card.querySelector('.resize-handle');
    applyCardSpan(card, card.dataset.span || card.dataset.defaultSpan || 8);
    card.classList.add('reflowing');

    const beginDragSession = (startEvent, start) => {
      const cards = [...dashboard.querySelectorAll('.card')];
      let activeTarget = null;
      card.classList.add('dragging');

      const move = e => {
        const point = pointerClientXY(e);
        const dx = point.x - start.x;
        const dy = point.y - start.y;
        card.style.transform = `translate(${dx}px, ${dy}px) scale(1.02)`;

        const over = findReorderTarget(cards, card, point.x, point.y);
        if (activeTarget !== over) {
          cards.forEach(c => c.classList.remove('drop-target'));
          activeTarget = over;
          if (activeTarget) activeTarget.classList.add('drop-target');
        }
        if (activeTarget) reorderCardBefore(dashboard, card, activeTarget, point.x);
        if (e.cancelable) e.preventDefault();
      };

      const up = () => {
        cards.forEach(c => c.classList.remove('drop-target'));
        card.classList.remove('dragging');
        card.style.transform = '';
        saveLayout();
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
        window.removeEventListener('touchmove', move);
        window.removeEventListener('touchend', up);
      };

      window.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
      window.addEventListener('touchmove', move, { passive: false });
      window.addEventListener('touchend', up);
    };

    const startDrag = ev => {
      if (ev.target.closest('.resize-handle') || ev.target.closest('button') || ev.target.closest('a') || ev.target.closest('input') || ev.target.closest('select') || ev.target.closest('textarea')) return;
      if (ev.type === 'touchstart') {
        const start = pointerClientXY(ev);
        let dragging = false;
        const touchMove = moveEv => {
          const point = pointerClientXY(moveEv);
          if (!dragging && Math.hypot(point.x - start.x, point.y - start.y) > 10) {
            dragging = true;
            beginDragSession(ev, start);
          }
          if (dragging && moveEv.cancelable) moveEv.preventDefault();
        };
        const touchEnd = () => {
          window.removeEventListener('touchmove', touchMove);
          window.removeEventListener('touchend', touchEnd);
        };
        window.addEventListener('touchmove', touchMove, { passive: false });
        window.addEventListener('touchend', touchEnd);
        return;
      }
      ev.preventDefault();
      beginDragSession(ev, pointerClientXY(ev));
    };

    head.addEventListener('mousedown', startDrag);
    head.addEventListener('touchstart', startDrag, { passive: true });

    handle?.addEventListener('mousedown', ev => {
      ev.preventDefault();
      ev.stopPropagation();
      const startX = ev.clientX;
      const startSpan = Number(card.dataset.span || card.dataset.defaultSpan || 8);
      const dashboardRect = dashboard.getBoundingClientRect();
      const colWidth = (dashboardRect.width - GRID_GAP * (GRID_COLS - 1)) / GRID_COLS;
      function move(e) {
        const deltaCols = Math.round((e.clientX - startX) / Math.max(1, colWidth + GRID_GAP));
        applyCardSpan(card, startSpan + deltaCols);
      }
      function up() {
        saveLayout();
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
      }
      window.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
    });
  });
}

async function refresh() {
  const tf = encodeURIComponent(normalizeTimeframe(stateUi.timeframe));
  const limit = Math.max(30, Math.min(1000, Number(stateUi.candleLimit || DEFAULT_LIMITS[stateUi.timeframe] || 180)));
  const offset = Math.max(0, Number(stateUi.historyOffset || 0));
  const res = await fetch(`/api/dashboard?interval=${tf}&limit=${limit}&offset=${offset}`, { cache: 'no-store' });
  const data = await res.json();
  const { status, state, runtime, cumulative, events, freshnessSeconds, serverTimeUtc, ohlcv } = data;
  const grid = { ...((status.stats || {}).grid || {}), openOrders: ((runtime.grid || {}).orders || []).length };
  const pos = status.position || {};
  stateUi.lastState = state;
  stateUi.lastStatus = status;
  const botBtn = document.getElementById('bot-toggle-btn');
  botBtn.textContent = state.paused ? 'Play bot' : 'Pause bot';
  botBtn.classList.toggle('paused', !!state.paused);
  botBtn.classList.toggle('playing', !state.paused);

  const activeInterval = normalizeTimeframe(data.chartInterval || stateUi.timeframe || status.interval || '1m');
  stateUi.timeframe = activeInterval;
  stateUi.lastOhlcv = ohlcv || [];
  localStorage.setItem('tradebot-chart-timeframe', activeInterval);
  document.getElementById('pill-symbol').textContent = status.symbol || '--';
  document.getElementById('pill-interval').textContent = activeInterval;
  document.getElementById('server-time').textContent = fmtDate(serverTimeUtc);
  document.getElementById('fresh-label').textContent = freshnessSeconds != null ? `Live payload • ${humanAge(freshnessSeconds)}` : 'No timestamp';

  renderStickySummary(status, cumulative, grid);
  const ai = ((status.stats || {}).ai || {});
  const gridSkipReason = (grid.skipped || status.lastEvent === 'AI_SKIP' || status.lastEvent === 'GRID_SKIP')
    ? (grid.skipReason || (ai.gridAllowed === false ? 'ai_grid_disallowed' : null) || status.lastEvent)
    : null;

  renderKVs('status-list', [
    ['Paused', state.paused ? 'Yes' : 'No', '', getChanged('status.paused', state.paused)],
    ['Last event', status.lastEvent || '--', '', getChanged('status.lastEvent', status.lastEvent)],
    ['Grid status', gridSkipReason ? `Waiting (${gridSkipReason})` : 'Active / eligible', '', getChanged('status.gridreason', gridSkipReason || 'active')],
    ['AI confidence', ai.confidence != null ? fmtPct(ai.confidence) : '--', '', getChanged('status.aiconf', ai.confidence)],
    ['Status timestamp', fmtDate(status.tsUtc), '', getChanged('status.tsUtc', status.tsUtc)],
    ['Runtime saved', fmtDate(runtime.savedAt), '', getChanged('runtime.savedAt', runtime.savedAt)],
    ['Freshness', humanAge(freshnessSeconds), '', getChanged('freshness', Math.round((freshnessSeconds || 0) * 10) / 10)]
  ]);
  renderKVs('position-list', status.position ? [
    ['Entry price', fmtPrice(pos.entryPrice), '', getChanged('pos.entry', pos.entryPrice)],
    ['Quantity BTC', fmtNum(pos.qtyBtc, 6), '', getChanged('pos.qty', pos.qtyBtc)],
    ['Unrealized PnL', fmtMoney(pos.unrealizedPnlUsdt), signedClass(pos.unrealizedPnlUsdt), getChanged('pos.pnl', pos.unrealizedPnlUsdt)],
    ['Unrealized PnL %', fmtPct(pos.unrealizedPnlPct), signedClass(pos.unrealizedPnlPct), getChanged('pos.pnlpct', pos.unrealizedPnlPct)],
    ['Trail stop', pos.stop ? fmtPrice(pos.stop) : 'Not armed', '', getChanged('pos.stop', pos.stop)],
    ['Entry time', fmtDate(pos.entryTimeUtc), '', getChanged('pos.time', pos.entryTimeUtc)]
  ] : [['Position', 'No open inventory', '', false]]);
  renderKVs('grid-list', [
    ['Mode', grid.mode || state.gridMode || '--', '', getChanged('grid.mode', grid.mode || state.gridMode)],
    ['Spacing', fmtPct(grid.spacingPct), '', getChanged('grid.spacing', grid.spacingPct)],
    ['Levels', grid.levels ?? '--', '', getChanged('grid.levels', grid.levels)],
    ['Open orders', grid.openOrders ?? '--', '', getChanged('grid.orders', grid.openOrders)],
    ['Reserved USDT', fmtMoney(runtime.grid?.reserved_usdt), '', getChanged('grid.reserved_usdt', runtime.grid?.reserved_usdt)],
    ['Reserved BTC', fmtNum(runtime.grid?.reserved_btc, 6), '', getChanged('grid.reserved_btc', runtime.grid?.reserved_btc)]
  ]);
  renderKVs('perf-list', [
    ['Equity', fmtMoney(status.equityUsdt), signedClass(status.equityUsdt), getChanged('perf.equity', status.equityUsdt)],
    ['Session PnL', fmtMoney((status.stats || {}).pnlUsdt), signedClass((status.stats || {}).pnlUsdt), getChanged('perf.pnl', (status.stats || {}).pnlUsdt)],
    ['Trades', cumulative.trades ?? 0, '', getChanged('perf.trades', cumulative.trades)],
    ['Wins', cumulative.wins ?? 0, '', getChanged('perf.wins', cumulative.wins)],
    ['Losses', cumulative.losses ?? 0, '', getChanged('perf.losses', cumulative.losses)],
    ['Peak equity', fmtMoney(runtime.stats?.peak_equity), '', getChanged('perf.peak', runtime.stats?.peak_equity)]
  ]);
  renderKVs('risk-list', [
    ['Allow live orders', state.allowLiveOrders ? 'Yes' : 'No', '', getChanged('risk.live', state.allowLiveOrders)],
    ['Max daily loss', fmtPct(state.maxDailyLossPct), '', getChanged('risk.maxloss', state.maxDailyLossPct)],
    ['Max drawdown', fmtPct((status.stats || {}).maxDrawdownPct), '', getChanged('risk.dd', (status.stats || {}).maxDrawdownPct)],
    ['Cooldown until', runtime.stats?.cooldown_until ? fmtDate(runtime.stats.cooldown_until) : 'None', '', getChanged('risk.cooldown', runtime.stats?.cooldown_until)],
    ['Trend strength', fmtPct((status.stats || {}).trendStrength), '', getChanged('risk.trend', (status.stats || {}).trendStrength)]
  ]);
  renderKVs('config-list', [
    ['Symbol', state.symbol || '--', '', getChanged('cfg.symbol', state.symbol)],
    ['Interval', state.interval || '--', '', getChanged('cfg.interval', state.interval)],
    ['Fee (bps)', state.feeBps ?? '--', '', getChanged('cfg.fee', state.feeBps)],
    ['Paper limit slippage', state.paperLimitSlipBps ?? 3, '', getChanged('cfg.limitslip', state.paperLimitSlipBps)],
    ['Paper market slippage', state.paperMarketSlipBps ?? 12, '', getChanged('cfg.marketslip', state.paperMarketSlipBps)],
    ['Grid max exposure', fmtPct(state.gridMaxExposurePct), '', getChanged('cfg.maxexpo', state.gridMaxExposurePct)]
  ]);

  drawAllocation(Number(status.usdt || 0), Number(status.btc || 0) * Number(status.price || 0), Number(status.equityUsdt || 0));
  renderEvents(events || []);
  renderOrders((runtime.grid || {}).orders || [], runtime.grid || {});
  drawCandles(ohlcv || []);
  renderTimeframeControls();
}

loadLayout();
enableDrag();
renderTimeframeControls();
setInterval(refresh, 1000);
refresh();
</script>
</body>
</html>'''


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
    tmp.replace(path)


def read_events() -> list[dict]:
    if not TRADES_PATH.exists():
        return []
    rows = []
    try:
        with TRADES_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return rows[-500:]


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
    history = read_json(HISTORY_PATH) or {}
    items = deque(history.get("items", []), maxlen=5000)
    _backfill_history_from_logs(items)
    price = status.get("price")
    equity = status.get("equityUsdt")
    ts = status.get("tsUtc")
    if isinstance(price, (int, float)) and isinstance(equity, (int, float)) and ts:
        if not items or items[-1].get("ts") != ts:
            items.append({"ts": ts, "price": price, "equity": equity})
    payload = {"items": list(items)}
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


def get_ohlcv(symbol: str, interval: str, limit: int = 120, offset: int = 0) -> list[dict]:
    interval = normalize_interval(interval)
    limit = max(30, min(MAX_OHLCV_LIMIT, int(limit)))
    offset = max(0, int(offset))
    key = (symbol, interval, limit, offset)
    if key in _ohlcv_cache:
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
        return payload
    except Exception:
        return _ohlcv_cache.get(key, [])


class Handler(BaseHTTPRequestHandler):
    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get('Content-Length', '0') or '0')
        except Exception:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path == '/api/control':
                body = self._read_json_body()
                state = read_json(STATE_PATH)
                state['paused'] = bool(body.get('paused'))
                write_json(STATE_PATH, state)
                payload = {'ok': True, 'paused': state['paused']}
                self._send(200, json.dumps(payload).encode('utf-8'), 'application/json; charset=utf-8')
                return
            self._send(404, b'Not found', 'text/plain; charset=utf-8')
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self._send(500, json.dumps({'error': str(e)}).encode('utf-8'), 'application/json; charset=utf-8')
            except Exception:
                pass

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/dashboard":
                qs = parse_qs(parsed.query)
                status = read_json(STATUS_PATH)
                state = read_json(STATE_PATH)
                runtime = read_json(RUNTIME_PATH)
                cumulative = read_json(CUM_PATH)
                symbol = status.get("symbol") or state.get("symbol", "BTCUSDT")
                requested_interval = normalize_interval(qs.get("interval", [status.get("interval") or state.get("interval", "1m")])[0])
                raw_limit = qs.get("limit", [SUPPORTED_INTERVALS[requested_interval]["default_limit"]])[0]
                raw_offset = qs.get("offset", [0])[0]
                try:
                    limit = max(30, min(MAX_OHLCV_LIMIT, int(raw_limit)))
                except Exception:
                    limit = SUPPORTED_INTERVALS[requested_interval]["default_limit"]
                try:
                    offset = max(0, int(raw_offset))
                except Exception:
                    offset = 0
                live_price = status.get('price')
                payload = {
                    "status": status,
                    "state": state,
                    "runtime": runtime,
                    "cumulative": cumulative,
                    "events": read_events(),
                    "history": update_history(status),
                    "ohlcv": merge_live_price_into_ohlcv(get_ohlcv(symbol, requested_interval, limit=limit, offset=offset), live_price),
                    "chartInterval": requested_interval,
                    "chartLimit": limit,
                    "chartOffset": offset,
                    "supportedIntervals": list(SUPPORTED_INTERVALS.keys()),
                    "freshnessSeconds": freshness_seconds(status.get("tsUtc")),
                    "serverTimeUtc": datetime.now(timezone.utc).isoformat(),
                }
                self._send(200, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
                return
            if parsed.path == "/health":
                self._send(200, b'{"status":"ok"}', "application/json; charset=utf-8")
                return
            self._send(404, b'Not found', 'text/plain; charset=utf-8')
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self._send(500, json.dumps({"error": str(e)}).encode("utf-8"), "application/json; charset=utf-8")
            except Exception:
                pass

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Dashboard listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
