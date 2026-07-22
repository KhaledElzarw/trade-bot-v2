/**
 * Multi-wallet dashboard (v2).
 *
 * Security posture (closes A14):
 *  - NO innerHTML / outerHTML / insertAdjacentHTML / document.write anywhere.
 *    All untrusted values are rendered with textContent or safe DOM nodes.
 *  - Links are parsed with `URL` and only https: (plus explicitly allowed
 *    loopback http:) are permitted; javascript:/data:/file: are rejected.
 *  - No model-endpoint or allowlist editing exists in this UI (A09).
 *
 * Truthfulness: empty / loading / stale / degraded / error states are rendered
 * as themselves. Nothing is fabricated when data is missing.
 */

'use strict';

const API = '/api/v2';

/* ---------------------------------------------------------------- utilities */

/** Safe element factory. `text` is always set via textContent. */
function el(tag, opts = {}) {
  const node = document.createElement(tag);
  if (opts.className) node.className = opts.className;
  if (opts.text !== undefined && opts.text !== null) node.textContent = String(opts.text);
  if (opts.attrs) {
    for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, String(v));
  }
  if (opts.children) {
    for (const child of opts.children) if (child) node.appendChild(child);
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/**
 * Parse and vet a URL. Returns a safe href string or null.
 * Only https: is allowed, plus http: on loopback for the local dashboard.
 */
function safeUrl(raw) {
  if (typeof raw !== 'string' || raw === '') return null;
  let url;
  try {
    url = new URL(raw, window.location.origin);
  } catch {
    return null;
  }
  if (url.protocol === 'https:') return url.href;
  if (url.protocol === 'http:') {
    const host = url.hostname;
    if (host === 'localhost' || host === '127.0.0.1' || host === '::1') return url.href;
  }
  return null; // javascript:, data:, file:, blob:, and everything else
}

/** Build an anchor only when the URL passes vetting; otherwise plain text. */
function safeLink(raw, label) {
  const href = safeUrl(raw);
  if (href === null) return el('span', { text: label });
  const a = el('a', { text: label, attrs: { href, rel: 'noopener noreferrer', target: '_blank' } });
  return a;
}

async function getJson(path) {
  const res = await fetch(`${API}${path}`, { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/* ------------------------------------------------------------ state banners */

function stateNode(kind, message) {
  const roles = { error: 'alert', stale: 'status', degraded: 'status' };
  return el('p', {
    className: `state state--${kind}`,
    text: message,
    attrs: { role: roles[kind] || 'status', 'data-state': kind },
  });
}

const emptyNode = (m) => stateNode('empty', m || 'No data yet.');
const loadingNode = () => stateNode('loading', 'Loading…');
const errorNode = (m) => stateNode('error', m || 'Could not load data.');

/* ----------------------------------------------------------------- summary */

function renderSummary(root, data) {
  clear(root);
  if (!data) { root.appendChild(emptyNode('Portfolio summary unavailable.')); return; }

  const active = data.active || {};
  const shadow = data.shadow || {};

  const activeSection = el('section', {
    className: 'summary summary--active',
    attrs: { 'aria-labelledby': 'active-heading' },
    children: [
      el('h2', { text: 'Active portfolio', attrs: { id: 'active-heading' } }),
      metric('Starting capital', active.starting_capital),
      metric('Current equity', active.current_equity),
      metric('Net P&L', active.net_pnl),
      metric('Archived lifetime P&L', active.archived_lifetime_net_pnl),
    ],
  });

  // Shadow capital lives in its OWN section and is never added to active.
  const shadowSection = el('section', {
    className: 'summary summary--shadow',
    attrs: { 'aria-labelledby': 'shadow-heading' },
    children: [
      el('h2', { text: 'Shadow (virtual capital)', attrs: { id: 'shadow-heading' } }),
      metric('Virtual equity', shadow.virtual_equity),
      el('p', { className: 'note', text: shadow.note || 'Excluded from active totals.' }),
    ],
  });

  root.appendChild(activeSection);
  root.appendChild(shadowSection);

  const dh = data.dark_horse;
  if (dh) {
    root.appendChild(el('section', {
      className: 'summary summary--darkhorse',
      attrs: { 'aria-labelledby': 'dh-heading' },
      children: [
        el('h2', { text: 'Dark Horse', attrs: { id: 'dh-heading' } }),
        metric('Current equity', dh.current_equity),
        metric('Lifetime P&L', dh.lifetime_net_pnl),
      ],
    }));
  }

  const dhd = data.dark_horse_daily;
  if (dhd) {
    root.appendChild(el('section', {
      className: 'summary summary--darkhorse',
      attrs: { 'aria-labelledby': 'dhd-heading' },
      children: [
        el('h2', { text: 'Darkhorse - Daily', attrs: { id: 'dhd-heading' } }),
        metric('Current equity', dhd.current_equity),
        metric('Lifetime P&L', dhd.lifetime_net_pnl),
      ],
    }));
  }
}

function metric(label, value) {
  return el('div', {
    className: 'metric',
    children: [
      el('span', { className: 'metric__label', text: label }),
      el('span', { className: 'metric__value', text: value === undefined || value === null ? '—' : value }),
    ],
  });
}

/* ------------------------------------------------------------ wallet table */

const COLUMNS = [
  ['display_name', 'Name'],
  ['wallet_id', 'Wallet ID'],
  ['strategy_name', 'Strategy'],
  ['strategy_version_id', 'Version'],
  ['days_since_assignment_changed', 'Days since change'],
  ['starting_equity', 'Starting'],
  ['current_equity', 'Equity'],
  ['lifetime_net_pnl', 'Lifetime P&L'],
  ['unrealized_pnl', 'Unrealized P&L'],
  ['total_fees', 'Fees'],
  ['btc_quantity', 'BTC'],
  ['usdt_quantity', 'USDT'],
  ['open_orders', 'Open orders'],
  ['completed_orders', 'Completed orders'],
  ['status', 'Status'],
  ['health', 'Health'],
];

// Columns rendered as a coloured "buys/sells" split (green/red) instead of a
// bare total. The total still lives under `key` so sorting stays numeric.
const SPLIT_COLUMNS = new Set(['open_orders', 'completed_orders']);

/** A "6/4" cell: buys in green, sells in red. */
function buySellNode(buy, sell) {
  return el('span', {
    className: 'bs',
    children: [
      el('span', { className: 'bs__buy', text: String(buy ?? 0) }),
      el('span', { className: 'bs__sep', text: '/' }),
      el('span', { className: 'bs__sell', text: String(sell ?? 0) }),
    ],
  });
}

/** Compare two wallet rows on `key`; numeric when both sides look numeric. */
function compareWallets(a, b, key) {
  const av = a[key], bv = b[key];
  const numeric = (v) => typeof v === 'number'
    || (typeof v === 'string' && v.trim() !== '' && /^-?\d/.test(v.trim()) && !isNaN(parseFloat(v)));
  if (numeric(av) && numeric(bv)) return parseFloat(av) - parseFloat(bv);
  return String(av === undefined || av === null ? '' : av)
    .localeCompare(String(bv === undefined || bv === null ? '' : bv));
}

const SORT_GLYPH = { asc: ' ▲', desc: ' ▼' };

function renderWallets(root, wallets, onSelect, sort) {
  clear(root);
  if (!Array.isArray(wallets) || wallets.length === 0) {
    root.appendChild(emptyNode('No wallets match this filter.'));
    return;
  }

  // Shared, persisted across header clicks within this render.
  const sortState = sort || { key: null, dir: 'asc' };

  const rows = wallets.slice();
  if (sortState.key) {
    rows.sort((a, b) => compareWallets(a, b, sortState.key));
    if (sortState.dir === 'desc') rows.reverse();
  }

  const onHeader = (key) => {
    if (sortState.key === key) {
      sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
    } else {
      sortState.key = key;
      sortState.dir = 'asc';
    }
    renderWallets(root, wallets, onSelect, sortState);
  };

  const head = el('tr', {
    children: COLUMNS.map(([key, label]) => {
      const active = sortState.key === key;
      const th = el('th', {
        className: active ? 'sortable sortable--active' : 'sortable',
        text: label + (active ? SORT_GLYPH[sortState.dir] : ''),
        attrs: {
          scope: 'col', role: 'button', tabindex: '0',
          'aria-sort': active ? (sortState.dir === 'asc' ? 'ascending' : 'descending') : 'none',
          'aria-label': `Sort by ${label}`,
        },
      });
      th.addEventListener('click', () => onHeader(key));
      th.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onHeader(key); }
      });
      return th;
    }),
  });
  const body = el('tbody', {
    children: rows.map((w) => {
      const row = el('tr', {
        className: 'wallet-row',
        attrs: { tabindex: '0', role: 'button',
          'aria-label': `View details for ${w.display_name || w.wallet_id}` },
        children: COLUMNS.map(([key], i) => {
          const cell = el(i === 0 ? 'th' : 'td', {});
          if (SPLIT_COLUMNS.has(key)) {
            cell.appendChild(buySellNode(w[`${key}_buy`], w[`${key}_sell`]));
          } else {
            const value = w[key];
            cell.textContent = value === undefined || value === null ? '—' : value;
          }
          if (i === 0) cell.setAttribute('scope', 'row');
          return cell;
        }),
      });
      if (typeof onSelect === 'function' && w.wallet_id) {
        const open = () => onSelect(w.wallet_id);
        row.addEventListener('click', open);
        row.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
        });
      }
      return row;
    }),
  });

  const table = el('table', {
    className: 'wallets',
    children: [
      el('caption', { text: `${wallets.length} wallet(s)` }),
      el('thead', { children: [head] }),
      body,
    ],
  });
  root.appendChild(table);
}

