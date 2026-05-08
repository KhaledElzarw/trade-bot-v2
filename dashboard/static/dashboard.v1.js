const GRID_COLS = 24;
const GRID_GAP = 14;
const MIN_SPAN = 6;
const TIMEFRAMES = ['1s', '1m', '5m', '30m', '1h', '1d', '1w', '1M'];
const TIMEFRAME_LABELS = { '1s': '1 Second', '1m': '1 Minute', '5m': '5 Minutes', '30m': '30 Minutes', '1h': '1 Hour', '1d': '1 Day', '1w': '1 Week', '1M': '1 Month' };
const DEFAULT_LIMITS = { '1s': 240, '1m': 180, '5m': 180, '30m': 180, '1h': 240, '1d': 180, '1w': 120, '1M': 120 };
const GRID_MODES = ['scalpy', 'fatty'];
const MODE_CONTROL_MODES = ['scalpy', 'fatty', 'ai_optimized'];
const GRID_MODE_LABELS = { scalpy: 'Scalpy', fatty: 'Fatty', ai_optimized: 'Optimized AI' };
const LEGACY_OPTIMIZED_MODES = new Set(['flexy', 'ai_optimized']);
const SERVER_TIME_ZONE = 'Asia/Dubai';
const GST_OFFSET_MS = 4 * 60 * 60 * 1000;
const NEWS_PAGE_SIZE = 5;
const NEWS_HISTORY_DAYS = 90;
const MACRO_CALENDAR_WINDOW_DAYS = 7;
const MACRO_CALENDAR_LOOKBACK_MONTHS = 12;
const MACRO_CALENDAR_LOOKAHEAD_MONTHS = 12;
const MACRO_CALENDAR_KINDS = {
  completed: { prefix: 'completed-macro', status: 'Completed', empty: 'No completed macro events match the filters' },
  upcoming: { prefix: 'upcoming-macro', status: 'Upcoming', empty: 'No upcoming macro events match the filters' },
};
const MACRO_CALENDAR_TEMPLATES = [
  ['Asia Liquidity Open', 8, 0, 2, '#1767c2', 'Watch Asia liquidity, early dollar tone, and grid spread pressure.', 'Asia session set the initial liquidity tone for BTC spread and inventory risk.', '🇯🇵', 'Asia session'],
  ['Europe Macro / Yields Check', 12, 0, 2, '#f7931a', 'Watch EUR/US yields and risk appetite before the US data window.', 'Europe macro flow updated rate-pressure context for the active grid.', '🇪🇺', 'Europe'],
  ['US Data Window', 16, 30, 3, '#0d8a2f', 'Watch scheduled US releases and liquidity reaction around the event.', 'US data window passed; confirm whether volatility expanded or faded.', '🇺🇸', 'United States'],
  ['US Cash Open / ETF Flow', 17, 30, 3, '#d54545', 'Watch ETF flow, equity beta, and headline reaction during the cash open.', 'US cash open flow is in; reassess BTC trend pressure and exposure.', '🇺🇸', 'United States'],
  ['Daily Close Risk Review', 23, 45, 2, '#13a7b4', 'Review realized PnL, open exposure, and overnight grid risk.', 'Daily risk review completed; carry only exposure justified by the regime.', '🇺🇳', 'Global crypto close'],
];
const INITIAL_QUERY = new URLSearchParams(window.location.search);
function normalizeInitialTimeframe(tf) { return TIMEFRAMES.includes(tf) ? tf : '1m'; }
const CONFIG_FIELDS = [
  { key: 'symbol', label: 'Symbol', type: 'text' },
  { key: 'interval', label: 'Interval', type: 'select', options: TIMEFRAMES },
  { key: 'feeBps', label: 'Fee BPS', type: 'number', step: '1' },
  { key: 'riskPerTradePct', label: 'Risk Per Trade', type: 'number', step: '0.001' },
  { key: 'positionCapPct', label: 'Position Cap %', type: 'number', step: '0.01' },
  { key: 'maxDailyLossPct', label: 'Max Daily Loss %', type: 'number', step: '0.01' },
  { key: 'maxTradesPerDay', label: 'Max Trades / Day', type: 'number', step: '1' },
  { key: 'gridMode', label: 'Grid Mode', type: 'select', options: GRID_MODES },
  { key: 'gridLevels', label: 'Grid Levels', type: 'number', step: '1' },
  { key: 'gridSpacingPct', label: 'Grid Spacing %', type: 'number', step: '0.001' },
  { key: 'gridMaxExposurePct', label: 'Grid Max Exposure %', type: 'number', step: '0.01' },
  { key: 'gridMinPerLevelUsdt', label: 'Min Per Level USDT', type: 'number', step: '1' },
  { key: 'gridMinSpacingPctScalpy', label: 'Scalpy Min Spacing %', type: 'number', step: '0.001' },
  { key: 'gridMinSpacingPctFatty', label: 'Fatty Min Spacing %', type: 'number', step: '0.001' },
  { key: 'gridTrailActive', label: 'Trail Active', type: 'select', options: ['true', 'false'] },
  { key: 'gridTrailAtrMult', label: 'Trail ATR Mult', type: 'number', step: '0.1' },
  { key: 'gridTrailArmAfterAtr', label: 'Trail Arm After ATR', type: 'number', step: '0.1' },
  { key: 'gridTrailArmTrendStrength', label: 'Trail Arm Trend', type: 'number', step: '0.001' },
  { key: 'gridTrailForceExitTrendStrength', label: 'Trail Force Exit Trend', type: 'number', step: '0.001' },
  { key: 'gridTrailMinNetProfitPct', label: 'Trail Min Net Profit %', type: 'number', step: '0.001' },
  { key: 'gridTrendEscapeStrength', label: 'Trend Escape Strength', type: 'number', step: '0.001' },
  { key: 'cooldownMinutesAfterLoss', label: 'Cooldown After Loss (min)', type: 'number', step: '1' },
  { key: 'allowLiveOrders', label: 'Allow Live Orders', type: 'select', options: ['true', 'false'] },
  { key: 'paperStartUsdt', label: 'Paper Start USDT', type: 'number', step: '1' },
  { key: 'paperStartBtc', label: 'Paper Start BTC', type: 'number', step: '0.000001' },
  { key: 'aiEnabled', label: 'AI Assist', type: 'select', options: ['true', 'false'] },
  { key: 'aiDryRun', label: 'AI Dry Run', type: 'select', options: ['true', 'false'] },
  { key: 'aiShadowMode', label: 'AI Shadow Mode', type: 'select', options: ['true', 'false'] },
  { key: 'aiEndpointKey', label: 'AI Endpoint', type: 'select', options: [] },
  { key: 'aiProvider', label: 'AI Provider', type: 'text' },
  { key: 'aiBaseUrl', label: 'AI Base URL', type: 'text' },
  { key: 'aiModel', label: 'AI Primary Model', type: 'select', options: [] },
  { key: 'aiQuickModel', label: 'AI Quick Model', type: 'select', options: [] },
  { key: 'aiDeepModel', label: 'AI Deep Model', type: 'select', options: [] },
  { key: 'aiFallbackModel', label: 'AI Fallback Model', type: 'select', options: [] },
  { key: 'aiMinConfidence', label: 'AI Min Confidence', type: 'number', step: '0.01' },
  { key: 'aiPollSeconds', label: 'AI Poll Seconds', type: 'number', step: '1' },
  { key: 'aiTimeoutSeconds', label: 'AI Timeout Seconds', type: 'number', step: '1' },
  { key: 'aiTemperature', label: 'AI Temperature', type: 'number', step: '0.05' },
  { key: 'hourlySummary', label: 'Hourly Summary', type: 'select', options: ['true', 'false'] },
];
const AI_MODEL_FIELDS = new Set(['aiModel', 'aiQuickModel', 'aiDeepModel', 'aiFallbackModel']);
const stateUi = {
  theme: localStorage.getItem('tradebot-theme') || 'light',
  lastSnapshot: {},
  lastEvents: [],
  lastAiDecisions: [],
  aiDecisionPage: 0,
  macroCalendars: {
    completed: { page: 0, monthFilter: '', yearFilter: '', eventFilter: '' },
    upcoming: { page: 0, monthFilter: '', yearFilter: '', eventFilter: '' },
  },
  lastMacroCalendarServerTime: null,
  activeAgentRole: '',
  activeAgentDecisionId: '',
  eventPage: -1,
  eventPageSize: 5,
  newsPage: 0,
  newsPageSize: NEWS_PAGE_SIZE,
  lastOrders: [],
  orderPage: -1,
  orderPageSize: 5,
  orderFilter: 'all',
  orderTab: 'open',
  lastOrderGrid: {},
  lastStatus: null,
  lastState: null,
  lastCumulative: null,
  lastRuntime: null,
  lastOhlcv: [],
  lastIntelligence: null,
  aiEndpoints: [],
  aiEndpointModels: {},
  refreshInFlight: false,
  marketRefreshInFlight: false,
  marketRefreshTimer: null,
  dashboardRefreshTimer: null,
  liveEventSource: null,
  liveEventStreamActive: false,
  liveEventReconnectTimer: null,
  liveEventLastDataAt: 0,
  freshnessTicker: null,
  hardStatusSyncTimer: null,
  hardChartSyncTimer: null,
  dashboardSleeping: false,
  heavyRefreshLoaded: false,
  marketRefreshMs: 1000,
  dashboardRefreshMs: 30 * 60 * 1000,
  chartSocket: null,
  chartStreamGeneration: 0,
  chartStreamSymbol: 'BTCUSDT',
  chartStreamInterval: null,
  chartReconnectAttempt: 0,
  chartReconnectTimer: null,
  chartStaleTimer: null,
  chartDrawHandle: null,
  chartLastEventAt: 0,
  chartFallbackActive: true,
  chartFallbackReason: 'seed',
  lastSeq: {},
  lastSeqInstance: {},
  eventCursor: 0,
  lastSummaryMetricKeys: new Set(),
  lastEventsRenderKey: '',
  lastOrdersRenderKey: '',
  timeframe: normalizeInitialTimeframe(INITIAL_QUERY.get('interval') || localStorage.getItem('tradebot-chart-timeframe') || '1m'),
  candleLimit: Number(localStorage.getItem('tradebot-chart-limit') || 180),
  historyOffset: Number(localStorage.getItem('tradebot-chart-history-offset') || 0),
  panOffset: Number(localStorage.getItem('tradebot-chart-pan-offset') || 0),
  visibleOhlcv: [],
  chartHoverIndex: null,
  chartDragState: null,
};

function setTheme(theme) {
  document.body.classList.toggle('light', theme === 'light');
  document.body.classList.toggle('dark', theme === 'dark');
  stateUi.theme = theme;
  localStorage.setItem('tradebot-theme', theme);
}
setTheme(stateUi.theme);

document.getElementById('theme-toggle').addEventListener('click', () => setTheme(stateUi.theme === 'dark' ? 'light' : 'dark'));
document.getElementById('bot-toggle-btn').addEventListener('click', toggleBotPause);
document.getElementById('ai-toggle-btn').addEventListener('click', toggleAiAssist);
document.getElementById('reset-layout-btn').addEventListener('click', () => { localStorage.removeItem(getLayoutKey()); applyDefaultLayout(); saveLayout(); });
document.getElementById('events-first-btn').addEventListener('click', () => changeEventPage('first'));
document.getElementById('events-prev-btn').addEventListener('click', () => changeEventPage('prev'));
document.getElementById('events-next-btn').addEventListener('click', () => changeEventPage('next'));
document.getElementById('events-last-btn').addEventListener('click', () => changeEventPage('last'));
bindClickIfPresent('news-first-btn', () => changeNewsPage('first'));
bindClickIfPresent('news-prev-btn', () => changeNewsPage('prev'));
bindClickIfPresent('news-next-btn', () => changeNewsPage('next'));
bindClickIfPresent('news-last-btn', () => changeNewsPage('last'));
bindClickIfPresent('ai-decisions-first-btn', () => changeAiDecisionPage('first'));
bindClickIfPresent('ai-decisions-prev-btn', () => changeAiDecisionPage('prev'));
bindClickIfPresent('ai-decisions-next-btn', () => changeAiDecisionPage('next'));
bindClickIfPresent('ai-decisions-last-btn', () => changeAiDecisionPage('last'));
Object.keys(MACRO_CALENDAR_KINDS).forEach(kind => {
  const prefix = MACRO_CALENDAR_KINDS[kind].prefix;
  bindClickIfPresent(`${prefix}-first-btn`, () => changeMacroCalendarPage(kind, 'first'));
  bindClickIfPresent(`${prefix}-prev-btn`, () => changeMacroCalendarPage(kind, 'prev'));
  bindClickIfPresent(`${prefix}-next-btn`, () => changeMacroCalendarPage(kind, 'next'));
  bindClickIfPresent(`${prefix}-last-btn`, () => changeMacroCalendarPage(kind, 'last'));
  const monthFilter = document.getElementById(`${prefix}-month-filter`);
  if (monthFilter) monthFilter.addEventListener('change', () => setMacroCalendarFilter(kind, 'month', monthFilter.value));
  const yearFilter = document.getElementById(`${prefix}-year-filter`);
  if (yearFilter) yearFilter.addEventListener('change', () => setMacroCalendarFilter(kind, 'year', yearFilter.value));
  const eventFilter = document.getElementById(`${prefix}-event-filter`);
  if (eventFilter) eventFilter.addEventListener('change', () => setMacroCalendarFilter(kind, 'event', eventFilter.value));
});
bindClickIfPresent('agent-configure-btn', () => renderAgentChatNotice('Agent configuration is not enabled in this read-only recovery.'));
bindClickIfPresent('agent-chat-send-btn', () => renderAgentChatNotice('Agent chat is not enabled in this read-only recovery.'));
const agentSelect = document.getElementById('agent-select');
if (agentSelect) agentSelect.addEventListener('change', () => setActiveAgentReport(agentSelect.value));
const agentChatInput = document.getElementById('agent-chat-input');
if (agentChatInput) agentChatInput.addEventListener('keydown', ev => {
  if ((ev.metaKey || ev.ctrlKey) && ev.key === 'Enter') renderAgentChatNotice('Agent chat is not enabled in this read-only recovery.');
});
document.getElementById('orders-tab-open-btn').addEventListener('click', () => setOrderTab('open'));
document.getElementById('orders-tab-history-btn').addEventListener('click', () => setOrderTab('history'));
document.getElementById('orders-filter-buy-btn').addEventListener('click', () => setOrderFilter('BUY'));
document.getElementById('orders-filter-sell-btn').addEventListener('click', () => setOrderFilter('SELL'));
document.getElementById('orders-first-btn').addEventListener('click', () => changeOrderPage('first'));
document.getElementById('orders-prev-btn').addEventListener('click', () => changeOrderPage('prev'));
document.getElementById('orders-next-btn').addEventListener('click', () => changeOrderPage('next'));
document.getElementById('orders-last-btn').addEventListener('click', () => changeOrderPage('last'));
bindClickIfPresent('config-open-btn', openConfigModal);
bindClickIfPresent('config-close-btn', closeConfigModal);
bindClickIfPresent('config-save-btn', saveConfig);
const configModal = document.getElementById('config-modal');
if (configModal) {
  configModal.addEventListener('click', ev => {
    if (ev.target === configModal) closeConfigModal();
  });
}
document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape') closeConfigModal();
});

