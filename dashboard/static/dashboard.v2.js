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
  ['btc_quantity', 'BTC'],
  ['usdt_quantity', 'USDT'],
  ['status', 'Status'],
  ['health', 'Health'],
];

function renderWallets(root, wallets) {
  clear(root);
  if (!Array.isArray(wallets) || wallets.length === 0) {
    root.appendChild(emptyNode('No wallets match this filter.'));
    return;
  }

  const head = el('tr', {
    children: COLUMNS.map(([, label]) => el('th', { text: label, attrs: { scope: 'col' } })),
  });
  const body = el('tbody', {
    children: wallets.map((w) =>
      el('tr', {
        children: COLUMNS.map(([key], i) => {
          const value = w[key];
          const cell = el(i === 0 ? 'th' : 'td', {
            text: value === undefined || value === null ? '—' : value,
          });
          if (i === 0) cell.setAttribute('scope', 'row');
          return cell;
        }),
      })
    ),
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

/* -------------------------------------------------------------- controller */

async function load(state) {
  const walletRoot = document.getElementById('wallets');
  const summaryRoot = document.getElementById('summary');
  if (!walletRoot || !summaryRoot) return;

  clear(walletRoot);
  walletRoot.appendChild(loadingNode());

  try {
    const summary = await getJson('/portfolio/summary');
    renderSummary(summaryRoot, summary);
  } catch {
    clear(summaryRoot);
    summaryRoot.appendChild(errorNode('Could not load portfolio summary.'));
  }

  try {
    const query = state.filter === 'all' ? '' : `?kind=${encodeURIComponent(state.filter)}`;
    const data = await getJson(`/wallets${query}`);
    renderWallets(walletRoot, data.wallets);
  } catch {
    clear(walletRoot);
    walletRoot.appendChild(errorNode('Could not load wallets.'));
  }
}

function init() {
  const state = { filter: 'active' };
  const filterRoot = document.getElementById('filters');
  const onChange = (value) => {
    state.filter = value;
    if (filterRoot) renderFilters(filterRoot, state.filter, onChange);
    load(state);
  };
  if (filterRoot) renderFilters(filterRoot, state.filter, onChange);
  load(state);
}

if (typeof document !== 'undefined' && document.readyState !== 'loading') {
  init();
} else if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', init);
}

/* Exported for tests (Node/jsdom). */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { el, clear, safeUrl, safeLink, renderSummary, renderWallets, renderFilters, stateNode };
}