/* ---------------------------------------------------------------- filters */

const FILTERS = [
  ['all', 'All'],
  ['active', 'All active'],
  ['shadow', 'All shadow'],
  ['dark_horse', 'Dark Horse'],
  ['dark_horse_daily', 'Darkhorse - Daily'],
  ['archived', 'Archived'],
];

function renderFilters(root, current, onChange) {
  clear(root);
  const group = el('div', {
    className: 'filters',
    attrs: { role: 'group', 'aria-label': 'Wallet filters' },
  });
  for (const [value, label] of FILTERS) {
    const btn = el('button', {
      className: 'filter',
      text: label,
      attrs: {
        type: 'button',
        'data-filter': value,
        'aria-pressed': String(value === current),
      },
    });
    btn.addEventListener('click', () => onChange(value));
    group.appendChild(btn);
  }
  root.appendChild(group);
}

/* ---------------------------------------------------- wallet drill-down */

const INSIGHTS = [
  ['current_equity', 'Current equity'],
  ['lifetime_net_pnl', 'Lifetime P&L'],
  ['realized_pnl', 'Realized P&L'],
  ['unrealized_pnl', 'Unrealized P&L'],
  ['total_fees', 'Fees paid'],
  ['btc_quantity', 'BTC held'],
  ['avg_cost', 'Avg cost'],
  ['usdt_quantity', 'USDT cash'],
  ['trade_count', 'Trades'],
  ['buy_count', 'Buys'],
  ['sell_count', 'Sells'],
  ['win_rate', 'Sell win rate'],
];