function fmtNum(v, digits = 2) { if (v === null || v === undefined || Number.isNaN(Number(v))) return '--'; return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits }); }
function fmtMoney(v) { return v === null || v === undefined ? '--' : `$${fmtNum(v, 2)}`; }
function fmtPct(v) { const n = Number(v); return Number.isFinite(n) ? `${(n * 100).toFixed(2)}%` : '--'; }
function fmtPrice(v) { return v === null || v === undefined ? '--' : fmtNum(v, 2); }
function fmtDate(v) { if (!v) return '--'; try { return new Date(v).toLocaleString(); } catch { return v; } }
function fmtServerTime(v) {
  if (!v) return '--';
  const date = new Date(v);
  if (Number.isNaN(date.getTime())) return String(v);
  try {
    const rendered = new Intl.DateTimeFormat(undefined, {
      timeZone: SERVER_TIME_ZONE,
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    }).format(date);
    return `${rendered} GST`;
  } catch {
    return `${fmtDate(v)} GST`;
  }
}
function signedClass(v) { return Number(v) > 0 ? 'positive' : Number(v) < 0 ? 'negative' : ''; }
function humanAge(seconds) { if (seconds == null) return '--'; if (seconds < 60) return `${seconds.toFixed(2)}s`; if (seconds < 3600) return `${(seconds/60).toFixed(1)}m`; return `${(seconds/3600).toFixed(1)}h`; }
function buildKvHtml(label, value, extraClass='', changed=false) { return `<div class="kv ${changed ? 'changed' : ''}"><div class="k">${label}</div><div class="v ${extraClass}">${value}</div></div>`; }
function renderKVs(targetId, rows) { document.getElementById(targetId).innerHTML = rows.map(([k, v, extraClass, changed]) => buildKvHtml(k, v, extraClass, changed)).join(''); }
function setTextIfPresent(targetId, value) { const el = document.getElementById(targetId); if (el) el.textContent = value; return el; }
function setHtmlIfPresent(targetId, value) { const el = document.getElementById(targetId); if (el) el.innerHTML = value; return el; }
function bindClickIfPresent(targetId, handler) { const el = document.getElementById(targetId); if (el) el.addEventListener('click', handler); return el; }
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}
function normalizeTimeframe(tf) { return TIMEFRAMES.includes(tf) ? tf : '1m'; }
function optionValue(opt) { return typeof opt === 'object' ? opt.value : opt; }
function optionLabel(opt) { return typeof opt === 'object' ? opt.label : opt; }
function endpointForKey(key) { return (stateUi.aiEndpoints || []).find(ep => ep.key === key) || null; }
function endpointKeyForState(state) {
  if (state && state.aiEndpointKey) return state.aiEndpointKey;
  const base = String((state && state.aiBaseUrl) || '').replace(/\/$/, '');
  const match = (stateUi.aiEndpoints || []).find(ep => ep.baseUrl && String(ep.baseUrl).replace(/\/$/, '') === base);
  return match ? match.key : 'custom';
}
function modelOptionsForEndpoint(key, fallbackModels = []) {
  const models = (stateUi.aiEndpointModels || {})[key] || [];
  if (models.length) return models;
  return key === endpointKeyForState(stateUi.lastState || {}) ? fallbackModels : [];
}
function syncTimeframeUrl() {
  const tf = normalizeTimeframe(stateUi.timeframe);
  localStorage.setItem('tradebot-chart-timeframe', tf);
  const url = new URL(window.location.href);
  url.searchParams.set('interval', tf);
  url.searchParams.delete('auto');
  history.replaceState(null, '', `${url.pathname}${url.search}`);
  const topTf = document.getElementById('top-timeframe');
  if (topTf && topTf.tagName === 'A') topTf.href = `${url.pathname}${url.search}`;
}
function getChanged(path, value) { const old = stateUi.lastSnapshot[path]; stateUi.lastSnapshot[path] = value; return old !== undefined && old !== value; }
function acceptPayloadSeq(channel, seq, instanceId = null) {
  if (seq === undefined || seq === null) return true;
  const key = channel || 'status';
  if (instanceId && stateUi.lastSeqInstance[key] && stateUi.lastSeqInstance[key] !== instanceId) {
    stateUi.lastSeq[key] = 0;
  }
  if (instanceId) stateUi.lastSeqInstance[key] = String(instanceId);
  const next = Number(seq);
  if (!Number.isFinite(next)) return true;
  const prev = Number(stateUi.lastSeq[key] || 0);
  if (next <= prev) return false;
  stateUi.lastSeq[key] = next;
  return true;
}
function getLayoutKey() { return 'tradebot-layout-v8'; }
function gridModeLabel(mode) { return GRID_MODE_LABELS[mode] || mode; }
function dashboardModeKey(state) {
  const mode = String((state && state.gridMode) || '').trim().toLowerCase();
  if (GRID_MODES.includes(mode)) return mode;
  if (LEGACY_OPTIMIZED_MODES.has(mode)) return 'ai_optimized';
  return 'grid';
}
function dashboardModeLabel(state, aiEnabled) {
  const suffix = aiEnabled ? 'Local AI' : 'Rules';
  const mode = dashboardModeKey(state);
  if (GRID_MODES.includes(mode)) return `${GRID_MODE_LABELS[mode]} + ${suffix}`;
  if (mode === 'ai_optimized') return aiEnabled ? 'Optimized AI' : 'Rules';
  return `Grid + ${suffix}`;
}
function renderModeControl(aiEnabled) {
  const el = document.getElementById('state-mode');
  if (!el) return;
  const current = dashboardModeKey(stateUi.lastState || {});
  el.innerHTML = `
    <span class="mode-status">${aiEnabled ? 'Local AI' : 'Rules'}</span>
    <span class="mode-switch" role="group" aria-label="Grid mode">
      ${MODE_CONTROL_MODES.map(mode => {
        const active = mode === current;
        const enabled = GRID_MODES.includes(mode);
        return `<button class="mode-option ${active ? 'active' : ''}" type="button" data-grid-mode="${mode}" aria-pressed="${active ? 'true' : 'false'}"${enabled ? '' : ' disabled'}>${gridModeLabel(mode)}</button>`;
      }).join('')}
    </span>
  `;
  el.querySelectorAll('[data-grid-mode]').forEach(btn => {
    if (!btn.disabled) btn.addEventListener('click', () => setGridMode(btn.dataset.gridMode));
  });
}
function persistChartViewport() {
  localStorage.setItem('tradebot-chart-limit', String(Math.max(30, Math.min(1000, Number(stateUi.candleLimit || 180)))));
  localStorage.setItem('tradebot-chart-pan-offset', String(Math.max(0, Number(stateUi.panOffset || 0))));
}
function setCandleDetails(candle) {
  const el = document.getElementById('hover-ohlcv');
  if (!el) return;
  if (!candle) {
    el.innerHTML = '<strong>Candle</strong><span>--</span>';
    return;
  }
  const ts = candle.openTimeMs ? `${new Date(candle.openTimeMs).toLocaleString()}  ` : '';
  el.innerHTML = `<strong>Candle</strong><span>${ts}O ${fmtPrice(candle.open)}  H ${fmtPrice(candle.high)}  L ${fmtPrice(candle.low)}  C ${fmtPrice(candle.close)}  Vol ${fmtMoney(candle.volumeUsdt)}</span>`;
}
function eventTimeMs(ev) {
  const raw = ev && (ev.tsUtc || ev.ts || ev.time || ev.timestamp);
  if (!raw) return null;
  const parsed = typeof raw === 'number' ? raw : Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : null;
}
function chartPairLabel(symbol = chartSymbol()) {
  const raw = String(symbol || 'BTCUSDT').toUpperCase();
  if (raw.endsWith('USDT')) return `${raw.slice(0, -4)}/USD`;
  if (raw.endsWith('USD')) return `${raw.slice(0, -3)}/USD`;
  return raw;
}
function updateChartTitle() {
  const title = document.querySelector('#market-card h2');
  if (!title) return;
  title.textContent = `${chartPairLabel()} · ${TIMEFRAME_LABELS[stateUi.timeframe] || stateUi.timeframe} · INDEX`;
}
function showBootError(message) {
  const el = document.getElementById('boot-error');
  if (!el) return;
  el.textContent = message || '';
  el.style.display = message ? 'block' : 'none';
}

function startFreshnessTicker() {
  if (stateUi.freshnessTicker) return;
  stateUi.freshnessTicker = setInterval(() => {
    const freshLabel = document.getElementById('fresh-label');
    if (!freshLabel) return;
    const base = Number(stateUi.lastStatusFreshnessSeconds || 0);
    const receivedAt = Number(stateUi.lastStatusReceivedAt || 0);
    if (!receivedAt) return;
    const age = base + Math.max(0, (Date.now() - receivedAt) / 1000);
    freshLabel.textContent = `Live payload • ${humanAge(age)}`;
  }, 500);
}
function safeRender(name, fn) {
  try {
    fn();
    return true;
  } catch (err) {
    showBootError(`${name} render error: ${err && err.message ? err.message : err}`);
    return false;
  }
}

function renderStickySummary(status, cumulative, runtime, grid) {
  cumulative = cumulative || {};
  runtime = runtime || {};
  grid = grid || {};
  const unreal = Number((status.position || {}).unrealizedPnlUsdt || 0);
  const realized = Number((cumulative || {}).realizedPnlUsdt || 0);
  const grossRealizedRaw = (cumulative || {}).grossRealizedPnlUsdt;
  const grossRealized = Number(grossRealizedRaw === undefined || grossRealizedRaw === null ? (realized + Number((cumulative || {}).feesPaidUsdt || 0)) : grossRealizedRaw);
  const feesPaid = Number((cumulative || {}).feesPaidUsdt || 0);
  const expectedNet = grossRealized - feesPaid;
  const delta = Math.abs(realized - expectedNet);
  const stats = (status.stats || {});
  const equity = Number(status.equityUsdt || 0);
  const btcValue = Number(status.btc || 0) * Number(status.price || 0);
  const exposure = equity > 0 ? btcValue / equity : 0;
  const paused = stateUi.lastState && stateUi.lastState.paused;
  const aiEnabled = !(stateUi.lastState && stateUi.lastState.aiEnabled === false);
  const ai = aiEnabled ? (stats.ai || runtime.ai || {}) : {};
  const stateLabel = paused ? 'PAUSED' : (aiEnabled ? 'LIVE / AI-GATED' : 'LIVE / GRID');
  const nextAction = paused ? 'Paused' : (ai.riskAction ? String(ai.riskAction).split('_').join(' ') : (grid.openOrders > 0 ? 'Wait' : 'Build Grid'));
  const items = [
    ['equity', 'Total Equity', fmtMoney(equity), getChanged('summary.equity', equity), ''],
    ['realized', 'Current Net PnL', fmtMoney(realized), getChanged('summary.realized', realized), signedClass(realized)],
    ['unrealized', 'Unrealized Net PnL', fmtMoney(unreal), getChanged('summary.unrealized', unreal), signedClass(unreal)],
    ['fees', 'Total Fees Paid', fmtMoney(feesPaid), getChanged('summary.fees', feesPaid), feesPaid > 0 ? 'negative' : ''],
  ];
  const summaryEl = document.getElementById('sticky-summary');
  const itemKeys = new Set(items.map(([key]) => key));
  if (summaryEl) {
    const needsSummarySeed = !summaryEl.children.length || items.some(([key]) => !document.getElementById(`summary-${key}`));
    if (needsSummarySeed) {
      summaryEl.innerHTML = items.map(([key, label, value, changed, extraClass]) => `<div class="metric ${changed ? 'changed' : ''}" data-summary-key="${key}"><div class="label">${label}</div><div class="value ${extraClass || ''}" id="summary-${key}">${value}</div></div>`).join('');
    } else {
      items.forEach(([key, label, value, changed, extraClass]) => {
        const metric = summaryEl.querySelector(`[data-summary-key="${key}"]`);
        const valueEl = document.getElementById(`summary-${key}`);
        if (metric) metric.classList.toggle('changed', changed);
        if (metric) {
          const labelEl = metric.querySelector('.label');
          if (labelEl && labelEl.textContent !== label) labelEl.textContent = label;
        }
        if (valueEl && valueEl.textContent !== value) valueEl.textContent = value;
        if (valueEl) valueEl.className = `value ${extraClass || ''}`;
      });
    }
  }
  stateUi.lastSummaryMetricKeys = itemKeys;
  const auditFormula = `Net ${fmtMoney(realized)} = Gross ${fmtMoney(grossRealized)} - Fees ${fmtMoney(feesPaid)}`;
  const auditFormulaEl = document.getElementById('pnl-audit-formula');
  const auditStatus = document.getElementById('pnl-audit-status');
  if (auditFormulaEl) auditFormulaEl.textContent = auditFormula;
  if (auditStatus) {
    if (delta <= 0.01) {
      auditStatus.textContent = 'CONSISTENT';
      auditStatus.className = 'pill positive';
    } else {
      auditStatus.textContent = `MISMATCH ${fmtMoney(delta)}`;
      auditStatus.className = 'pill negative';
    }
  }
  setTextIfPresent('trading-state-label', stateLabel);
  renderModeControl(aiEnabled);
  const riskEl = setTextIfPresent('state-risk', exposure > 0.85 ? 'High' : (exposure > 0.55 ? 'Normal' : 'Light'));
  if (riskEl) riskEl.className = exposure > 0.85 ? 'negative' : 'positive';
  setTextIfPresent('state-exposure', fmtPct(exposure));
  setTextIfPresent('state-action', nextAction.split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' '));
  updateChartTitle();
  setHtmlIfPresent('chart-price-pill', `<span class="label">BTC Price</span><span class="${signedClass(0)}">${fmtPrice(status.price)}</span>`);
  const lastVisible = stateUi.visibleOhlcv.length ? stateUi.visibleOhlcv[stateUi.visibleOhlcv.length - 1] : {};
  setTextIfPresent('chart-quote-line', `O ${fmtPrice(lastVisible.open)}  C ${fmtPrice(status.price)}`);
}

async function toggleBotPause() {
  const btn = document.getElementById('bot-toggle-btn');
  btn.disabled = true;
  try {
    const shouldPause = !(stateUi.lastState && stateUi.lastState.paused);
    const res = await fetch(apiPath('/api/control'), { method: 'POST', headers: apiHeaders(), body: JSON.stringify({ paused: shouldPause }) });
    if (!res.ok) throw new Error(`control failed: ${res.status}`);
    await refresh();
  } finally {
    btn.disabled = false;
  }
}

async function toggleAiAssist() {
  const btn = document.getElementById('ai-toggle-btn');
  btn.disabled = true;
  try {
    const enabled = !(stateUi.lastState && stateUi.lastState.aiEnabled === false);
    const res = await fetch(apiPath('/api/config'), {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({ aiEnabled: !enabled }),
    });
    if (!res.ok) throw new Error(`AI control failed: ${res.status}`);
    await refresh();
  } finally {
    btn.disabled = false;
  }
}

async function setGridMode(mode) {
  const nextMode = GRID_MODES.includes(String(mode)) ? String(mode) : null;
  if (!nextMode) {
    renderModeControl(!(stateUi.lastState && stateUi.lastState.aiEnabled === false));
    return;
  }
  const previousState = Object.assign({}, stateUi.lastState || {});
  const previousMode = previousState.gridMode;
  stateUi.lastState = Object.assign({}, previousState, { gridMode: nextMode });
  renderModeControl(stateUi.lastState.aiEnabled !== false);
  const modeEl = document.getElementById('state-mode');
  if (modeEl) modeEl.classList.add('saving');
  try {
    const res = await fetch(apiPath('/api/config'), {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({ gridMode: nextMode }),
    });
    if (!res.ok) throw new Error(await responseErrorMessage(res, `mode save failed: ${res.status}`));
    await refresh();
  } catch (err) {
    stateUi.lastState = Object.assign({}, previousState, { gridMode: previousMode });
    renderModeControl(stateUi.lastState.aiEnabled !== false);
    showBootError(err && err.message ? err.message : 'mode save failed');
  } finally {
    const finalEl = document.getElementById('state-mode');
    if (finalEl) finalEl.classList.remove('saving');
  }
}

function changeEventPage(direction) {
  const events = stateUi.lastEvents || [];
  const totalPages = Math.max(1, Math.ceil(events.length / stateUi.eventPageSize));
  if (stateUi.eventPage < 0) stateUi.eventPage = 0;
  if (direction === 'first') stateUi.eventPage = 0;
  if (direction === 'last') stateUi.eventPage = totalPages - 1;
  if (direction === 'prev') stateUi.eventPage = Math.max(0, stateUi.eventPage - 1);
  if (direction === 'next') stateUi.eventPage = Math.min(totalPages - 1, stateUi.eventPage + 1);
  renderEvents(events.slice().reverse());
}