const ORDER_COLUMNS = [
  ['placed_at', 'Placed'],
  ['filled_at', 'Filled'],
  ['side', 'Side'],
  ['order_type', 'Type'],
  ['requested_qty', 'Req. qty'],
  ['filled_qty', 'Filled qty'],
  ['price', 'Price'],
  ['notional', 'Notional'],
  ['fee', 'Fee'],
  ['realized_pnl', 'Realized'],
  ['status', 'Status'],
  ['reason', 'Reason'],
];

// Resting orders carry a different (smaller) shape than filled history rows.
const OPEN_ORDER_COLUMNS = [
  ['side', 'Side'],
  ['order_type', 'Type'],
  ['limit_price', 'Limit price'],
  ['quantity', 'Quantity'],
  ['reason_code', 'Reason'],
  ['status', 'Status'],
];

function overlayRoot() {
  let root = document.getElementById('wallet-detail');
  if (!root) {
    root = el('div', { attrs: { id: 'wallet-detail' } });
    document.body.appendChild(root);
  }
  return root;
}

function closeWalletDetail() {
  disposeCharts();
  const root = document.getElementById('wallet-detail');
  if (root) clear(root);
}

function insightGrid(insights) {
  return el('div', {
    className: 'insights',
    children: INSIGHTS.map(([key, label]) => {
      const v = insights ? insights[key] : null;
      return el('div', {
        className: 'insight',
        children: [
          el('span', { className: 'insight__label', text: label }),
          el('span', { className: 'insight__value',
            text: v === undefined || v === null ? '—' : v }),
        ],
      });
    }),
  });
}

function orderTable(orders, caption) {
  if (!Array.isArray(orders) || orders.length === 0) {
    return emptyNode(caption === 'open'
      ? 'No resting orders right now — limit orders rest here until they fill or expire.'
      : 'No orders recorded for this wallet.');
  }
  const columns = caption === 'open' ? OPEN_ORDER_COLUMNS : ORDER_COLUMNS;
  const head = el('tr', {
    children: columns.map(([, label]) =>
      el('th', { text: label, attrs: { scope: 'col' } })),
  });
  const body = el('tbody', {
    children: orders.map((o) => el('tr', {
      className: o.status === 'rejected' ? 'order-row order-row--rejected' : 'order-row',
      children: columns.map(([key]) => {
        const v = o[key];
        return el('td', {
          className: key === 'side' ? `side side--${String(v).toLowerCase()}` : undefined,
          text: v === undefined || v === null ? '—' : v,
        });
      }),
    })),
  });
  return el('div', {
    className: 'table-wrap',
    children: [el('table', {
      className: 'orders',
      children: [
        el('caption', { text: `${orders.length} order(s)` }),
        el('thead', { children: [head] }),
        body,
      ],
    })],
  });
}

/* ------------------------------------------------------------ charts (LWC) */
/*
 * Charts are drawn with the self-hosted TradingView Lightweight Charts library
 * (loaded from /static/vendor, so `script-src 'self'` is satisfied). The library
 * is optional: when it is absent (unit tests, or a failed asset load) every
 * mount is a no-op and the panel shows a plain empty state — no chart code ever
 * reaches for innerHTML, matching the A14 posture of the rest of this file.
 */

const CHARTS_AVAILABLE = () => typeof LightweightCharts !== 'undefined';

const CHART_THEME = {
  layout: { background: { color: 'transparent' }, textColor: '#9aa4b6' },
  grid: { vertLines: { color: 'rgba(255,255,255,0.04)' },
          horzLines: { color: 'rgba(255,255,255,0.04)' } },
  rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
  timeScale: { borderColor: 'rgba(255,255,255,0.08)', timeVisible: true },
};

const UP = '#26a69a';
const DOWN = '#ef5350';

// Every chart instance we create is tracked so closing the modal disposes them.
let activeCharts = [];

function disposeCharts() {
  for (const c of activeCharts) {
    try { c.remove(); } catch { /* already gone */ }
  }
  activeCharts = [];
}

/** Create a themed chart sized to its container; tracked for disposal. */
function makeChart(container, height) {
  const chart = LightweightCharts.createChart(container, {
    ...CHART_THEME,
    width: container.clientWidth || 760,
    height,
    handleScale: true,
    handleScroll: true,
  });
  activeCharts.push(chart);
  // Keep the chart width in step with the (resizable/scrollable) modal.
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth || 760 });
    });
    ro.observe(container);
  }
  return chart;
}

function num(v) {
  const n = parseFloat(v);
  return isNaN(n) ? null : n;
}

/** Price panel: candles + this wallet's strategy overlays + trade markers. */
function mountPriceChart(container, data) {
  if (!CHARTS_AVAILABLE() || !data || !Array.isArray(data.candles) || data.candles.length === 0) {
    container.appendChild(emptyNode('No price history to chart yet.'));
    return;
  }
  const chart = makeChart(container, 300);
  const candles = chart.addCandlestickSeries({
    upColor: UP, downColor: DOWN, borderVisible: false,
    wickUpColor: UP, wickDownColor: DOWN,
  });
  candles.setData(data.candles);

  const lowerOverlays = (data.overlays || []).filter((o) => o.pane === 'lower');
  for (const ov of (data.overlays || [])) {
    if (ov.pane !== 'price') continue;
    if (ov.kind === 'threshold') {
      candles.createPriceLine({
        price: num(ov.value), color: ov.color, lineWidth: 1,
        lineStyle: 2, axisLabelVisible: false, title: ov.label || '',
      });
    } else if (Array.isArray(ov.points)) {
      const s = chart.addLineSeries({
        color: ov.color, lineWidth: 1, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false,
      });
      s.setData(ov.points);
    }
  }

  if (Array.isArray(data.markers) && data.markers.length) {
    candles.setMarkers(data.markers.map(markerFor));
  }

  // Optional lower oscillator pane, time-synced with the price pane.
  if (lowerOverlays.length) {
    const lowerBox = el('div', { className: 'chart chart--lower' });
    container.parentElement.appendChild(lowerBox);
    mountLowerPane(lowerBox, lowerOverlays, chart);
  }
  chart.timeScale().fitContent();
}

function markerFor(m) {
  const t = m.time;
  if (m.side === 'BUY') {
    return { time: t, position: 'belowBar', color: '#4a90d9',
             shape: 'arrowUp', text: 'B' };
  }
  const color = m.result === 'win' ? UP : m.result === 'loss' ? DOWN : '#9aa4b6';
  return { time: t, position: 'aboveBar', color, shape: 'arrowDown', text: 'S' };
}