function renderEvents(ordered) {
  const rows = ordered || [];
  stateUi.lastEvents = rows.slice().reverse();
  const totalPages = Math.max(1, Math.ceil(rows.length / stateUi.eventPageSize));
  if (stateUi.eventPage < 0 || stateUi.eventPage >= totalPages) stateUi.eventPage = 0;
  const start = stateUi.eventPage * stateUi.eventPageSize;
  const pageRows = rows.slice(start, start + stateUi.eventPageSize);
  const renderKey = JSON.stringify([stateUi.eventPage, totalPages, pageRows.map(ev => [ev.tsUtc, ev.event, ev.price, ev.qtyBtc])]);
  const body = document.getElementById('events-body');
  if (renderKey === stateUi.lastEventsRenderKey) return;
  stateUi.lastEventsRenderKey = renderKey;
  body.innerHTML = '';
  pageRows.forEach(ev => {
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

function eventCursor(event) {
  return Number((event || {})._eventId || (event || {}).eventId || 0);
}

function applyEventsPatch(patch) {
  if (!patch) return;
  const items = Array.isArray(patch.items) ? patch.items : [];
  if (patch.mode === 'snapshot') {
    stateUi.lastEvents = items.slice();
    stateUi.eventCursor = Number(patch.cursor || eventCursor(items[items.length - 1]) || 0);
    stateUi.lastEventsRenderKey = '';
    return;
  }
  const existing = stateUi.lastEvents || [];
  const seen = new Set(existing.map(ev => eventCursor(ev)).filter(Boolean));
  const appended = items.filter(ev => {
    const cursor = eventCursor(ev);
    return cursor ? !seen.has(cursor) : true;
  });
  if (appended.length) {
    stateUi.lastEvents = existing.concat(appended).slice(-500);
  }
  stateUi.eventCursor = Math.max(Number(stateUi.eventCursor || 0), Number(patch.cursor || 0));
}

function compactAgentReason(agent) {
  const evidence = Array.isArray(agent.evidence) && agent.evidence.length ? agent.evidence[0] : '';
  return agent.summary || evidence || '--';
}

function agentDisplayName(role) {
  const text = String(role || 'portfolio_manager').replace(/_/g, ' ').trim();
  return text ? text.replace(/\b\w/g, ch => ch.toUpperCase()) : 'Portfolio Manager';
}

function normalizeAiDecisionRow(row) {
  if (!row || typeof row !== 'object' || Array.isArray(row)) return null;
  const decision = row.decision && typeof row.decision === 'object' && !Array.isArray(row.decision)
    ? Object.assign({}, row.decision)
    : Object.assign({}, row);
  if (!decision.reports && row.reports) decision.reports = row.reports;
  if (!decision.decisionId && row.decisionId) decision.decisionId = row.decisionId;
  if (!decision.tsUtc && row.tsUtc) decision.tsUtc = row.tsUtc;
  if (!decision.latencySeconds && row.latencySeconds) decision.latencySeconds = row.latencySeconds;
  return decision;
}

function aiDecisionGates(decision) {
  return [
    decision.gridAllowed === false ? 'Grid blocked' : (decision.gridAllowed === true ? 'Grid allowed' : ''),
    decision.pauseNewBuys ? 'Pause buys' : '',
    decision.allowSellsOnly ? 'Sells only' : '',
    decision.reduceExposure ? 'Reduce exposure' : '',
    decision.flattenRecommended ? 'Flatten' : '',
    decision.dryRun ? 'Dry-run' : '',
    decision.shadowMode ? 'Shadow' : '',
    decision.stale ? 'Stale' : '',
  ].filter(Boolean);
}

function aiStrategySummary(decision) {
  const profile = decision.strategyProfile && typeof decision.strategyProfile === 'object' ? decision.strategyProfile : {};
  const name = decision.strategyProfileName || profile.name || decision.recommendedMode || '--';
  const status = decision.strategyProfileStatus || '';
  const mode = decision.recommendedMode || '';
  const bits = [name, status, mode && mode !== name ? mode : ''].filter(Boolean);
  return bits.join(' · ') || '--';
}

function aiProfileSettings(decision) {
  const profile = decision.strategyProfile && typeof decision.strategyProfile === 'object' ? decision.strategyProfile : {};
  const spacing = profile.spacingPct ?? decision.recommendedSpacingPct;
  const levels = profile.levels ?? decision.recommendedLevels;
  const exposure = profile.maxExposurePct ?? decision.recommendedMaxExposurePct;
  const perLevel = profile.perLevelUsdt;
  return [
    spacing != null ? `Spacing ${fmtPct(Number(spacing))}` : '',
    levels != null ? `Levels ${levels}` : '',
    exposure != null ? `Max exposure ${fmtPct(Number(exposure))}` : '',
    perLevel != null ? `Per level ${fmtMoney(Number(perLevel))}` : '',
  ].filter(Boolean).join(' · ');
}

function aiValidationSummary(decision) {
  const report = decision.validationReport && typeof decision.validationReport === 'object' ? decision.validationReport : {};
  if (!Object.keys(report).length) return '';
  return [
    report.passed === false ? 'Validation failed' : 'Validation passed',
    report.mode || '',
    report.sampleCount != null ? `${report.sampleCount} samples` : '',
    report.error || '',
  ].filter(Boolean).join(' · ');
}

function currentDecisionAgents(decision) {
  return (Array.isArray(decision.agents) ? decision.agents : [])
    .filter(agent => agent && typeof agent === 'object' && !Array.isArray(agent));
}

function renderAgentChatNotice(message) {
  const target = document.getElementById('agent-chat-messages');
  if (!target) return;
  target.innerHTML = `
    <div class="agent-message assistant">
      <div class="agent-message-role">Read-only recovery</div>
      <div class="agent-message-body">${escapeHtml(message)}</div>
    </div>
  `;
}

function renderAgentChatShell(decision = {}, agents = []) {
  const select = document.getElementById('agent-select');
  const threadSelect = document.getElementById('agent-thread-select');
  const messages = document.getElementById('agent-chat-messages');
  const proposals = document.getElementById('agent-proposals');
  const input = document.getElementById('agent-chat-input');
  const send = document.getElementById('agent-chat-send-btn');
  if (threadSelect) threadSelect.innerHTML = '<option>New discussion</option>';
  if (proposals) proposals.innerHTML = '';
  if (input) input.value = '';
  if (send) send.disabled = false;
  const safeAgents = agents.length ? agents : [{ role: 'portfolio_manager', recommendation: '--', summary: '' }];
  const decisionId = decision.decisionId || '';
  if (!stateUi.activeAgentRole || stateUi.activeAgentDecisionId !== decisionId) {
    stateUi.activeAgentRole = safeAgents[0].role || 'portfolio_manager';
    stateUi.activeAgentDecisionId = decisionId;
  }
  if (select) {
    select.innerHTML = safeAgents.map(agent => {
      const role = String(agent.role || 'portfolio_manager');
      return `<option value="${escapeHtml(role)}"${role === stateUi.activeAgentRole ? ' selected' : ''}>${escapeHtml(agentDisplayName(role))}</option>`;
    }).join('');
  }
  if (!messages) return;
  const active = safeAgents.find(agent => String(agent.role || '') === stateUi.activeAgentRole);
  if (!active || !compactAgentReason(active) || compactAgentReason(active) === '--') {
    messages.innerHTML = '<div class="agent-chat-empty">No discussion yet</div>';
    return;
  }
  messages.innerHTML = `
    <div class="agent-message assistant">
      <div class="agent-message-role">${escapeHtml(agentDisplayName(active.role))}</div>
      <div class="agent-message-body">${escapeHtml(compactAgentReason(active))}</div>
    </div>
  `;
}

function setActiveAgentReport(role) {
  stateUi.activeAgentRole = String(role || '');
  const decision = stateUi.lastAiDecisions[stateUi.aiDecisionPage] || {};
  renderAgentChatShell(decision, currentDecisionAgents(decision));
}

function changeAiDecisionPage(direction) {
  const rows = stateUi.lastAiDecisions || [];
  const totalPages = Math.max(1, rows.length);
  if (direction === 'first') stateUi.aiDecisionPage = 0;
  if (direction === 'last') stateUi.aiDecisionPage = totalPages - 1;
  if (direction === 'prev') stateUi.aiDecisionPage = Math.max(0, Number(stateUi.aiDecisionPage || 0) - 1);
  if (direction === 'next') stateUi.aiDecisionPage = Math.min(totalPages - 1, Number(stateUi.aiDecisionPage || 0) + 1);
  renderAiDecisions(rows, { preservePage: true });
}

function renderAiDecisionPager(totalPages) {
  setTextIfPresent('ai-decisions-page-indicator', `Page ${stateUi.aiDecisionPage + 1} / ${totalPages}`);
  ['first', 'prev'].forEach(name => {
    const btn = document.getElementById(`ai-decisions-${name}-btn`);
    if (btn) btn.disabled = stateUi.aiDecisionPage <= 0;
  });
  ['next', 'last'].forEach(name => {
    const btn = document.getElementById(`ai-decisions-${name}-btn`);
    if (btn) btn.disabled = stateUi.aiDecisionPage >= totalPages - 1;
  });
}

function renderAiDecisions(decisions) {
  const rows = (Array.isArray(decisions) ? decisions : []).map(normalizeAiDecisionRow).filter(Boolean);
  stateUi.lastAiDecisions = rows.slice();
  const body = document.getElementById('ai-decisions-body');
  if (!body) return;
  if (!rows.length) {
    stateUi.aiDecisionPage = 0;
    body.innerHTML = '<div class="ai-decision-why">No AI decisions yet</div>';
    renderAgentChatShell({}, []);
    renderAiDecisionPager(1);
    ['first', 'prev', 'next', 'last'].forEach(name => {
      const btn = document.getElementById(`ai-decisions-${name}-btn`);
      if (btn) btn.disabled = true;
    });
    return;
  }
  const totalPages = rows.length;
  stateUi.aiDecisionPage = Math.max(0, Math.min(totalPages - 1, Number(stateUi.aiDecisionPage || 0)));
  const decision = rows[stateUi.aiDecisionPage] || rows[0] || {};
  const gates = aiDecisionGates(decision);
  const profileSettings = aiProfileSettings(decision);
  const validation = aiValidationSummary(decision);
  const profileMeta = [
    decision.strategyProfileId || '',
    decision.researchSnapshotId ? `research ${decision.researchSnapshotId}` : '',
  ].filter(Boolean).join(' · ');
  const agentRows = (Array.isArray(decision.agents) ? decision.agents : [])
    .filter(agent => agent && typeof agent === 'object' && !Array.isArray(agent));
  const agents = agentRows.map(agent => `
    <button class="ai-agent ${String(agent.role || '') === stateUi.activeAgentRole ? 'active' : ''}" type="button" data-agent-role="${escapeHtml(agent.role || '')}">
      <div class="ai-agent-head"><span>${escapeHtml(String(agent.role || '').replace(/_/g, ' '))}</span><span>${escapeHtml(agent.recommendation || '--')}</span></div>
      <div class="ai-agent-reason">${escapeHtml(compactAgentReason(agent))}</div>
    </button>
  `).join('');
  const risks = (Array.isArray(decision.keyRisks) ? decision.keyRisks : [])
    .slice(0, 4)
    .map(risk => `<div>${escapeHtml(risk)}</div>`)
    .join('');
  body.innerHTML = `
    <div class="ai-decision-topline">
      <div class="ai-decision-field">
        <div class="ai-decision-label">Time</div>
        <div class="ai-decision-value">${escapeHtml(fmtDate(decision.tsUtc))}</div>
        <div class="ai-decision-id">${escapeHtml(decision.decisionId || '--')}</div>
      </div>
      <div class="ai-decision-field">
        <div class="ai-decision-label">Action</div>
        <div class="ai-decision-value">${escapeHtml(decision.riskAction || '--')} · ${escapeHtml(gates.join(' · '))} · ${fmtPct(decision.confidence)}</div>
        <div class="ai-decision-id">${escapeHtml(decision.regime || '--')} / ${escapeHtml(decision.directionBias || '--')}</div>
      </div>
      <div class="ai-decision-field">
        <div class="ai-decision-label">Strategy</div>
        <div class="ai-decision-value">${escapeHtml(aiStrategySummary(decision))}</div>
        <div class="ai-decision-id">${escapeHtml(profileMeta || profileSettings || '--')}</div>
      </div>
    </div>
    <div class="ai-decision-why">
      <div class="ai-decision-label">Why</div>
      <div class="ai-decision-why-text">${escapeHtml(decision.note || decision.error || decision.projectedImpact || '--')}</div>
      ${profileSettings ? `<div class="ai-decision-id">${escapeHtml(profileSettings)}</div>` : ''}
      ${validation ? `<div class="ai-decision-id">${escapeHtml(validation)}</div>` : ''}
      ${risks ? `<div class="ai-decision-id">${risks}</div>` : ''}
      ${agents ? `<div class="ai-agent-list">${agents}</div>` : ''}
    </div>
  `;
  body.querySelectorAll('[data-agent-role]').forEach(btn => {
    btn.addEventListener('click', () => setActiveAgentReport(btn.getAttribute('data-agent-role') || ''));
  });
  renderAgentChatShell(decision, agentRows);
  renderAiDecisionPager(totalPages);
}

function setOrderTab(tab) {
  stateUi.orderTab = tab;
  stateUi.orderPage = -1;
  renderOrders();
}

function setOrderFilter(side) {
  const normalized = side === 'BUY' || side === 'SELL' ? side : 'all';
  stateUi.orderFilter = stateUi.orderFilter === normalized ? 'all' : normalized;
  stateUi.orderPage = -1;
  renderOrders();
}

function getTradeHistoryRows() {
  const history = stateUi.lastEvents.filter(ev => ev.event === 'ENTER' || ev.event === 'EXIT').map(ev => ({
    side: ev.side,
    price: ev.price,
    qty_btc: ev.qtyBtc,
    total: Number(ev.notionalUsdt || 0),
    type: ev.event,
  }));
  return history;
}

function getFilteredOrders() {
  const baseRows = stateUi.orderTab === 'history' ? getTradeHistoryRows() : (stateUi.lastOrders || []).slice();
  if (stateUi.orderFilter === 'BUY') return baseRows.filter(order => order.side === 'BUY');
  if (stateUi.orderFilter === 'SELL') return baseRows.filter(order => order.side === 'SELL');
  return baseRows;
}

function orderPatchKey(order) {
  const explicit = (order || {})._orderKey || (order || {}).id || (order || {}).orderId || (order || {}).clientOrderId;
  if (explicit) return String(explicit);
  return JSON.stringify([
    (order || {}).side || '',
    Number((order || {}).price || 0),
    Number((order || {}).qty_btc || (order || {}).qtyBtc || 0),
    Number((order || {}).total || (order || {}).notionalUsdt || 0),
    (order || {}).type || '',
  ]);
}

function applyOrdersPatch(patch) {
  if (!patch) return;
  const items = Array.isArray(patch.items) ? patch.items : [];
  if (patch.mode === 'snapshot') {
    stateUi.lastOrders = items.slice();
    stateUi.lastOrdersRenderKey = '';
    return;
  }
  const byKey = new Map((stateUi.lastOrders || []).map(order => [orderPatchKey(order), order]));
  (patch.ops || []).forEach(op => {
    if (!op || !op.key) return;
    if (op.op === 'remove') byKey.delete(String(op.key));
    if (op.op === 'upsert' && op.item) byKey.set(String(op.key), op.item);
  });
  stateUi.lastOrders = Array.from(byKey.values());
}

function changeOrderPage(direction) {
  const rows = getFilteredOrders();
  const totalPages = Math.max(1, Math.ceil(rows.length / stateUi.orderPageSize));
  if (stateUi.orderPage < 0) stateUi.orderPage = 0;
  if (direction === 'first') stateUi.orderPage = 0;
  if (direction === 'last') stateUi.orderPage = totalPages - 1;
  if (direction === 'prev') stateUi.orderPage = Math.max(0, stateUi.orderPage - 1);
  if (direction === 'next') stateUi.orderPage = Math.min(totalPages - 1, stateUi.orderPage + 1);
  renderOrders();
}

function renderOrders() {
  const rows = getFilteredOrders();
  const marketPrice = Number((stateUi.lastStatus || {}).price || 0);
  const normalizedRows = rows.map(order => {
    const orderPrice = Number(order.price || 0);
    const delta = marketPrice > 0 ? ((orderPrice / marketPrice) - 1) : null;
    const relativeLabel = stateUi.orderTab === 'history'
      ? '--'
      : ((order.side === 'BUY')
          ? (orderPrice <= marketPrice ? `Below market ${fmtPct(Math.abs(delta || 0))}` : `Above market ${fmtPct(delta || 0)}`)
          : (orderPrice >= marketPrice ? `Above market ${fmtPct(Math.abs(delta || 0))}` : `Below market ${fmtPct(Math.abs(delta || 0))}`));
    const relativeRank = stateUi.orderTab === 'history' ? 2 : ((order.side === 'BUY') ? (orderPrice <= marketPrice ? 0 : 1) : (orderPrice >= marketPrice ? 0 : 1));
    return { ...order, _delta: delta, _relativeLabel: relativeLabel, _relativeRank: relativeRank };
  });
  if (stateUi.orderTab === 'open') {
    normalizedRows.sort((a, b) => {
      if (a._relativeRank !== b._relativeRank) return a._relativeRank - b._relativeRank;
      if ((a.side || '') !== (b.side || '')) return String(a.side || '').localeCompare(String(b.side || ''));
      return Number(a.price || 0) - Number(b.price || 0);
    });
  }
  const totalPages = Math.max(1, Math.ceil(normalizedRows.length / stateUi.orderPageSize));
  if (stateUi.orderPage < 0 || stateUi.orderPage >= totalPages) stateUi.orderPage = 0;
  const start = stateUi.orderPage * stateUi.orderPageSize;
  const pageRows = normalizedRows.slice(start, start + stateUi.orderPageSize);
  const renderKey = JSON.stringify([stateUi.orderTab, stateUi.orderFilter, stateUi.orderPage, totalPages, marketPrice, pageRows.map(order => [order.side, order.price, order.qty_btc, order.total, order.type, order._relativeLabel])]);
  const body = document.getElementById('orders-body');
  if (renderKey === stateUi.lastOrdersRenderKey) return;
  stateUi.lastOrdersRenderKey = renderKey;
  body.innerHTML = '';
  pageRows.forEach(order => {
    const total = Number(order.total || (Number(order.qty_btc || 0) * Number(order.price || 0)));
    const sideClass = (order.side || '').toLowerCase() === 'buy' ? 'buy' : ((order.side || '').toLowerCase() === 'sell' ? 'sell' : '');
    const impossible = stateUi.orderTab === 'open' && ((order.side === 'BUY' && Number(order.price || 0) > marketPrice) || (order.side === 'SELL' && Number(order.price || 0) < marketPrice));
    const relativeStyle = impossible ? 'negative' : ((stateUi.orderTab === 'open' && order._relativeRank === 0) ? 'positive' : '');
    const tr = document.createElement('tr');
    tr.innerHTML = `<td class="order-side ${sideClass}">${order.side || '--'}</td><td>${fmtPrice(order.price)}</td><td class="${relativeStyle}">${order._relativeLabel}</td><td>${fmtNum(order.qty_btc, 6)}</td><td>${fmtMoney(total)}</td><td>${order.type || (stateUi.orderTab === 'history' ? 'TRADE' : 'LIMIT')}</td>`;
    body.appendChild(tr);
  });
  document.getElementById('orders-page-indicator').textContent = `Page ${stateUi.orderPage + 1} / ${totalPages}`;
  document.getElementById('orders-first-btn').disabled = stateUi.orderPage <= 0;
  document.getElementById('orders-prev-btn').disabled = stateUi.orderPage <= 0;
  document.getElementById('orders-next-btn').disabled = stateUi.orderPage >= totalPages - 1;
  document.getElementById('orders-last-btn').disabled = stateUi.orderPage >= totalPages - 1;
  document.getElementById('orders-filter-buy-btn').classList.toggle('active-filter', stateUi.orderFilter === 'BUY');
  document.getElementById('orders-filter-sell-btn').classList.toggle('active-filter', stateUi.orderFilter === 'SELL');
  document.getElementById('orders-tab-open-btn').classList.toggle('active-filter', stateUi.orderTab === 'open');
  document.getElementById('orders-tab-history-btn').classList.toggle('active-filter', stateUi.orderTab === 'history');
}

function renderLiveStatusFooter(status, state, runtime, data, grid) {
  status = status || {};
  state = state || {};
  runtime = runtime || {};
  data = data || {};
  grid = grid || {};
  const stats = status.stats || {};
  const aiEnabled = state.aiEnabled !== false;
  const ai = aiEnabled ? (stats.ai || runtime.ai || {}) : {};
  const endpointLabel = aiEndpointDisplayLabel(data, state);
  const aiMode = aiModeLabel(aiEnabled, ai);
  renderKVs('status-list', [
    ['Status timestamp', fmtDate(status.tsUtc), '', getChanged('status.tsUtc', status.tsUtc)],
    ['Runtime saved', fmtDate(runtime.savedAt), '', getChanged('runtime.savedAt', runtime.savedAt)],
    ['Grid status', grid.openOrders > 0 ? 'Active' : 'Idle', '', getChanged('status.grid', grid.openOrders)],
    ['AI endpoint', endpointLabel, '', getChanged('status.aiEndpoint', endpointLabel)],
    ['AI model', ai.model || state.aiModel || '--', '', getChanged('status.aiModel', ai.model || state.aiModel)],
    ['AI confidence', ai.confidence != null ? fmtPct(Number(ai.confidence)) : '--', '', getChanged('status.aiConfidence', ai.confidence)],
    ['AI mode', aiMode || '--', '', getChanged('status.aiMode', aiMode)],
  ]);
}

function titleCaseLabel(value) {
  return String(value || '')
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function aiEndpointDisplayLabel(data, state) {
  data = data || {};
  state = state || {};
  if (data.aiEndpointLabel) return data.aiEndpointLabel;
  const key = data.aiEndpointKey || state.aiEndpointKey || '';
  const endpoint = endpointForKey(key);
  if (endpoint && endpoint.label) return endpoint.label;
  if (key) return titleCaseLabel(key);
  return state.aiBaseUrl || '--';
}

function aiModeLabel(aiEnabled, ai) {
  if (!aiEnabled) return 'off';
  ai = ai || {};
  const parts = [];
  if (ai.dryRun) parts.push('dry-run');
  if (ai.shadowMode) parts.push('shadow');
  if (ai.stale) {
    const reason = String(ai.source || '').replace(/_/g, ' ').trim();
    parts.push(reason ? `stale (${reason})` : 'stale');
  } else {
    parts.push('live');
  }
  return parts.join(' / ');
}

function impactBars(level, color = 'orange') {
  return `<div class="impact-bars">${Array.from({ length: 8 }, (_, i) => `<span class="${i < level ? `on ${color}` : ''}"></span>`).join('')}</div>`;
}

function newsSentiment(title) {
  const text = String(title || '').toLowerCase();
  if (['surge', 'inflow', 'rally', 'rise', 'gain'].some(word => text.includes(word))) return 'Bullish';
  if (['fall', 'drop', 'outflow', 'hack', 'loss'].some(word => text.includes(word))) return 'Bearish';
  return 'Neutral';
}

function newsTimestamp(card) {
  const raw = String((card && (card.publishedUtc || card.age)) || '').trim();
  if (!raw || raw === 'latest' || raw === '30m refresh') return 0;
  const parsed = Date.parse(raw);
  if (Number.isFinite(parsed)) return parsed;
  const normalized = Date.parse(raw.replace(' ', 'T'));
  return Number.isFinite(normalized) ? normalized : 0;
}

function newsAgeLabel(card) {
  const raw = String((card && (card.age || card.publishedUtc)) || 'latest');
  return raw.slice(0, 16).replace('T', ' ');
}

function normalizeNewsCard(card, fromRaw = false) {
  const title = String((card && card.title) || 'Crypto market update').trim();
  if (!title || title === 'Awaiting fresh crypto headlines') return null;
  const lower = title.toLowerCase();
  const publishedUtc = String((card && card.publishedUtc) || '').trim();
  return {
    title,
    source: (card && card.source) || (fromRaw ? 'RSS' : 'Local AI'),
    age: newsAgeLabel(card || {}),
    publishedUtc,
    sentiment: (card && card.sentiment) || newsSentiment(title),
    impact: (card && card.impact) || (lower.includes('bitcoin') || lower.includes('btc') ? 6 : 4),
    url: (card && card.url) || '',
  };
}

function normalizedNewsCards(intelligence) {
  const cards = [];
  const seen = new Set();
  const cutoffMs = Date.now() - (NEWS_HISTORY_DAYS * 24 * 60 * 60 * 1000);
  const pushCard = (card, fromRaw = false) => {
    if (!card || typeof card !== 'object' || Array.isArray(card)) return;
    const normalized = normalizeNewsCard(card, fromRaw);
    if (!normalized) return;
    const ts = newsTimestamp(normalized);
    if (ts && ts < cutoffMs) return;
    const key = String(normalized.url || normalized.title).trim().toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    cards.push({ ...normalized, _sortTs: ts });
  };
  if (Array.isArray(intelligence && intelligence.newsCards)) {
    intelligence.newsCards.forEach(card => pushCard(card, false));
  }
  const rawNews = Array.isArray(intelligence && intelligence.rawNews) ? intelligence.rawNews : [];
  rawNews.forEach(item => pushCard(item, true));
  cards.sort((a, b) => Number(b._sortTs || 0) - Number(a._sortTs || 0));
  if (!cards.length) {
    return [{
      title: 'Awaiting fresh crypto headlines',
      source: 'Local',
      age: '30m refresh',
      sentiment: 'Neutral',
      impact: 3,
      url: '',
    }];
  }
  return cards.map(card => {
    const { _sortTs, ...clean } = card;
    return clean;
  });
}

function newsPageRows(cards, page = stateUi.newsPage) {
  const rows = Array.isArray(cards) ? cards : [];
  const totalPages = Math.max(1, Math.ceil(rows.length / stateUi.newsPageSize));
  const pageIndex = Math.max(0, Math.min(totalPages - 1, Number(page || 0)));
  const start = pageIndex * stateUi.newsPageSize;
  return {
    rows: rows.slice(start, start + stateUi.newsPageSize),
    totalPages,
    page: pageIndex,
    totalEvents: rows.length,
  };
}

function renderNewsPager(totalPages, totalEvents) {
  const suffix = totalEvents === 1 ? 'story' : 'stories';
  setTextIfPresent('news-page-indicator', `Page ${stateUi.newsPage + 1} / ${totalPages} • ${totalEvents} ${suffix}`);
  ['first', 'prev'].forEach(name => {
    const btn = document.getElementById(`news-${name}-btn`);
    if (btn) btn.disabled = stateUi.newsPage <= 0;
  });
  ['next', 'last'].forEach(name => {
    const btn = document.getElementById(`news-${name}-btn`);
    if (btn) btn.disabled = stateUi.newsPage >= totalPages - 1;
  });
}

function renderNewsCards(cards) {
  const pageData = newsPageRows(cards);
  stateUi.newsPage = pageData.page;
  document.getElementById('news-stack').innerHTML = pageData.rows.map(card => {
    const sentiment = card.sentiment || 'Neutral';
    const color = sentiment.toLowerCase() === 'bullish' ? 'green' : 'orange';
    const url = card.url || '';
    const title = url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(card.title || 'Crypto market update')}</a>` : escapeHtml(card.title || 'Crypto market update');
    return `
      <div class="news-card">
        <div class="news-title">${title}</div>
        <div class="news-row">
          <div><span class="source-chip">${escapeHtml(card.source || 'Local AI')}</span> <span class="news-meta">${escapeHtml(card.age || '30m refresh')}</span></div>
          <span class="sentiment-chip ${color === 'green' ? 'bullish' : 'neutral'}">${escapeHtml(sentiment)}</span>
        </div>
        <div class="news-meta" style="margin-bottom:6px">Impact</div>
        ${impactBars(card.impact || 4, color)}
      </div>
    `;
  }).join('');
  renderNewsPager(pageData.totalPages, pageData.totalEvents);
}

function changeNewsPage(direction) {
  const cards = normalizedNewsCards(stateUi.lastIntelligence || {});
  const totalPages = Math.max(1, Math.ceil(cards.length / stateUi.newsPageSize));
  if (direction === 'first') stateUi.newsPage = 0;
  if (direction === 'last') stateUi.newsPage = totalPages - 1;
  if (direction === 'prev') stateUi.newsPage = Math.max(0, stateUi.newsPage - 1);
  if (direction === 'next') stateUi.newsPage = Math.min(totalPages - 1, stateUi.newsPage + 1);
  renderNewsCards(cards);
}

function renderIntelligence(status, cumulative, runtime, intelligence) {
  const hasIntelligenceNews = intelligence
    && ((Array.isArray(intelligence.newsCards) && intelligence.newsCards.length)
      || (Array.isArray(intelligence.rawNews) && intelligence.rawNews.length));
  if (hasIntelligenceNews) {
    renderNewsCards(normalizedNewsCards(intelligence));
    return;
  }
  cumulative = cumulative || {};
  runtime = runtime || {};
  const realized = Number((cumulative || {}).realizedPnlUsdt || 0);
  const unreal = Number((status.position || {}).unrealizedPnlUsdt || 0);
  const ai = ((status.stats || {}).ai || runtime.ai || {});
  const aiTone = ai.riskAction ? String(ai.riskAction).split('_').join(' ') : 'wait';
  const cards = [
    {
      title: 'ETF Demand Strong, Near-Term Profit Taking',
      source: 'CryptoTimes',
      age: 'today',
      sentiment: 'Bullish',
      color: 'green',
      impact: 7,
    },
    {
      title: 'Fed Caution Keeps Dollar / Yields In Focus',
      source: 'Macro',
      age: 'live setup',
      sentiment: 'Neutral',
      color: 'orange',
      impact: 4,
    },
    {
      title: `Bot PnL: ${fmtMoney(realized)} Realized, ${fmtMoney(unreal)} Unrealized`,
      source: 'Local AI',
      age: aiTone,
      sentiment: Number(realized + unreal) >= 0 ? 'Bullish' : 'Neutral',
      color: Number(realized + unreal) >= 0 ? 'green' : 'orange',
      impact: Math.min(8, Math.max(2, Math.round(Math.abs(realized + unreal) / 25) + 2)),
    },
    {
      title: 'ETF Flow And Session Liquidity Watch',
      source: 'Flow',
      age: 'daily',
      sentiment: 'Neutral',
      color: 'orange',
      impact: 5,
    },
    {
      title: 'Grid Exposure Check Before Next Macro Window',
      source: 'Risk',
      age: 'live setup',
      sentiment: 'Neutral',
      color: 'orange',
      impact: 4,
    },
  ];
  while (cards.length < NEWS_PAGE_SIZE) {
    cards.push({
      title: 'Awaiting fresh crypto headlines',
      source: 'Local',
      age: '30m refresh',
      sentiment: 'Neutral',
      color: 'orange',
      impact: 3,
    });
  }
  stateUi.newsPage = 0;
  renderNewsCards(cards);
}

function regimeRows(status, runtime) {
  runtime = runtime || {};
  const ohlcv = stateUi.visibleOhlcv || [];
  const first = ohlcv[0], last = ohlcv[ohlcv.length - 1];
  const change = first && last ? (Number(last.close) / Number(first.open) - 1) : 0;
  const ranges = ohlcv.map(c => Number(c.high || 0) - Number(c.low || 0));
  const avgRange = ranges.length ? ranges.reduce((a, b) => a + b, 0) / ranges.length : 0;
  const price = Number(status.price || (last && last.close) || 0);
  const volPct = price > 0 ? avgRange / price : 0;
  const exposure = Number(status.equityUsdt || 0) > 0 ? (Number(status.btc || 0) * price) / Number(status.equityUsdt || 1) : 0;
  const ai = ((status.stats || {}).ai || runtime.ai || {});
  return [
    ['Trend', Math.abs(change) < 0.015 ? 'Range' : (change > 0 ? 'Uptrend' : 'Downtrend'), 0.68, 'Price oscillating inside range', 'blue'],
    ['Liquidity', exposure > 0.55 ? 'Improving' : 'Balanced', 0.74, 'Capital available for grid', 'green'],
    ['Volatility', volPct > 0.012 ? 'Elevated' : 'Calm', 0.62, 'ATR proxy from live candles', 'orange'],
    ['ETF Demand', 'Strong', 0.82, 'April inflows remain key driver', 'green'],
    ['Macro Pressure', 'Tight', 0.58, 'Rates and USD sensitivity', 'orange'],
    ['Execution Quality', ai.stale ? 'Watch' : 'Good', ai.stale ? 0.45 : 0.78, ai.stale ? 'AI feed stale or fallback' : 'Local checks healthy', ai.stale ? 'orange' : 'green'],
  ];
}

function drawRegimeRadar(rows) {
  const canvas = document.getElementById('regime-radar');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;
  ctx.scale(devicePixelRatio, devicePixelRatio);
  ctx.clearRect(0, 0, rect.width, rect.height);
  const cx = rect.width / 2, cy = rect.height / 2 + 4;
  const r = Math.min(rect.width, rect.height) * 0.34;
  const labels = rows.map(row => row[0]);
  const points = rows.map((row, i) => {
    const angle = -Math.PI / 2 + (i / rows.length) * Math.PI * 2;
    return { x: cx + Math.cos(angle) * r * row[2], y: cy + Math.sin(angle) * r * row[2], angle, label: labels[i] };
  });
  ctx.strokeStyle = 'rgba(23,23,19,.16)';
  ctx.lineWidth = 1;
  for (let ring = 1; ring <= 3; ring++) {
    ctx.beginPath();
    rows.forEach((_, i) => {
      const angle = -Math.PI / 2 + (i / rows.length) * Math.PI * 2;
      const x = cx + Math.cos(angle) * r * ring / 3;
      const y = cy + Math.sin(angle) * r * ring / 3;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.closePath(); ctx.stroke();
  }
  rows.forEach((_, i) => {
    const angle = -Math.PI / 2 + (i / rows.length) * Math.PI * 2;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(angle) * r, cy + Math.sin(angle) * r); ctx.stroke();
  });
  ctx.beginPath();
  points.forEach((p, i) => { if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y); });
  ctx.closePath();
  ctx.fillStyle = 'rgba(23,103,194,.14)';
  ctx.strokeStyle = '#1767c2';
  ctx.lineWidth = 3;
  ctx.fill(); ctx.stroke();
  ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--muted');
  ctx.font = '700 11px Inter, sans-serif';
  ctx.textAlign = 'center';
  labels.forEach((label, i) => {
    const angle = -Math.PI / 2 + (i / rows.length) * Math.PI * 2;
    ctx.fillText(label.split(' ')[0], cx + Math.cos(angle) * (r + 28), cy + Math.sin(angle) * (r + 22));
  });
}

function renderRegime(status, runtime, intelligence) {
  const rows = (intelligence && intelligence.regimeSignals && intelligence.regimeSignals.length)
    ? intelligence.regimeSignals.map(row => [row.signal, row.status, Number(row.score || 0.5), row.note, row.tone || 'orange'])
    : regimeRows(status, runtime);
  document.getElementById('signal-table').innerHTML = `
    <div class="signal-row header"><div>Signal</div><div>Status</div><div>Trend</div><div>Notes</div></div>
    ${rows.map(row => `<div class="signal-row"><strong>${row[0]}</strong><span class="status-chip ${row[4] === 'green' ? 'good' : 'warn'}">${row[1]}</span><span class="spark" style="color:${row[4] === 'green' ? 'var(--green)' : row[4] === 'blue' ? 'var(--blue)' : 'var(--btc)'}"></span><span>${row[3]}</span></div>`).join('')}
  `;
  drawRegimeRadar(rows);
  const trend = rows[0][1];
  const finalRegime = (intelligence && intelligence.finalRegime) || {};
  document.getElementById('final-regime-title').textContent = finalRegime.title || (trend === 'Range' ? 'Range Consolidation' : trend);
  document.getElementById('final-regime-copy').textContent = finalRegime.copy || (trend === 'Range'
    ? 'Choppy price action within established range. Maintain grid discipline and capital efficiency.'
    : 'Directional pressure is rising. Keep exits fee-aware and let AI gating manage exposure.');
  const updated = document.getElementById('regime-updated');
  if (updated && intelligence && intelligence.generatedAtUtc) updated.textContent = `AI refresh ${intelligence.generatedAtUtc.slice(11, 16)}`;
}

function shiftMonth(year, monthIndex, delta) {
  const total = (year * 12) + monthIndex + delta;
  return { year: Math.floor(total / 12), monthIndex: ((total % 12) + 12) % 12 };
}

function daysInMonth(year, monthIndex) {
  return new Date(Date.UTC(year, monthIndex + 1, 0)).getUTCDate();
}

function macroCalendarEvents(serverTimeUtc) {
  const current = new Date(serverTimeUtc || Date.now());
  const nowUtcMs = Number.isNaN(current.getTime()) ? Date.now() : current.getTime();
  const gstNow = new Date(nowUtcMs + GST_OFFSET_MS);
  const year = gstNow.getUTCFullYear();
  const monthIndex = gstNow.getUTCMonth();
  const events = [];
  for (let offset = -MACRO_CALENDAR_LOOKBACK_MONTHS; offset <= MACRO_CALENDAR_LOOKAHEAD_MONTHS; offset += 1) {
    const shifted = shiftMonth(year, monthIndex, offset);
    for (let day = 1; day <= daysInMonth(shifted.year, shifted.monthIndex); day += 1) {
      MACRO_CALENDAR_TEMPLATES.forEach(([title, hour, minute, impact, color, upcoming, completed, geoFlag, geoLabel]) => {
        const eventUtcMs = Date.UTC(shifted.year, shifted.monthIndex, day, hour - 4, minute, 0, 0);
        const eventGst = new Date(eventUtcMs + GST_OFFSET_MS);
        const done = nowUtcMs >= eventUtcMs;
        const hour12 = hour % 12 || 12;
        const ampm = hour < 12 ? 'AM' : 'PM';
        events.push({
          title,
          year: eventGst.getUTCFullYear(),
          month: eventGst.getUTCMonth() + 1,
          monthName: eventGst.toLocaleDateString(undefined, { month: 'short' }),
          day: eventGst.getUTCDate(),
          date: eventGst.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }),
          time: `${hour12}:${String(minute).padStart(2, '0')} ${ampm} GST`,
          sortTs: eventUtcMs,
          impact,
          color,
          geoFlag,
          geoLabel,
          status: done ? 'Completed' : 'Upcoming',
          summary: done ? completed : upcoming,
        });
      });
    }
  }
  return events;
}

function macroCalendarState(kind) {
  const key = MACRO_CALENDAR_KINDS[kind] ? kind : 'completed';
  if (!stateUi.macroCalendars[key]) {
    stateUi.macroCalendars[key] = { page: 0, monthFilter: '', yearFilter: '', eventFilter: '' };
  }
  return stateUi.macroCalendars[key];
}

function filteredMacroCalendarEvents(events, kind = 'completed') {
  const calendarKind = MACRO_CALENDAR_KINDS[kind] ? kind : 'completed';
  const calendar = macroCalendarState(calendarKind);
  return (events || []).filter(ev => {
    const monthOk = !calendar.monthFilter || String(ev.month) === String(calendar.monthFilter);
    const yearOk = !calendar.yearFilter || String(ev.year) === String(calendar.yearFilter);
    const eventOk = !calendar.eventFilter || ev.title === calendar.eventFilter;
    const statusOk = ev.status === MACRO_CALENDAR_KINDS[calendarKind].status;
    return monthOk && yearOk && eventOk && statusOk;
  });
}

function sortedMacroCalendarRows(events, kind = 'completed') {
  const status = MACRO_CALENDAR_KINDS[kind] ? MACRO_CALENDAR_KINDS[kind].status : 'Completed';
  return (events || [])
    .filter(ev => ev.status === status)
    .sort((a, b) => {
      const left = Number(a.sortTs || 0);
      const right = Number(b.sortTs || 0);
      return status === 'Completed' ? right - left : left - right;
    });
}

function macroCalendarPageRows(events, kind = 'completed', page = macroCalendarState(kind).page) {
  const pageData = macroCalendarGroupedPageRows(events, kind, page);
  return {
    rows: pageData.rows,
    totalPages: pageData.totalPages,
    page: pageData.page,
    totalEvents: pageData.totalEvents,
  };
}

function macroCalendarDayKey(event) {
  return [
    event.year || '',
    String(event.month || '').padStart(2, '0'),
    String(event.day || '').padStart(2, '0'),
  ].join('-');
}

function macroCalendarDayGroups(rows) {
  const groups = [];
  (rows || []).forEach(event => {
    const key = macroCalendarDayKey(event);
    const lastGroup = groups[groups.length - 1];
    if (lastGroup && lastGroup.key === key) {
      lastGroup.events.push(event);
      return;
    }
    groups.push({ key, events: [event] });
  });
  return groups;
}

function macroCalendarGroupPages(groups) {
  const dayGroups = groups || [];
  const daysPerPage = Math.max(1, Number(MACRO_CALENDAR_WINDOW_DAYS || 7));
  const pages = [];
  for (let start = 0; start < dayGroups.length; start += daysPerPage) {
    pages.push(dayGroups.slice(start, start + daysPerPage));
  }
  return pages.length ? pages : [[]];
}

function macroCalendarGroupedPageRows(events, kind = 'completed', page = macroCalendarState(kind).page) {
  const rowsSource = sortedMacroCalendarRows(events, kind);
  const groupPages = macroCalendarGroupPages(macroCalendarDayGroups(rowsSource));
  const pages = Math.max(1, groupPages.length);
  const pageIndex = Math.max(0, Math.min(pages - 1, Number(page || 0)));
  const groups = rowsSource.length ? groupPages[pageIndex] : [];
  return {
    groups,
    rows: groups.flatMap(group => group.events || []),
    totalPages: pages,
    page: pageIndex,
    totalEvents: rowsSource.length,
  };
}

function setSelectOptions(selectId, options, selectedValue, emptyLabel) {
  const select = document.getElementById(selectId);
  if (!select) return;
  select.innerHTML = [
    `<option value="">${escapeHtml(emptyLabel)}</option>`,
    ...options.map(opt => `<option value="${escapeHtml(opt.value)}"${String(opt.value) === String(selectedValue) ? ' selected' : ''}>${escapeHtml(opt.label)}</option>`),
  ].join('');
}

function populateMacroCalendarFilters(kind, events) {
  const calendar = macroCalendarState(kind);
  const prefix = MACRO_CALENDAR_KINDS[kind].prefix;
  const months = Array.from(new Map((events || []).map(ev => [String(ev.month), {
    value: String(ev.month),
    label: ev.monthName || String(ev.month),
  }])).values()).sort((a, b) => Number(a.value) - Number(b.value));
  const years = Array.from(new Set((events || []).map(ev => ev.year))).sort((a, b) => a - b)
    .map(year => ({ value: String(year), label: String(year) }));
  const eventTypes = Array.from(new Set((events || []).map(ev => ev.title))).sort()
    .map(title => ({ value: title, label: title }));
  setSelectOptions(`${prefix}-month-filter`, months, calendar.monthFilter, 'All months');
  setSelectOptions(`${prefix}-year-filter`, years, calendar.yearFilter, 'All years');
  setSelectOptions(`${prefix}-event-filter`, eventTypes, calendar.eventFilter, 'All events');
}

function setMacroCalendarFilter(calendarKind, filterKind, value) {
  const calendar = macroCalendarState(calendarKind);
  if (filterKind === 'month') calendar.monthFilter = value || '';
  if (filterKind === 'year') calendar.yearFilter = value || '';
  if (filterKind === 'event') calendar.eventFilter = value || '';
  calendar.page = 0;
  renderMacroCalendar(stateUi.lastMacroCalendarServerTime);
}

function renderMacroCalendarPager(kind, totalPages, totalEvents) {
  const prefix = MACRO_CALENDAR_KINDS[kind].prefix;
  const calendar = macroCalendarState(kind);
  setTextIfPresent(`${prefix}-page-indicator`, `Page ${calendar.page + 1} / ${totalPages} • ${totalEvents} events`);
  ['first', 'prev'].forEach(name => {
    const btn = document.getElementById(`${prefix}-${name}-btn`);
    if (btn) btn.disabled = calendar.page <= 0;
  });
  ['next', 'last'].forEach(name => {
    const btn = document.getElementById(`${prefix}-${name}-btn`);
    if (btn) btn.disabled = calendar.page >= totalPages - 1;
  });
}

function changeMacroCalendarPage(kind, direction) {
  const calendarKind = MACRO_CALENDAR_KINDS[kind] ? kind : 'completed';
  const calendar = macroCalendarState(calendarKind);
  const pages = macroCalendarGroupedPageRows(
    filteredMacroCalendarEvents(macroCalendarEvents(stateUi.lastMacroCalendarServerTime), calendarKind),
    calendarKind,
  ).totalPages;
  if (direction === 'first') calendar.page = 0;
  if (direction === 'last') calendar.page = pages - 1;
  if (direction === 'prev') calendar.page = Math.max(0, Number(calendar.page || 0) - 1);
  if (direction === 'next') calendar.page = Math.min(pages - 1, Number(calendar.page || 0) + 1);
  renderMacroCalendar(stateUi.lastMacroCalendarServerTime);
}

function macroCalendarDeltaLabel(event, nowUtcMs) {
  const eventMs = Number(event.sortTs || nowUtcMs || Date.now());
  const diffMs = eventMs - Number(nowUtcMs || Date.now());
  const totalMinutes = Math.max(0, Math.floor(Math.abs(diffMs) / 60000));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  const prefix = diffMs < 0 ? '-' : '';
  if (hours > 99) {
    const days = Math.floor(hours / 24);
    const remHours = hours % 24;
    return `${prefix}${days}d${remHours}h`;
  }
  return `${prefix}${hours}h${String(minutes).padStart(2, '0')}m`;
}

function macroCalendarBadge(event, nowUtcMs) {
  const delta = macroCalendarDeltaLabel(event, nowUtcMs);
  const titleBase = `${event.date || ''} ${event.time || ''}`;
  const title = event.status === 'Upcoming' ? `${titleBase} - ${delta}` : titleBase;
  const deltaHtml = event.status === 'Upcoming'
    ? `<span class="calendar-icon-delta">${escapeHtml(delta)}</span>`
    : '';
  return `
    <div class="calendar-icon" style="background:${escapeHtml(event.color || '#1767c2')}" title="${escapeHtml(title)}">
      <span class="calendar-icon-day">${escapeHtml(event.day || '')}</span>
      <span class="calendar-icon-month">${escapeHtml(event.monthName || '')}</span>
      ${deltaHtml}
    </div>
  `;
}

function macroCalendarEventTime(event, nowUtcMs) {
  const time = event.time || '';
  if (event.status !== 'Upcoming') return time;
  return `${time} (${macroCalendarDeltaLabel(event, nowUtcMs)})`;
}

function renderMacroCalendar(serverTimeUtc) {
  stateUi.lastMacroCalendarServerTime = serverTimeUtc || stateUi.lastMacroCalendarServerTime || new Date().toISOString();
  const current = new Date(stateUi.lastMacroCalendarServerTime);
  const nowUtcMs = Number.isNaN(current.getTime()) ? Date.now() : current.getTime();
  const events = macroCalendarEvents(stateUi.lastMacroCalendarServerTime);
  Object.keys(MACRO_CALENDAR_KINDS).forEach(kind => {
    const prefix = MACRO_CALENDAR_KINDS[kind].prefix;
    const target = document.getElementById(`${prefix}-calendar`);
    if (!target) return;
    const kindEvents = events.filter(ev => ev.status === MACRO_CALENDAR_KINDS[kind].status);
    populateMacroCalendarFilters(kind, kindEvents);
    const filtered = filteredMacroCalendarEvents(kindEvents, kind);
    const pageData = macroCalendarGroupedPageRows(filtered, kind);
    const calendar = macroCalendarState(kind);
    calendar.page = pageData.page;
    if (!pageData.rows.length) {
      target.innerHTML = `<div class="calendar-empty">${escapeHtml(MACRO_CALENDAR_KINDS[kind].empty)}</div>`;
      renderMacroCalendarPager(kind, 1, 0);
      return;
    }
    target.innerHTML = pageData.groups.map(group => `
      <div class="calendar-day-group ${(group.events[0] || {}).status.toLowerCase()}">
        ${macroCalendarBadge(group.events[0] || {}, nowUtcMs)}
        <div class="calendar-day-events">
          ${(group.events || []).map(ev => {
            const flag = ev.geoFlag
              ? ` <span class="calendar-event-flag" title="${escapeHtml(ev.geoLabel || '')}">${escapeHtml(ev.geoFlag)}</span>`
              : '';
            return `
            <div class="calendar-row ${ev.status.toLowerCase()}">
              <div class="calendar-main">
                <strong>${escapeHtml(ev.title)}${flag}</strong>
                <div class="calendar-event-time">${escapeHtml(macroCalendarEventTime(ev, nowUtcMs))}</div>
                <div class="calendar-summary">${escapeHtml(ev.status)} - ${escapeHtml(ev.summary)}</div>
              </div>
              <div class="calendar-impact"><div class="calendar-meta" style="margin-bottom:5px">Impact</div><div class="impact-dots">${Array.from({ length: 3 }, (_, i) => `<span class="${i < ev.impact ? 'on' : ''}"></span>`).join('')}</div></div>
            </div>
          `; }).join('')}
        </div>
      </div>
    `).join('');
    renderMacroCalendarPager(kind, pageData.totalPages, pageData.totalEvents);
  });
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
  const visibleRows = getVisibleOhlcv(allRows);
  const canvas = document.getElementById('market-chart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;
  ctx.scale(devicePixelRatio, devicePixelRatio);
  ctx.clearRect(0, 0, rect.width, rect.height);
  if (!visibleRows.length) {
    setCandleDetails(null);
    return;
  }
  const fallback = canvas.parentElement ? canvas.parentElement.querySelector('.server-chart-fallback') : null;
  if (fallback) fallback.remove();
  const priceArea = rect.height - 8;
  const padL = 22, padR = 72, padT = 18, padB = 24;
  const highs = visibleRows.map(c => c.high);
  const lows = visibleRows.map(c => c.low);
  const maxP = Math.max(...highs), minP = Math.min(...lows), spanP = Math.max(1e-9, maxP - minP);
  const chartW = rect.width - padL - padR;
  const candleGap = chartW / Math.max(visibleRows.length, 1);
  const candleW = Math.max(5, candleGap * 0.62);
  const priceToY = price => padT + (maxP - Number(price || 0)) / spanP * (priceArea - padT - padB);
  ctx.strokeStyle = 'rgba(23,23,19,.09)';
  ctx.lineWidth = 1;
  for (let i = 0; i < 6; i++) {
    const y = padT + ((priceArea - padT - padB) * i / 5);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(rect.width - padR, y); ctx.stroke();
    const price = maxP - ((maxP - minP) * i / 5);
    ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--muted');
    ctx.font = '12px Inter, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(fmtPrice(price), rect.width - padR + 10, y + 4);
  }
  const hoverState = stateUi.chartHoverIndex;
  const markerBuckets = new Map();
  (stateUi.lastEvents || []).forEach(ev => {
    if (ev.event !== 'ENTER' && ev.event !== 'EXIT') return;
    const ts = eventTimeMs(ev);
    if (!Number.isFinite(ts)) return;
    const idx = visibleRows.findIndex((c, i) => {
      const open = Number(c.openTimeMs || 0);
      const close = Number(c.closeTimeMs || visibleRows[i + 1]?.openTimeMs || (open + (intervalDurationMs(stateUi.timeframe) || 0)));
      return ts >= open && ts <= close;
    });
    if (idx < 0) return;
    if (!markerBuckets.has(idx)) markerBuckets.set(idx, []);
    markerBuckets.get(idx).push(ev);
  });
  visibleRows.forEach((c, i) => {
    const x = padL + candleGap * (i + 0.5);
    const yHigh = priceToY(c.high);
    const yLow = priceToY(c.low);
    const yOpen = priceToY(c.open);
    const yClose = priceToY(c.close);
    const up = c.close >= c.open;
    const color = up ? '#2fc486' : '#d54545';
    ctx.strokeStyle = color;
    ctx.beginPath(); ctx.moveTo(x, yHigh); ctx.lineTo(x, yLow); ctx.stroke();
    const top = Math.min(yOpen, yClose), body = Math.max(2, Math.abs(yClose - yOpen));
    ctx.fillStyle = color;
    ctx.fillRect(x - candleW / 2, top, candleW, body);
    if (hoverState === i) {
      ctx.strokeStyle = '#f7931a';
      ctx.strokeRect(x - candleW / 2 - 2, top - 2, candleW + 4, body + 4);
    }
  });
  markerBuckets.forEach((events, idx) => {
    const c = visibleRows[idx];
    const x = padL + candleGap * (idx + 0.5);
    events.slice(-4).forEach((ev, markerIdx) => {
      const isEntry = ev.event === 'ENTER';
      const label = isEntry ? 'B' : 'S';
      const color = isEntry ? '#0d8a2f' : '#d54545';
      const anchorY = Number.isFinite(Number(ev.price)) ? priceToY(ev.price) : priceToY(isEntry ? c.low : c.high);
      const offset = 16 + markerIdx * 18;
      const y = isEntry
        ? Math.min(priceArea - padB - 11, anchorY + offset)
        : Math.max(padT + 11, anchorY - offset);
      ctx.save();
      ctx.beginPath();
      ctx.arc(x, y, 10, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = 'rgba(255,253,248,.95)';
      ctx.stroke();
      ctx.fillStyle = '#fffdf8';
      ctx.font = '900 11px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, x, y + 0.5);
      ctx.restore();
    });
  });
  const latest = visibleRows[visibleRows.length - 1];
  const delta = latest.open ? latest.close - latest.open : 0;
  const deltaPct = latest.open ? delta / latest.open : 0;
  setHtmlIfPresent('market-legend', `<span><b>${escapeHtml(latest.symbol || 'BTC/USDT')}</b></span><span>O ${fmtPrice(latest.open)}</span><span>H ${fmtPrice(latest.high)}</span><span>L ${fmtPrice(latest.low)}</span><span>C ${fmtPrice(latest.close)}</span><span class="${signedClass(delta)}">${delta >= 0 ? '+' : ''}${fmtMoney(delta)} (${deltaPct >= 0 ? '+' : ''}${fmtPct(deltaPct)})</span>`);
  const updateHover = x => {
    const idx = Math.max(0, Math.min(visibleRows.length - 1, Math.floor((x - padL) / Math.max(1, candleGap))));
    stateUi.chartHoverIndex = idx;
    const c = visibleRows[idx];
    setCandleDetails(c);
  };
  setCandleDetails(hoverState != null && visibleRows[hoverState] ? visibleRows[hoverState] : latest);
  const applyPanFromClientX = clientX => {
    if (!(stateUi.chartDragState && stateUi.chartDragState.active)) return;
    const dx = clientX - stateUi.chartDragState.startX;
    const shift = Math.round(dx / Math.max(1, candleGap));
    const maxOffset = Math.max(0, allRows.length - Math.max(30, Number(stateUi.candleLimit || DEFAULT_LIMITS[stateUi.timeframe] || 180)));
    stateUi.panOffset = Math.max(0, Math.min(maxOffset, stateUi.chartDragState.startOffset + shift));
    persistChartViewport();
    scheduleChartDraw();
  };
  const stopMousePan = () => {
    stateUi.chartDragState = null;
    canvas.style.cursor = 'grab';
  };
  canvas.onmousedown = ev => {
    if (ev.button !== 0) return;
    stateUi.chartDragState = { active: true, startX: ev.clientX, startOffset: Number(stateUi.panOffset || 0) };
    canvas.style.cursor = 'grabbing';
    const move = moveEv => {
      applyPanFromClientX(moveEv.clientX);
      if (moveEv.cancelable) moveEv.preventDefault();
    };
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      stopMousePan();
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    ev.preventDefault();
  };
  canvas.onmouseup = stopMousePan;
  canvas.onmouseleave = () => {
    if (stateUi.chartDragState && stateUi.chartDragState.active) return;
    canvas.style.cursor = 'grab';
    stateUi.chartHoverIndex = null;
    setCandleDetails(latest);
    scheduleChartDraw();
  };
  canvas.onmousemove = ev => {
    if (stateUi.chartDragState && stateUi.chartDragState.active) {
      applyPanFromClientX(ev.clientX);
      return;
    }
    const r = canvas.getBoundingClientRect();
    updateHover(ev.clientX - r.left);
    scheduleChartDraw();
  };
  canvas.onwheel = ev => {
    ev.preventDefault();
    const current = Math.max(30, Math.min(1000, Number(stateUi.candleLimit || DEFAULT_LIMITS[stateUi.timeframe] || 180)));
    const step = Math.max(5, Math.round(current * 0.08));
    const next = ev.deltaY < 0 ? Math.max(30, current - step) : Math.min(1000, current + step);
    if (next === current) return;
    stateUi.candleLimit = next;
    const maxOffset = Math.max(0, allRows.length - next);
    stateUi.panOffset = Math.max(0, Math.min(maxOffset, Number(stateUi.panOffset || 0)));
    persistChartViewport();
    scheduleChartDraw();
  };
  canvas.style.cursor = stateUi.chartDragState && stateUi.chartDragState.active ? 'grabbing' : 'grab';
  if (canvas.style.touchAction !== 'none') {
    canvas.style.touchAction = 'none';
  }
  canvas.ontouchstart = ev => {
    const touch = ev.touches && ev.touches[0];
    if (!touch) return;
    stateUi.chartDragState = { active: true, startX: touch.clientX, startOffset: Number(stateUi.panOffset || 0) };
  };
  canvas.ontouchmove = ev => {
    const touch = ev.touches && ev.touches[0];
    if (!touch || !(stateUi.chartDragState && stateUi.chartDragState.active)) return;
    const dx = touch.clientX - stateUi.chartDragState.startX;
    const shift = Math.round(dx / Math.max(1, candleGap));
    const maxOffset = Math.max(0, allRows.length - Math.max(30, Number(stateUi.candleLimit || DEFAULT_LIMITS[stateUi.timeframe] || 180)));
    stateUi.panOffset = Math.max(0, Math.min(maxOffset, stateUi.chartDragState.startOffset + shift));
    persistChartViewport();
    scheduleChartDraw();
    if (ev.cancelable) ev.preventDefault();
  };
  canvas.ontouchend = () => {
    stateUi.chartDragState = null;
  };
}

function binanceInterval(tf) {
  return normalizeTimeframe(tf);
}

function intervalDurationMs(tf) {
  return {
    '1s': 1000,
    '1m': 60_000,
    '5m': 5 * 60_000,
    '30m': 30 * 60_000,
    '1h': 60 * 60_000,
    '1d': 24 * 60 * 60_000,
    '1w': 7 * 24 * 60 * 60_000,
    '1M': 31 * 24 * 60 * 60_000,
  }[normalizeTimeframe(tf)] || null;
}

function chartLimit() {
  return Math.max(30, Math.min(1000, Number(stateUi.candleLimit || DEFAULT_LIMITS[stateUi.timeframe] || 180)));
}

function chartSymbol() {
  return String((stateUi.lastStatus && stateUi.lastStatus.symbol) || (stateUi.lastState && stateUi.lastState.symbol) || 'BTCUSDT').toUpperCase();
}

function setChartStreamStatus(kind, label) {
  const el = document.getElementById('chart-stream-status');
  if (!el) return;
  el.className = `stream-status ${kind || ''}`;
  el.textContent = label || kind || 'stream';
}

function klineToBar(payload) {
  const data = payload && payload.data ? payload.data : payload;
  const k = data && data.k;
  if (!k) return null;
  return {
    openTimeMs: Number(k.t),
    open: Number(k.o),
    high: Number(k.h),
    low: Number(k.l),
    close: Number(k.c),
    volumeBase: Number(k.v),
    closeTimeMs: Number(k.T),
    volumeUsdt: Number(k.q),
    symbol: String(k.s || chartSymbol()).toUpperCase(),
    interval: normalizeTimeframe(k.i),
  };
}

function upsertRealtimeBar(bar) {
  if (!bar || bar.interval !== normalizeTimeframe(stateUi.timeframe) || !Number.isFinite(bar.openTimeMs)) return false;
  const rows = (stateUi.lastOhlcv || []).slice();
  if (!rows.length) {
    stateUi.lastOhlcv = [bar];
    scheduleChartDraw();
    return true;
  }
  const last = rows[rows.length - 1];
  const lastOpen = Number(last.openTimeMs || 0);
  if (bar.openTimeMs < lastOpen) return false;
  if (bar.openTimeMs === lastOpen) {
    rows[rows.length - 1] = Object.assign({}, last, bar);
  } else {
    rows.push(bar);
    const limit = chartLimit();
    if (rows.length > limit) rows.splice(0, rows.length - limit);
  }
  stateUi.lastOhlcv = rows;
  scheduleChartDraw();
  return true;
}

function scheduleChartDraw() {
  if (stateUi.chartDrawHandle != null || document.hidden) return;
  stateUi.chartDrawHandle = requestAnimationFrame(() => {
    stateUi.chartDrawHandle = null;
    safeRender('chart', () => drawCandles(stateUi.lastOhlcv || []));
  });
}

function handleKlineMessage(payload) {
  const bar = klineToBar(payload);
  if (!bar) return;
  stateUi.chartLastEventAt = Date.now();
  stateUi.chartFallbackActive = false;
  setChartStreamStatus('live', 'WS live');
  upsertRealtimeBar(bar);
}

function handleTradeMessage(payload) {
  const data = payload && payload.data ? payload.data : payload;
  if (!data || data.e !== 'aggTrade') return;
  stateUi.chartLastEventAt = Date.now();
  stateUi.chartFallbackActive = false;
  setChartStreamStatus('live', 'WS live');
  const rows = (stateUi.lastOhlcv || []).slice();
  if (!rows.length) return;
  const last = Object.assign({}, rows[rows.length - 1]);
  const price = Number(data.p);
  const tradeTime = Number(data.T || data.E || Date.now());
  if (!Number.isFinite(price) || !Number.isFinite(tradeTime)) return;
  const openTimeMs = Number(last.openTimeMs || 0);
  const closeTimeMs = Number(last.closeTimeMs || 0);
  if (tradeTime < openTimeMs || (Number.isFinite(closeTimeMs) && closeTimeMs > 0 && tradeTime > closeTimeMs)) return;
  last.close = price;
  last.high = Math.max(Number(last.high || price), price);
  last.low = Math.min(Number(last.low || price), price);
  rows[rows.length - 1] = last;
  stateUi.lastOhlcv = rows;
  scheduleChartDraw();
}

function stopChartStream() {
  stateUi.chartStreamGeneration += 1;
  if (stateUi.chartReconnectTimer) clearTimeout(stateUi.chartReconnectTimer);
  if (stateUi.chartStaleTimer) clearInterval(stateUi.chartStaleTimer);
  stateUi.chartReconnectTimer = null;
  stateUi.chartStaleTimer = null;
  if (stateUi.chartSocket) {
    try { stateUi.chartSocket.close(1000, 'resubscribe'); } catch {}
  }
  stateUi.chartSocket = null;
}

function startChartStream(symbol = chartSymbol(), interval = stateUi.timeframe) {
  stopChartStream();
  const generation = stateUi.chartStreamGeneration;
  stateUi.chartStreamSymbol = String(symbol || 'BTCUSDT').toUpperCase();
  stateUi.chartStreamInterval = normalizeTimeframe(interval);
  stateUi.chartReconnectAttempt = 0;
  openChartSocket(generation);
}

function openChartSocket(generation) {
  if (generation !== stateUi.chartStreamGeneration) return;
  setChartStreamStatus(stateUi.chartReconnectAttempt ? 'reconnecting' : 'reconnecting', stateUi.chartReconnectAttempt ? 'reconnecting' : 'connecting');
  updateChartTitle();
  stateUi.chartLastEventAt = Date.now();
  if (stateUi.chartStaleTimer) clearInterval(stateUi.chartStaleTimer);
  stateUi.chartStaleTimer = null;
  try {
    stateUi.chartSocket = new WebSocket(chartWebSocketUrl());
  } catch {
    scheduleChartReconnect(generation);
    return;
  }
  stateUi.chartSocket.addEventListener('open', () => {
    if (generation !== stateUi.chartStreamGeneration) return;
    stateUi.chartReconnectAttempt = 0;
    stateUi.chartLastEventAt = Date.now();
    stateUi.chartFallbackActive = false;
    setChartStreamStatus('live', 'WS live');
  });
  stateUi.chartSocket.addEventListener('message', event => {
    if (generation !== stateUi.chartStreamGeneration) return;
    let payload;
    try { payload = JSON.parse(event.data); } catch { return; }
    if (payload && payload.channel === 'chart') {
      if (!acceptPayloadSeq('chart', payload.seq, payload.serverInstanceId)) return;
      stateUi.chartLastEventAt = Date.now();
      if (payload.bar && upsertRealtimeBar(payload.bar)) scheduleChartDraw();
      if (payload.status) applyLiveMarketPayload({ ...payload, channel: 'status', seq: undefined, ohlcv: [], events: undefined }, false);
      return;
    }
    const data = payload && payload.data ? payload.data : payload;
    if (data && data.e === 'kline') handleKlineMessage(payload);
    if (data && data.e === 'aggTrade') handleTradeMessage(payload);
  });
  stateUi.chartSocket.addEventListener('close', () => {
    if (generation !== stateUi.chartStreamGeneration) return;
    stateUi.chartFallbackActive = true;
    if (stateUi.chartStaleTimer) clearInterval(stateUi.chartStaleTimer);
    stateUi.chartStaleTimer = null;
    scheduleChartReconnect(generation);
  });
  stateUi.chartSocket.addEventListener('error', () => {
    if (generation !== stateUi.chartStreamGeneration) return;
    stateUi.chartFallbackActive = true;
    setChartStreamStatus('stale', 'stale');
    try { stateUi.chartSocket.close(); } catch {}
  });
  stateUi.chartStaleTimer = setInterval(() => {
    if (generation !== stateUi.chartStreamGeneration) return;
    if (Date.now() - stateUi.chartLastEventAt > 5000) {
      stateUi.chartFallbackActive = true;
      setChartStreamStatus('stale', 'stale');
      refreshMarket();
    }
  }, 1000);
}

function chartWebSocketUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host || new URL(window.location.href).host;
  const params = new URLSearchParams();
  params.set('interval', normalizeTimeframe(stateUi.chartStreamInterval || stateUi.timeframe));
  params.set('limit', String(chartLimit()));
  params.set('offset', String(Math.max(0, Number(stateUi.historyOffset || 0))));
  const token = dashboardToken();
  if (token) params.set('token', token);
  return `${protocol}//${host}/ws/chart?${params.toString()}`;
}

function scheduleChartReconnect(generation) {
  if (generation !== stateUi.chartStreamGeneration) return;
  const delays = [1000, 2000, 5000, 10000, 30000];
  const delay = delays[Math.min(stateUi.chartReconnectAttempt, delays.length - 1)];
  stateUi.chartReconnectAttempt += 1;
  setChartStreamStatus('reconnecting', 'reconnecting');
  if (stateUi.chartReconnectTimer) clearTimeout(stateUi.chartReconnectTimer);
  stateUi.chartReconnectTimer = setTimeout(() => openChartSocket(generation), delay);
}

async function resyncChartHistory(reason = 'resync') {
  stopChartStream();
  stateUi.chartFallbackActive = true;
  stateUi.chartFallbackReason = reason;
  setChartStreamStatus('fallback', 'fallback polling');
  try {
    const tf = normalizeTimeframe(stateUi.timeframe);
    const data = await fetchJson('/api/market', {
      interval: tf,
      limit: chartLimit(),
      offset: Math.max(0, Number(stateUi.historyOffset || 0)),
      _: Date.now(),
    }, 'market seed', 7000);
    applyLiveMarketPayload(data, true);
    startChartStream((data.status && data.status.symbol) || chartSymbol(), tf);
    startLiveEventStream();
  } catch (err) {
    setChartStreamStatus('stale', 'stale');
    const freshLabel = document.getElementById('fresh-label');
    if (freshLabel) freshLabel.textContent = `Chart seed error • ${err.message || err}`;
  }
}

function updateSnapBadge(card) {
  const badge = card.querySelector('.snap-badge');
  if (badge) {
    const height = card.dataset.height ? ` - ${Math.round(Number(card.dataset.height))}px` : '';
    badge.textContent = `${card.dataset.span || card.dataset.defaultSpan || 8} cols${height}`;
  }
}
function ensureLayoutControls(card) {
  if (!card.querySelector('.snap-badge')) {
    const badge = document.createElement('div');
    badge.className = 'snap-badge';
    card.appendChild(badge);
  }
  if (!card.querySelector('.resize-handle')) {
    const handle = document.createElement('div');
    handle.className = 'resize-handle';
    handle.setAttribute('aria-hidden', 'true');
    card.appendChild(handle);
  }
}
function saveLayout() {
  const cards = [...document.querySelectorAll('.card')]
    .sort((a, b) => [...a.parentElement.children].indexOf(a) - [...b.parentElement.children].indexOf(b))
    .map((card, idx) => ({
      id: card.id,
      order: card.id === 'summary-card' ? 0 : (idx + 1),
      span: card.dataset.span || card.dataset.defaultSpan || '8',
      height: card.dataset.height || ''
    }));
  localStorage.setItem(getLayoutKey(), JSON.stringify(cards));
}
function applyCardSpan(card, span) {
  const nextSpan = Math.max(MIN_SPAN, Math.min(GRID_COLS, Number(span) || Number(card.dataset.defaultSpan || 8)));
  card.dataset.span = String(nextSpan);
  card.style.gridColumn = `span ${nextSpan}`;
  updateSnapBadge(card);
}
function applyCardHeight(card, height) {
  const nextHeight = Number(height || 0);
  if (nextHeight > 0) {
    const clamped = Math.max(140, Math.min(1400, nextHeight));
    card.dataset.height = String(Math.round(clamped));
    card.style.height = `${Math.round(clamped)}px`;
  } else {
    delete card.dataset.height;
    card.style.height = '';
  }
  updateSnapBadge(card);
}
function applyDefaultLayout() {
  localStorage.removeItem(getLayoutKey());
  const dashboard = document.getElementById('dashboard');
  const cards = [...document.querySelectorAll('.card')].sort((a, b) => {
    if (a.id === 'summary-card') return -1;
    if (b.id === 'summary-card') return 1;
    return Number(a.dataset.defaultCol || 1) - Number(b.dataset.defaultCol || 1);
  });
  cards.forEach(card => {
    dashboard.appendChild(card);
    ensureLayoutControls(card);
    applyCardSpan(card, card.dataset.defaultSpan || 8);
    applyCardHeight(card, null);
  });
}
function loadLayout() {
  const raw = localStorage.getItem(getLayoutKey());
  if (!raw) { applyDefaultLayout(); return; }
  try {
    const cards = JSON.parse(raw);
    const dashboard = document.getElementById('dashboard');
    const byId = new Map(cards.map(cfg => [cfg.id, cfg]));
    const ordered = [...document.querySelectorAll('.card')].sort((a, b) => {
      if (a.id === 'summary-card') return -1;
      if (b.id === 'summary-card') return 1;
      const aCfg = byId.get(a.id) || {};
      const bCfg = byId.get(b.id) || {};
      return Number(aCfg.order === undefined || aCfg.order === null ? 9999 : aCfg.order) - Number(bCfg.order === undefined || bCfg.order === null ? 9999 : bCfg.order);
    });
    ordered.forEach(card => {
      dashboard.appendChild(card);
      ensureLayoutControls(card);
      const cfg = byId.get(card.id) || {};
      applyCardSpan(card, cfg.span || card.dataset.defaultSpan || 8);
      applyCardHeight(card, cfg.height || null);
    });
  } catch {
    applyDefaultLayout();
  }
}
function pointerClientXY(ev) {
  const p = (ev.touches && ev.touches[0]) || (ev.changedTouches && ev.changedTouches[0]) || ev;
  return { x: p.clientX, y: p.clientY };
}
function midpointOfCard(card) { const rect = card.getBoundingClientRect(); return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }; }
function findReorderTarget(cards, dragCard, x, y) {
  let best = null, bestDistance = Infinity;
  cards.forEach(card => {
    if (card === dragCard) return;
    const mid = midpointOfCard(card);
    const dist = Math.hypot(mid.x - x, mid.y - y);
    if (dist < bestDistance) { best = card; bestDistance = dist; }
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
    ensureLayoutControls(card);
    const head = card.querySelector('.card-head');
    const handle = card.querySelector('.resize-handle');
    applyCardSpan(card, card.dataset.span || card.dataset.defaultSpan || 8);
    if (card.dataset.height) applyCardHeight(card, card.dataset.height);
    card.classList.add('reflowing');
    const beginDragSession = start => {
      const cards = [...dashboard.querySelectorAll('.card')];
      let activeTarget = null, pendingPoint = start, rafId = null;
      dashboard.classList.add('drag-active');
      card.classList.add('dragging');
      const renderFrame = () => {
        rafId = null;
        const dx = pendingPoint.x - start.x;
        const dy = pendingPoint.y - start.y;
        card.style.transform = `translate3d(${dx}px, ${dy}px, 0) scale(1.02)`;
        const over = findReorderTarget(cards, card, pendingPoint.x, pendingPoint.y);
        if (activeTarget !== over) {
          cards.forEach(c => c.classList.remove('drop-target'));
          activeTarget = over;
          if (activeTarget) activeTarget.classList.add('drop-target');
        }
        if (activeTarget) reorderCardBefore(dashboard, card, activeTarget, pendingPoint.x);
      };
      const queueFrame = point => { pendingPoint = point; if (rafId == null) rafId = requestAnimationFrame(renderFrame); };
      const move = e => { queueFrame(pointerClientXY(e)); if (e.cancelable) e.preventDefault(); };
      const up = () => {
        if (rafId != null) cancelAnimationFrame(rafId);
        cards.forEach(c => c.classList.remove('drop-target'));
        dashboard.classList.remove('drag-active');
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
      if (ev.target.closest('.resize-handle') || ev.target.closest('button') || ev.target.closest('input') || ev.target.closest('select')) return;
      if (ev.type === 'touchstart') {
        const start = pointerClientXY(ev);
        let dragging = false;
        const touchMove = moveEv => {
          const point = pointerClientXY(moveEv);
          if (!dragging && Math.hypot(point.x - start.x, point.y - start.y) > 10) { dragging = true; beginDragSession(start); }
          if (dragging && moveEv.cancelable) moveEv.preventDefault();
        };
        const touchEnd = () => { window.removeEventListener('touchmove', touchMove); window.removeEventListener('touchend', touchEnd); };
        window.addEventListener('touchmove', touchMove, { passive: false });
        window.addEventListener('touchend', touchEnd);
        return;
      }
      ev.preventDefault();
      beginDragSession(pointerClientXY(ev));
    };
    const startResize = startPoint => {
      const startSpan = Number(card.dataset.span || card.dataset.defaultSpan || 8);
      const startHeight = card.getBoundingClientRect().height;
      const dashboardRect = dashboard.getBoundingClientRect();
      const colWidth = (dashboardRect.width - GRID_GAP * (GRID_COLS - 1)) / GRID_COLS;
      let pendingPoint = startPoint, rafId = null;
      card.classList.add('resizing');
      const renderFrame = () => {
        rafId = null;
        const deltaCols = Math.round((pendingPoint.x - startPoint.x) / Math.max(1, colWidth + GRID_GAP));
        const deltaHeight = pendingPoint.y - startPoint.y;
        applyCardSpan(card, startSpan + deltaCols);
        applyCardHeight(card, startHeight + deltaHeight);
        if (card.id === 'market-card') scheduleChartDraw();
      };
      const queueFrame = point => { pendingPoint = point; if (rafId == null) rafId = requestAnimationFrame(renderFrame); };
      const move = e => { queueFrame(pointerClientXY(e)); if (e.cancelable) e.preventDefault(); };
      const up = () => {
        if (rafId != null) cancelAnimationFrame(rafId);
        card.classList.remove('resizing');
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
    if (head) {
      head.addEventListener('mousedown', startDrag);
      head.addEventListener('touchstart', startDrag, { passive: true });
    }
    if (handle) {
      handle.addEventListener('mousedown', ev => { ev.preventDefault(); ev.stopPropagation(); startResize(pointerClientXY(ev)); });
      handle.addEventListener('touchstart', ev => { ev.stopPropagation(); startResize(pointerClientXY(ev)); }, { passive: true });
    }
  });
}

function renderTimeframeControls() {
  const el = document.getElementById('timeframe-controls');
  el.innerHTML = TIMEFRAMES.map(tf => `<button class="btn ${stateUi.timeframe === tf ? 'active-timeframe' : ''}" type="button" data-tf="${tf}">${TIMEFRAME_LABELS[tf] || tf}</button>`).join('');
  const topTf = document.getElementById('top-timeframe');
  if (topTf) topTf.textContent = TIMEFRAME_LABELS[stateUi.timeframe] || stateUi.timeframe;
  updateChartTitle();
  el.querySelectorAll('[data-tf]').forEach(btn => btn.addEventListener('click', () => {
    stateUi.timeframe = normalizeTimeframe(btn.dataset.tf);
    syncTimeframeUrl();
    resyncChartHistory('interval-change');
    refresh();
  }));
}

function populateConfigForm(state, modelOptions = []) {
  const grid = document.getElementById('config-form-grid');
  if (!grid) return;
  const endpointKey = endpointKeyForState(state);
  grid.innerHTML = CONFIG_FIELDS.map(field => {
    const rawValue = field.key === 'aiEndpointKey' ? endpointKey : state[field.key];
    const value = field.type === 'select' ? String(rawValue) : (rawValue === undefined || rawValue === null ? '' : rawValue);
    let options = field.options;
    if (field.key === 'aiEndpointKey') {
      options = (stateUi.aiEndpoints || []).map(ep => ({ value: ep.key, label: ep.label }));
    } else if (AI_MODEL_FIELDS.has(field.key)) {
      options = modelOptionsForEndpoint(endpointKey, modelOptions);
    }
    if (field.type === 'select') {
      const seen = new Set();
      const rawOptions = (options && options.length) ? [...options] : [value || '--'];
      const finalOptions = [];
      if (value && !rawOptions.some(opt => String(optionValue(opt)) === value)) rawOptions.unshift(value);
      rawOptions.forEach(opt => {
        const optValue = String(optionValue(opt));
        if (!optValue || seen.has(optValue)) return;
        seen.add(optValue);
        finalOptions.push(opt);
      });
      return `<div class="config-field"><label for="cfg-${field.key}">${field.label}</label><select id="cfg-${field.key}" data-key="${field.key}">${finalOptions.map(opt => {
        const optValue = String(optionValue(opt));
        return `<option value="${optValue}" ${optValue === value ? 'selected' : ''}>${optionLabel(opt)}</option>`;
      }).join('')}</select></div>`;
    }
    return `<div class="config-field"><label for="cfg-${field.key}">${field.label}</label><input id="cfg-${field.key}" data-key="${field.key}" type="${field.type}" step="${field.step || 'any'}" value="${value}"></div>`;
  }).join('');
  wireAiEndpointControls(modelOptions);
}

function setModelSelectOptions(select, models) {
  if (!select) return;
  const current = select.value;
  const finalModels = models && models.length ? models : (current ? [current] : ['--']);
  select.innerHTML = Array.from(new Set(finalModels.filter(Boolean))).map(model => `<option value="${model}" ${model === current ? 'selected' : ''}>${model}</option>`).join('');
  if (!Array.from(select.options).some(opt => opt.value === current) && select.options.length) select.value = select.options[0].value;
}

function wireAiEndpointControls(modelOptions = []) {
  const endpointSelect = document.getElementById('cfg-aiEndpointKey');
  const baseInput = document.getElementById('cfg-aiBaseUrl');
  const providerInput = document.getElementById('cfg-aiProvider');
  if (endpointSelect) {
    endpointSelect.addEventListener('change', () => {
      const ep = endpointForKey(endpointSelect.value);
      if (ep && ep.key !== 'custom') {
        if (baseInput) baseInput.value = ep.baseUrl;
        if (providerInput) providerInput.value = ep.provider || 'ollama';
      }
      const models = modelOptionsForEndpoint(endpointSelect.value, modelOptions);
      AI_MODEL_FIELDS.forEach(key => setModelSelectOptions(document.getElementById(`cfg-${key}`), models));
    });
  }
  if (baseInput && endpointSelect) {
    baseInput.addEventListener('input', () => {
      endpointSelect.value = 'custom';
      const models = modelOptionsForEndpoint('custom', modelOptions);
      AI_MODEL_FIELDS.forEach(key => setModelSelectOptions(document.getElementById(`cfg-${key}`), models));
    });
  }
}

function openConfigModal() {
  const modal = document.getElementById('config-modal');
  if (!modal) return;
  modal.hidden = false;
  if (document.body && document.body.classList) document.body.classList.add('modal-open');
}

function closeConfigModal() {
  const modal = document.getElementById('config-modal');
  if (!modal) return;
  modal.hidden = true;
  if (document.body && document.body.classList) document.body.classList.remove('modal-open');
}

function dashboardToken() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token') || localStorage.getItem('tradebot-dashboard-token') || '';
  if (params.get('token')) localStorage.setItem('tradebot-dashboard-token', token);
  return token;
}

function apiHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  const token = dashboardToken();
  if (token) headers['X-Tradebot-Token'] = token;
  return headers;
}

function apiPath(path) {
  return apiUrl(path);
}

function apiUrl(path, params = {}) {
  const token = dashboardToken();
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null) url.searchParams.set(key, value);
  });
  if (token) url.searchParams.set('token', token);
  return `${url.pathname}${url.search}`;
}

async function fetchJson(path, params = {}, label = 'request', timeoutMs = 7000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(apiUrl(path, params), {
      cache: 'no-store',
      headers: { 'Cache-Control': 'no-cache' },
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`${label} failed: ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

async function saveConfig() {
  const payload = {};
  document.querySelectorAll('#config-form-grid [data-key]').forEach(el => {
    payload[el.dataset.key] = el.value;
  });
  const res = await fetch(apiPath('/api/config'), { method: 'POST', headers: apiHeaders(), body: JSON.stringify(payload) });
  if (!res.ok) throw new Error(`config save failed: ${res.status}`);
  await refresh();
  await refreshMarket();
}

function applyLiveMarketPayload(data, renderChart = true) {
  if (!acceptPayloadSeq(data.channel || 'status', data.seq, data.serverInstanceId)) return null;
  const status = data.status || {};
  const state = data.state || stateUi.lastState || {};
  const runtime = data.runtime || stateUi.lastRuntime || {};
  const cumulative = data.cumulative || stateUi.lastCumulative || {};
  const ohlcv = data.ohlcv || [];
  const statusGrid = (status.stats || {}).grid || {};
  const runtimeGrid = runtime.grid || {};
  const hasRuntimeOrders = Array.isArray(runtimeGrid.orders);
  const runtimeOrders = hasRuntimeOrders ? runtimeGrid.orders : [];
  if (data.ordersPatch) {
    applyOrdersPatch(data.ordersPatch);
  } else if (hasRuntimeOrders) {
    stateUi.lastOrders = runtimeOrders.slice();
  }
  const grid = Object.assign({}, statusGrid, { openOrders: (stateUi.lastOrders || []).length });
  stateUi.lastState = state;
  stateUi.lastStatus = status;
  stateUi.lastRuntime = runtime;
  stateUi.lastCumulative = cumulative;
  const botToggle = document.getElementById('bot-toggle-btn');
  if (botToggle) botToggle.textContent = state.paused ? '▶' : '⏸';
  const aiToggle = document.getElementById('ai-toggle-btn');
  if (aiToggle) {
    const aiEnabled = state.aiEnabled !== false;
    aiToggle.textContent = aiEnabled ? 'AI On' : 'AI Off';
    aiToggle.classList.toggle('ai-on', aiEnabled);
    aiToggle.classList.toggle('ai-off', !aiEnabled);
  }
  stateUi.lastStatusFreshnessSeconds = Number(data.freshnessSeconds || 0);
  stateUi.lastStatusReceivedAt = Date.now();
  const freshLabel = document.getElementById('fresh-label');
  if (freshLabel) freshLabel.textContent = `Live payload • ${humanAge(stateUi.lastStatusFreshnessSeconds)}`;
  const serverTime = document.getElementById('server-time');
  if (serverTime) serverTime.textContent = fmtServerTime(data.serverTimeUtc);
  stateUi.marketRefreshMs = Math.max(1000, Number(data.refreshMs || stateUi.marketRefreshMs || 1000));
  safeRender('summary', () => renderStickySummary(status, cumulative, runtime, grid));
  if (data.eventsPatch) {
    safeRender('events', () => {
      applyEventsPatch(data.eventsPatch);
      renderEvents((stateUi.lastEvents || []).slice().reverse());
    });
  } else if (Array.isArray(data.events)) {
    safeRender('events', () => renderEvents(data.events.slice().reverse()));
  }
  if (Array.isArray(data.aiDecisions)) {
    safeRender('aiDecisions', () => renderAiDecisions(data.aiDecisions));
  }
  safeRender('orders', () => renderOrders());
  safeRender('status', () => renderLiveStatusFooter(status, state, runtime, data, grid));
  safeRender('calendar', () => renderMacroCalendar(data.serverTimeUtc));
  if (renderChart) {
    stateUi.lastOhlcv = ohlcv || [];
    safeRender('chart', () => drawCandles(ohlcv || []));
  }
  safeRender('regime', () => renderRegime(status, runtime, stateUi.lastIntelligence || {}));
  return { status, state, runtime, cumulative, ohlcv, grid };
}

async function refreshMarket() {
  if (stateUi.marketRefreshInFlight) return;
  stateUi.marketRefreshInFlight = true;
  try {
    const tf = normalizeTimeframe(stateUi.timeframe);
    const renderChart = true;
    const limit = chartLimit();
    const offset = Math.max(0, Number(stateUi.historyOffset || 0));
    const data = await fetchJson('/api/market', { interval: tf, limit, offset, ohlcv: '1', _: Date.now() }, 'market fetch', 4500);
    applyLiveMarketPayload(data, renderChart);
  } catch (err) {
    const freshLabel = document.getElementById('fresh-label');
    if (freshLabel) freshLabel.textContent = `Market fetch error • ${err.message || err}`;
  } finally {
    stateUi.marketRefreshInFlight = false;
  }
}

function liveEventUrl() {
  const params = new URLSearchParams();
  params.set('interval', normalizeTimeframe(stateUi.timeframe));
  params.set('limit', String(chartLimit()));
  params.set('offset', String(Math.max(0, Number(stateUi.historyOffset || 0))));
  params.set('ohlcv', '0');
  params.set('_', String(Date.now()));
  const token = dashboardToken();
  if (token) params.set('token', token);
  return `/api/live/events?${params.toString()}`;
}

function stopLiveEventStream() {
  stateUi.liveEventStreamActive = false;
  if (stateUi.liveEventReconnectTimer) clearTimeout(stateUi.liveEventReconnectTimer);
  stateUi.liveEventReconnectTimer = null;
  if (stateUi.liveEventSource) {
    try { stateUi.liveEventSource.close(); } catch {}
  }
  stateUi.liveEventSource = null;
}

function scheduleLiveEventReconnect() {
  if (stateUi.dashboardSleeping || document.hidden) return;
  if (stateUi.liveEventReconnectTimer) return;
  stateUi.liveEventReconnectTimer = setTimeout(() => {
    stateUi.liveEventReconnectTimer = null;
    startLiveEventStream();
  }, 1500);
}

function startLiveEventStream() {
  if (stateUi.dashboardSleeping || document.hidden) return;
  if (typeof EventSource === 'undefined') {
    if (!stateUi.marketRefreshTimer) scheduleMarketRefresh(stateUi.marketRefreshMs);
    return;
  }
  stopLiveEventStream();
  try {
    const source = new EventSource(liveEventUrl());
    stateUi.liveEventSource = source;
    source.addEventListener('open', () => {
      stateUi.liveEventStreamActive = true;
      stateUi.liveEventLastDataAt = Date.now();
    });
    source.addEventListener('market', event => {
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      stateUi.liveEventStreamActive = true;
      stateUi.liveEventLastDataAt = Date.now();
      applyLiveMarketPayload(data, false);
    });
    source.onerror = () => {
      stateUi.liveEventStreamActive = false;
      try { source.close(); } catch {}
      if (stateUi.liveEventSource === source) stateUi.liveEventSource = null;
      if (!stateUi.marketRefreshTimer) scheduleMarketRefresh(stateUi.marketRefreshMs);
      scheduleLiveEventReconnect();
    };
  } catch {
    stateUi.liveEventStreamActive = false;
    if (!stateUi.marketRefreshTimer) scheduleMarketRefresh(stateUi.marketRefreshMs);
    scheduleLiveEventReconnect();
  }
}

async function refresh() {
  if (stateUi.refreshInFlight) return;
  stateUi.refreshInFlight = true;
  try {
    const tf = normalizeTimeframe(stateUi.timeframe);
    const limit = Math.max(30, Math.min(1000, Number(stateUi.candleLimit || DEFAULT_LIMITS[stateUi.timeframe] || 180)));
    const offset = Math.max(0, Number(stateUi.historyOffset || 0));
    const cacheBust = Date.now();
    const data = await fetchJson('/api/dashboard', { interval: tf, limit, offset, _: cacheBust }, 'dashboard fetch', 12000);
    if (!acceptPayloadSeq('dashboard', data.seq, data.serverInstanceId)) return;
    const status = data.status || {};
    const state = data.state || {};
    const runtime = data.runtime || {};
    const cumulative = data.cumulative || {};
    const events = data.events || [];
    const aiDecisions = data.aiDecisions || [];
    const freshnessSeconds = data.freshnessSeconds;
    const serverTimeUtc = data.serverTimeUtc;
    const ohlcv = data.ohlcv || [];
    const aiModels = data.aiModels || [];
    stateUi.aiEndpoints = data.aiEndpoints || [];
    stateUi.aiEndpointModels = data.aiEndpointModels || {};
    stateUi.marketRefreshMs = Math.max(1000, Number(data.refreshMs || stateUi.marketRefreshMs || 1000));
    stateUi.dashboardRefreshMs = Math.max(60_000, Number(data.dashboardRefreshMs || stateUi.dashboardRefreshMs || (30 * 60 * 1000)));
    const intelligence = data.intelligence || {};
    stateUi.lastIntelligence = intelligence;
    const statusGrid = (status.stats || {}).grid || {};
    const grid = Object.assign({}, statusGrid, { openOrders: ((runtime.grid || {}).orders || []).length });
    stateUi.lastState = state;
    stateUi.lastStatus = status;
    stateUi.lastRuntime = runtime;
    stateUi.lastCumulative = cumulative;
    stateUi.lastOrders = ((runtime.grid || {}).orders || []).slice();
    setTextIfPresent('bot-toggle-btn', state.paused ? '▶' : '⏸');
    const aiEnabled = state.aiEnabled !== false;
    const aiToggle = document.getElementById('ai-toggle-btn');
    if (aiToggle) {
      aiToggle.textContent = aiEnabled ? 'AI On' : 'AI Off';
      aiToggle.classList.toggle('ai-on', aiEnabled);
      aiToggle.classList.toggle('ai-off', !aiEnabled);
    }
    setTextIfPresent('fresh-label', freshnessSeconds != null ? `Live payload • ${humanAge(freshnessSeconds)}` : 'No timestamp');
    setTextIfPresent('server-time', fmtServerTime(serverTimeUtc));
    safeRender('summary', () => renderStickySummary(status, cumulative, runtime, grid));
    safeRender('status', () => renderLiveStatusFooter(status, state, runtime, data, grid));
    safeRender('events', () => renderEvents((events || []).slice().reverse()));
    safeRender('aiDecisions', () => renderAiDecisions(aiDecisions));
    safeRender('orders', () => renderOrders());
    safeRender('timeframe', () => renderTimeframeControls());
    safeRender('intelligence', () => renderIntelligence(status, cumulative, runtime, intelligence));
    safeRender('regime', () => renderRegime(status, runtime, intelligence));
    safeRender('calendar', () => renderMacroCalendar(serverTimeUtc));
    const buyFilter = document.getElementById('orders-filter-buy-btn');
    if (buyFilter) buyFilter.style.color = '#35d08a';
    const sellFilter = document.getElementById('orders-filter-sell-btn');
    if (sellFilter) sellFilter.style.color = '#ff6b81';
    populateConfigForm(state, aiModels || []);
  } catch (err) {
    setTextIfPresent('fresh-label', `Dashboard fetch error • ${err.message || err}`);
  } finally {
    stateUi.refreshInFlight = false;
  }
}

function scheduleMarketRefresh(delayMs = stateUi.marketRefreshMs) {
  if (stateUi.dashboardSleeping || document.hidden) return;
  if (stateUi.marketRefreshTimer) clearTimeout(stateUi.marketRefreshTimer);
  stateUi.marketRefreshTimer = setTimeout(async () => {
    try {
      if (stateUi.dashboardSleeping || document.hidden) return;
      const now = Date.now();
      const staleMs = Math.max(4000, Number(stateUi.marketRefreshMs || 1000) * 3);
      const sseLooksStale = stateUi.liveEventStreamActive && (now - Number(stateUi.liveEventLastDataAt || 0) > staleMs);
      if (!stateUi.liveEventStreamActive || sseLooksStale) {
        await refreshMarket();
      }
    } finally {
      if (!stateUi.dashboardSleeping && !document.hidden) scheduleMarketRefresh(stateUi.marketRefreshMs);
    }
  }, Math.max(1000, Number(delayMs || 1000)));
}

function scheduleHardStatusSync() {
  if (stateUi.dashboardSleeping || document.hidden) return;
  if (stateUi.hardStatusSyncTimer) clearInterval(stateUi.hardStatusSyncTimer);
  stateUi.hardStatusSyncTimer = setInterval(() => {
    if (stateUi.dashboardSleeping || document.hidden) return;
    refreshMarket();
  }, Math.max(1000, Number(stateUi.marketRefreshMs || 1000)));
}

function scheduleHardChartSync() {
  if (stateUi.dashboardSleeping || document.hidden) return;
  if (stateUi.hardChartSyncTimer) clearInterval(stateUi.hardChartSyncTimer);
  stateUi.hardChartSyncTimer = setInterval(async () => {
    if (stateUi.dashboardSleeping || document.hidden) return;
    const staleFor = Date.now() - Number(stateUi.chartLastEventAt || 0);
    if (staleFor < 3500) return;
    try {
      const tf = normalizeTimeframe(stateUi.timeframe);
      const data = await fetchJson('/api/market', {
        interval: tf,
        limit: chartLimit(),
        offset: Math.max(0, Number(stateUi.historyOffset || 0)),
        ohlcv: '1',
        _: Date.now(),
      }, 'chart hard sync', 4500);
      applyLiveMarketPayload(data, true);
      stateUi.chartLastEventAt = Date.now();
      setChartStreamStatus('live', stateUi.liveEventStreamActive ? 'live (poll synced)' : 'poll live');
    } catch {
      // Keep silent; other loops continue trying.
    }
  }, 1500);
}

function stopDashboardActivity() {
  stopLiveEventStream();
  stopChartStream();
  if (stateUi.marketRefreshTimer) clearTimeout(stateUi.marketRefreshTimer);
  if (stateUi.hardStatusSyncTimer) clearInterval(stateUi.hardStatusSyncTimer);
  if (stateUi.hardChartSyncTimer) clearInterval(stateUi.hardChartSyncTimer);
  if (stateUi.dashboardRefreshTimer) clearTimeout(stateUi.dashboardRefreshTimer);
  stateUi.marketRefreshTimer = null;
  stateUi.hardStatusSyncTimer = null;
  stateUi.hardChartSyncTimer = null;
  stateUi.dashboardRefreshTimer = null;
}

function sleepDashboard(reason = 'hidden') {
  stateUi.dashboardSleeping = true;
  stopDashboardActivity();
  setChartStreamStatus('stale', reason === 'hidden' ? 'sleeping' : reason);
  const freshLabel = document.getElementById('fresh-label');
  if (freshLabel && document.hidden) freshLabel.textContent = 'Dashboard sleeping while tab is hidden';
}

function wakeDashboard(reason = 'visible') {
  stateUi.dashboardSleeping = false;
  stateUi.liveEventLastDataAt = 0;
  resyncChartHistory(reason);
  scheduleMarketRefresh(stateUi.marketRefreshMs);
  scheduleHardStatusSync();
  scheduleHardChartSync();
}

loadLayout();
enableDrag();
renderTimeframeControls();
syncTimeframeUrl();
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    sleepDashboard('hidden');
    return;
  }
  wakeDashboard('visible');
});
window.addEventListener('beforeunload', () => {
  sleepDashboard('unload');
  if (stateUi.freshnessTicker) clearInterval(stateUi.freshnessTicker);
});
startFreshnessTicker();
refresh().finally(() => {
  stateUi.heavyRefreshLoaded = true;
  if (document.hidden) {
    sleepDashboard('hidden');
    return;
  }
  wakeDashboard('boot');
});