/** Lower pane: oscillator line overlays + threshold guides, synced to `priceChart`. */
function mountLowerPane(container, overlays, priceChart) {
  const chart = makeChart(container, 130);
  let anchor = null;
  for (const ov of overlays) {
    if (ov.kind === 'line' && Array.isArray(ov.points)) {
      const s = chart.addLineSeries({
        color: ov.color, lineWidth: 1, priceLineVisible: false,
        lastValueVisible: false,
      });
      s.setData(ov.points);
      if (!anchor) anchor = s;
    }
  }
  if (!anchor) {
    anchor = chart.addLineSeries({ color: 'transparent' });
    anchor.setData(overlays[0] && overlays[0].points ? overlays[0].points : []);
  }
  for (const ov of overlays) {
    if (ov.kind === 'threshold') {
      anchor.createPriceLine({
        price: num(ov.value), color: ov.color, lineWidth: 1,
        lineStyle: 2, axisLabelVisible: false, title: ov.label || '',
      });
    }
  }
  syncTimeScales(priceChart, chart);
  chart.timeScale().fitContent();
}

/** Two-way visible-range sync so stacked panes scroll/zoom together. */
function syncTimeScales(a, b) {
  const ats = a.timeScale();
  const bts = b.timeScale();
  let guard = false;
  const link = (from, to) => from.subscribeVisibleLogicalRangeChange((r) => {
    if (guard || !r) return;
    guard = true;
    to.setVisibleLogicalRange(r);
    guard = false;
  });
  link(ats, bts);
  link(bts, ats);
}

/** Performance panel: equity (right scale) + realized/unrealized/fees (left). */
function mountPerfChart(container, points) {
  if (!CHARTS_AVAILABLE() || !Array.isArray(points) || points.length === 0) {
    container.appendChild(emptyNode('No performance history yet.'));
    return;
  }
  const chart = makeChart(container, 240);
  chart.applyOptions({ leftPriceScale: { visible: true,
    borderColor: 'rgba(255,255,255,0.08)' } });

  // `tone` maps to a CSS class so the swatch/label colour matches the line
  // WITHOUT an inline style (blocked by the CSP's `style-src 'self'`).
  const series = [
    { key: 'equity', label: 'Balance', color: '#4a90d9', tone: 'balance', scale: 'right' },
    { key: 'realized_pnl', label: 'Realized P&L', color: UP, tone: 'realized', scale: 'left' },
    { key: 'unrealized_pnl', label: 'Unrealized P&L', color: '#f5a623', tone: 'unrealized', scale: 'left' },
    { key: 'fees', label: 'Fees', color: '#c56be6', tone: 'fees', scale: 'left' },
  ];
  const legend = el('div', { className: 'chart-legend' });
  for (const def of series) {
    const s = chart.addLineSeries({
      color: def.color, lineWidth: 2, priceScaleId: def.scale,
      priceLineVisible: false, lastValueVisible: false,
    });
    s.setData(points.map((p) => ({ time: p.time, value: num(p[def.key]) }))
      .filter((d) => d.value !== null));
    legend.appendChild(legendItem(def.label, def.tone, s));
  }
  container.parentElement.appendChild(legend);
  chart.timeScale().fitContent();
}

/** A clickable legend chip that toggles its series' visibility. `tone` is a
 * colour class (not an inline style, which the CSP forbids) matching the line. */
function legendItem(label, tone, series) {
  let visible = true;
  const swatch = el('span', {
    className: `chart-legend__swatch chart-legend__swatch--${tone}` });
  const chip = el('button', {
    className: `chart-legend__item chart-legend__item--${tone}`,
    attrs: { type: 'button', 'aria-pressed': 'true', 'aria-label': `Toggle ${label}` },
    children: [swatch, el('span', { text: label })],
  });
  chip.addEventListener('click', () => {
    visible = !visible;
    series.applyOptions({ visible });
    chip.setAttribute('aria-pressed', String(visible));
    chip.classList.toggle('chart-legend__item--off', !visible);
  });
  return chip;
}

/** Exposure panel: percentage of equity held in BTC over time. */
function mountExposureChart(container, points) {
  if (!CHARTS_AVAILABLE() || !Array.isArray(points) || points.length === 0) {
    container.appendChild(emptyNode('No exposure history yet.'));
    return;
  }
  const chart = makeChart(container, 140);
  const area = chart.addAreaSeries({
    lineColor: '#4a90d9', topColor: 'rgba(74,144,217,0.35)',
    bottomColor: 'rgba(74,144,217,0.02)', lineWidth: 2,
    priceLineVisible: false, lastValueVisible: true,
  });
  area.setData(points.map((p) => ({ time: p.time, value: num(p.exposure_pct) }))
    .filter((d) => d.value !== null));
  chart.timeScale().fitContent();
}

/* --------------------------------------------------- analytics panel (DOM) */

/** Small labelled stat tile. */
function statTile(label, value) {
  return el('div', {
    className: 'stat',
    children: [
      el('span', { className: 'stat__label', text: label }),
      el('span', { className: 'stat__value',
        text: value === undefined || value === null ? '—' : value }),
    ],
  });
}

const ACTIVITY_TILES = [
  ['trade_count', 'Trades'],
  ['buy_count', 'Buys'],
  ['sell_count', 'Sells'],
  ['win_count', 'Wins'],
  ['loss_count', 'Losses'],
  ['win_rate', 'Win rate'],
  ['avg_win', 'Avg win'],
  ['avg_loss', 'Avg loss'],
  ['profit_factor', 'Profit factor'],
];

function activityStrip(activity) {
  const strip = el('div', { className: 'stat-strip' });
  for (const [key, label] of ACTIVITY_TILES) {
    strip.appendChild(statTile(label, activity ? activity[key] : null));
  }
  return strip;
}

/** Strategy metric blocks — rendered generically from whatever the backend
 * returns for the wallet's CURRENT strategy, so it follows reassignments. */
function strategyPanel(blocks, reasons) {
  const wrap = el('div', { className: 'strategy-panel' });
  const list = Array.isArray(blocks) ? blocks : [];
  for (const block of list) {
    const rows = el('div', { className: 'kv' });
    for (const r of (block.rows || [])) {
      rows.appendChild(el('div', {
        className: 'kv__row',
        children: [
          el('span', { className: 'kv__k', text: r.label }),
          el('span', { className: 'kv__v',
            text: r.value === undefined || r.value === null ? '—' : r.value }),
        ],
      }));
    }
    wrap.appendChild(el('div', {
      className: 'strategy-block',
      children: [el('h4', { className: 'strategy-block__title', text: block.title }), rows],
    }));
  }
  if (Array.isArray(reasons) && reasons.length) {
    const rows = el('div', { className: 'kv' });
    for (const r of reasons) {
      rows.appendChild(el('div', {
        className: 'kv__row',
        children: [
          el('span', { className: 'kv__k', text: r.reason }),
          el('span', { className: 'kv__v', text: String(r.count) }),
        ],
      }));
    }
    wrap.appendChild(el('div', {
      className: 'strategy-block',
      children: [el('h4', { className: 'strategy-block__title', text: 'Fills by reason' }), rows],
    }));
  }
  if (!wrap.firstChild) wrap.appendChild(emptyNode('No strategy metrics for this wallet.'));
  return wrap;
}

/**
 * Build the analytics section (charts + panels). Chart containers are created
 * now but only *mounted* after the section is in the DOM (Lightweight Charts
 * needs a laid-out container to size itself), so mount callbacks are collected
 * into `pending` and run by the caller.
 */
function analyticsSection(wallet, chartData, timeseries, pending) {
  const priceBox = el('div', { className: 'chart chart--price' });
  const perfBox = el('div', { className: 'chart chart--perf' });
  const expoBox = el('div', { className: 'chart chart--expo' });
  pending.push(() => mountPriceChart(priceBox, chartData));
  pending.push(() => mountPerfChart(perfBox, timeseries));
  pending.push(() => mountExposureChart(expoBox, timeseries));

  return el('div', {
    className: 'analytics',
    children: [
      el('h3', { className: 'detail__h', text: 'Price · indicators · trades' }),
      el('div', { className: 'chart-wrap', children: [priceBox] }),
      el('h3', { className: 'detail__h', text: 'Performance' }),
      el('div', { className: 'chart-wrap', children: [perfBox] }),
      el('h3', { className: 'detail__h', text: 'Exposure (% of equity in BTC)' }),
      el('div', { className: 'chart-wrap', children: [expoBox] }),
      el('h3', { className: 'detail__h', text: 'Activity' }),
      activityStrip(wallet.activity),
      el('h3', { className: 'detail__h', text: 'Strategy' }),
      strategyPanel(wallet.strategy_metrics, wallet.reason_breakdown),
    ],
  });
}

function renderWalletDetail(root, wallet, orders, chartData, timeseries) {
  clear(root);
  disposeCharts();
  const openOrders = (wallet && wallet.open_orders) || [];
  const pending = [];

  const closeBtn = el('button', {
    className: 'detail__close', text: '✕',
    attrs: { type: 'button', 'aria-label': 'Close wallet details' },
  });
  closeBtn.addEventListener('click', closeWalletDetail);

  const panel = el('div', {
    className: 'detail__panel',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Wallet details' },
    children: [
      closeBtn,
      el('h2', { className: 'detail__title', text: wallet.display_name || wallet.wallet_id }),
      el('p', { className: 'detail__meta',
        text: `${wallet.strategy_name || '—'} · ${wallet.strategy_version_id || '—'} · ${wallet.kind || ''}` }),
      el('p', { className: 'detail__desc',
        text: wallet.strategy_description || 'No strategy description available.' }),
      analyticsSection(wallet, chartData, timeseries, pending),
      el('h3', { className: 'detail__h', text: 'Performance snapshot' }),
      insightGrid(wallet.insights),
      el('h3', { className: 'detail__h', text: 'Open orders' }),
      orderTable(openOrders, 'open'),
      el('h3', { className: 'detail__h', text: 'Order history' }),
      orderTable(orders, 'history'),
    ],
  });

  const backdrop = el('div', { className: 'detail__backdrop', children: [panel] });
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeWalletDetail(); });
  root.appendChild(backdrop);
  closeBtn.focus();

  // Charts need a laid-out container to size themselves — mount now that the
  // panel is in the DOM. Failures never break the rest of the detail view.
  for (const mount of pending) {
    try { mount(); } catch { /* leave the empty state in place */ }
  }
}

async function openWalletDetail(walletId) {
  const root = overlayRoot();
  clear(root);
  root.appendChild(el('div', {
    className: 'detail__backdrop',
    children: [el('div', { className: 'detail__panel', children: [loadingNode()] })],
  }));
  try {
    const id = encodeURIComponent(walletId);
    const [wallet, ordersResp, chartData, tsResp] = await Promise.all([
      getJson(`/wallets/${id}`),
      getJson(`/wallets/${id}/orders`),
      getJson(`/wallets/${id}/chart`).catch(() => null),
      getJson(`/wallets/${id}/timeseries`).catch(() => ({ points: [] })),
    ]);
    renderWalletDetail(root, wallet, ordersResp.orders || [], chartData,
                       (tsResp && tsResp.points) || []);
  } catch {
    clear(root);
    const box = el('div', { className: 'detail__panel', children: [
      errorNode('Could not load wallet details.'),
    ] });
    const backdrop = el('div', { className: 'detail__backdrop', children: [box] });
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeWalletDetail(); });
    root.appendChild(backdrop);
  }
}

if (typeof document !== 'undefined') {
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeWalletDetail(); });
}

/* ---------------------------------------------------------------- insights */

const INSIGHT_CARDS = [
  { key: 'net_pnl', label: 'Net P&L', signed: true, sub: (d) =>
      `Realized ${d.realized_pnl} · Unrealized ${d.unrealized_pnl}` },
  { key: 'top_performer', label: 'Top performer',
    value: (d) => d.top_performer && d.top_performer.lifetime_net_pnl, signed: true,
    sub: (d) => d.top_performer && d.top_performer.display_name },
  { key: 'worst_performer', label: 'Worst performer',
    value: (d) => d.worst_performer && d.worst_performer.lifetime_net_pnl, signed: true,
    sub: (d) => d.worst_performer && d.worst_performer.display_name },
  { key: 'wallets_in_profit', label: 'Wallets in profit',
    value: (d) => d.wallets_in_profit && `${d.wallets_in_profit.count}/${d.wallets_in_profit.total}`,
    sub: () => 'of the real book' },
  { key: 'total_fills', label: 'Total fills',
    value: (d) => d.total_fills, sub: (d) => `Fees paid ${d.total_fees}` },
  { key: 'btc_exposure', label: 'BTC exposure',
    value: (d) => d.btc_exposure && d.btc_exposure.value,
    sub: (d) => d.btc_exposure && `${d.btc_exposure.btc} BTC · ${d.btc_exposure.pct_in_btc} of equity` },
  { key: 'open_orders', label: 'Resting orders', splitOrders: true,
    sub: (d) => d.open_orders && `${d.open_orders.total} total` },
  { key: 'most_active', label: 'Most active',
    value: (d) => d.most_active && `${d.most_active.fills} fills`,
    sub: (d) => d.most_active && d.most_active.display_name },
  { key: 'dark_horse', label: 'Dark Horse',
    value: (d) => d.dark_horse && d.dark_horse.lifetime_net_pnl, signed: true,
    sub: () => 'permanent · committee' },
  { key: 'dark_horse_daily', label: 'Darkhorse - Daily',
    value: (d) => d.dark_horse_daily && d.dark_horse_daily.lifetime_net_pnl, signed: true,
    sub: () => 'permanent · adaptive' },
];

/** Colour a signed money string green/red (neutral when zero/–). */
function signClass(value) {
  const n = parseFloat(value);
  if (isNaN(n) || n === 0) return '';
  return n > 0 ? 'pos' : 'neg';
}

function renderInsights(root, data) {
  clear(root);
  if (!data) { root.appendChild(emptyNode('Insights unavailable.')); return; }
  for (const card of INSIGHT_CARDS) {
    const valueNode = el('span', { className: 'insight-card__value' });
    if (card.splitOrders && data.open_orders) {
      valueNode.appendChild(buySellNode(data.open_orders.buys, data.open_orders.sells));
    } else {
      const v = card.value ? card.value(data) : data[card.key];
      valueNode.textContent = v === undefined || v === null ? '—' : v;
      const cls = card.signed ? signClass(v) : '';
      if (cls) valueNode.classList.add(cls);
    }
    const sub = card.sub ? card.sub(data) : null;
    root.appendChild(el('div', {
      className: 'insight-card',
      children: [
        el('span', { className: 'insight-card__label', text: card.label }),
        valueNode,
        sub ? el('span', { className: 'insight-card__sub', text: sub }) : null,
      ],
    }));
  }
}

/* -------------------------------------------------------------- controller */

function setLiveStatus(root, kind, message) {
  if (!root) return;
  clear(root);
  root.setAttribute('data-state', kind);
  root.appendChild(el('span', { className: 'live-dot' }));
  root.appendChild(el('span', { text: message }));
}

async function refresh(state) {
  const walletRoot = document.getElementById('wallets');
  const summaryRoot = document.getElementById('summary');
  const insightsRoot = document.getElementById('insights');
  const liveRoot = document.getElementById('live-status');
  if (!walletRoot || !summaryRoot) return;

  if (state.first) {
    walletRoot.appendChild(loadingNode());
    if (insightsRoot) insightsRoot.appendChild(loadingNode());
  }

  let ok = true;
  try {
    renderSummary(summaryRoot, await getJson('/portfolio/summary'));
  } catch { ok = false; clear(summaryRoot); summaryRoot.appendChild(errorNode('Could not load portfolio summary.')); }

  if (insightsRoot) {
    try {
      renderInsights(insightsRoot, await getJson('/portfolio/insights'));
    } catch { ok = false; clear(insightsRoot); insightsRoot.appendChild(errorNode('Could not load insights.')); }
  }

  try {
    const query = state.filter === 'all' ? '' : `?kind=${encodeURIComponent(state.filter)}`;
    const data = await getJson(`/wallets${query}`);
    renderWallets(walletRoot, data.wallets, openWalletDetail, state.sort);
  } catch { ok = false; clear(walletRoot); walletRoot.appendChild(errorNode('Could not load wallets.')); }

  state.first = false;
  if (ok) {
    state.lastOk = Date.now();
    setLiveStatus(liveRoot, 'live', 'Live');
  } else if (state.lastOk) {
    const secs = Math.round((Date.now() - state.lastOk) / 1000);
    setLiveStatus(liveRoot, 'stale', `Reconnecting… last update ${secs}s ago`);
  } else {
    setLiveStatus(liveRoot, 'error', 'Offline');
  }
}

const REFRESH_MS = 10000;

function init() {
  const state = { filter: 'active', sort: { key: null, dir: 'asc' }, first: true, lastOk: 0 };
  const filterRoot = document.getElementById('filters');
  const onChange = (value) => {
    state.filter = value;
    if (filterRoot) renderFilters(filterRoot, state.filter, onChange);
    refresh(state);
  };
  if (filterRoot) renderFilters(filterRoot, state.filter, onChange);
  refresh(state);

  // Poll for live updates; skip while the tab is hidden to save work, and
  // refresh immediately when it becomes visible again.
  if (typeof setInterval !== 'undefined') {
    setInterval(() => {
      if (typeof document === 'undefined' || document.visibilityState !== 'hidden') {
        refresh(state);
      }
    }, REFRESH_MS);
  }
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') refresh(state);
    });
  }
}

if (typeof document !== 'undefined' && document.readyState !== 'loading') {
  init();
} else if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', init);
}

/* Exported for tests (Node/jsdom). */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { el, clear, safeUrl, safeLink, renderSummary, renderWallets, renderFilters, stateNode, renderWalletDetail, insightGrid, orderTable, renderInsights, buySellNode, activityStrip, strategyPanel, markerFor, analyticsSection };
}
