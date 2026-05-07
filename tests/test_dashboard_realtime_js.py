import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


def _dashboard_script() -> str:
    script = Path("dashboard/static/dashboard.v1.js").read_text(encoding="utf-8")
    script = re.sub(r"\nloadLayout\(\);[\s\S]*$", "", script)
    return script


def test_dashboard_boot_tolerates_cards_without_heads(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for dashboard JS unit test")

    harness = r"""
    const assert = require('assert');
    const vm = require('vm');
    const script = process.env.DASHBOARD_SCRIPT;

    function makeElement(id) {
      const ctx = {
        scale(){}, clearRect(){}, beginPath(){}, arc(){}, stroke(){}, fillText(){},
        fillRect(){}, moveTo(){}, lineTo(){}, strokeRect(){}, closePath(){}, fill(){},
      };
      return {
        id,
        tagName: id === 'top-timeframe' ? 'A' : 'DIV',
        href: '',
        textContent: '',
        innerHTML: '',
        children: [],
        disabled: false,
        value: '',
        style: {},
        dataset: {},
        className: '',
        classList: { toggle(){}, add(){}, remove(){} },
        addEventListener(){},
        setAttribute(name, value){ this[name] = value; },
        appendChild(child){ this.children.push(child); },
        querySelector(sel){ return null; },
        querySelectorAll(){ return []; },
        getBoundingClientRect(){ return { width: 1200, height: 520, left: 0, top: 0 }; },
        getContext(){ return ctx; },
        parentElement: { querySelector(){ return { remove(){} }; } },
      };
    }

    const ids = [
      'theme-toggle','bot-toggle-btn','ai-toggle-btn','reset-layout-btn','events-first-btn','events-prev-btn','events-next-btn','events-last-btn',
      'orders-tab-open-btn','orders-tab-history-btn','orders-filter-buy-btn','orders-filter-sell-btn','orders-first-btn','orders-prev-btn','orders-next-btn','orders-last-btn',
      'config-save-btn','dashboard','summary-card','market-card','intelligence-card','regime-card','macro-card','events-card','orders-card','config-card','status-card',
      'fresh-label','server-time','sticky-summary','trading-state-label','state-mode','state-risk','state-exposure','state-action','chart-price-pill','chart-quote-line',
      'status-list','events-body','events-page-indicator','orders-body','orders-page-indicator','timeframe-controls','news-stack','signal-table','final-regime-title',
      'final-regime-copy','regime-updated','macro-calendar','config-form-grid','market-legend','hover-ohlcv','latest-candle','market-chart','chart-stream-status','boot-error','top-timeframe'
    ];
    const elements = new Map(ids.map(id => [id, makeElement(id)]));
    const cards = [
      'summary-card','market-card','intelligence-card','regime-card','macro-card',
      'events-card','orders-card','config-card','status-card',
    ].map(id => elements.get(id));
    cards.forEach(card => {
      card.dataset.defaultSpan = '8';
      card.dataset.defaultCol = '1';
      card.parentElement = { children: cards, querySelector(){ return null; } };
      card.querySelector = sel => {
        if (sel === '.card-head') return card.id === 'summary-card' ? null : makeElement(card.id + '-head');
        if (sel === '.resize-handle') return null;
        return null;
      };
      card.querySelectorAll = () => [];
    });
    const dashboard = elements.get('dashboard');
    dashboard.querySelectorAll = sel => sel === '.card' ? cards : [];

    const sample = {
      status: {
        tsUtc: '2026-05-01T00:00:00+00:00',
        symbol: 'BTCUSDT',
        price: 100,
        equityUsdt: 500,
        btc: 0.1,
        usdt: 490,
        stats: { grid: {}, ai: {} },
        position: {},
      },
      state: { paused: false, aiEnabled: false, symbol: 'BTCUSDT', interval: '1m', gridMode: 'scalpy' },
      runtime: { savedAt: '2026-05-01T00:00:00+00:00', grid: { orders: [] } },
      cumulative: { realizedPnlUsdt: 0, feesPaidUsdt: 0 },
      events: [], ohlcv: [], freshnessSeconds: 0.1, serverTimeUtc: '2026-05-01T00:00:00+00:00',
      seq: 1,
      serverInstanceId: 'server-1',
      channel: 'dashboard',
      aiModels: [],
      aiEndpoints: [],
      aiEndpointModels: {},
      intelligence: {
        newsCards: [{ title: 'Cached item', source: 'Cache', sentiment: 'Neutral', impact: 3 }],
        rawNews: [
          { title: 'Bitcoin raw rise', source: 'RSS', publishedUtc: '2026-05-07T12:00:00+00:00' },
          { title: 'Exchange hack loss', source: 'RSS' },
          { title: 'Macro neutral', source: 'RSS' },
          { title: 'ETF flow update', source: 'RSS' },
        ],
      },
      refreshMs: 1000,
      dashboardRefreshMs: 60000,
    };

    const sandbox = {
      console,
      URL,
      URLSearchParams,
      Number,
      Math,
      Date,
      JSON,
      Object,
      Array,
      Set,
      Map,
      String,
      Error,
      AbortController,
      devicePixelRatio: 1,
      localStorage: { getItem(){ return null; }, setItem(){}, removeItem(){} },
      history: { replaceState(){} },
      document: {
        hidden: false,
        body: { classList: { toggle(){}, add(){}, remove(){} } },
        getElementById(id){ return elements.get(id) || null; },
        createElement(tag){ return makeElement(tag); },
        querySelector(){ return null; },
        querySelectorAll(sel){ return sel === '.card' ? cards : []; },
        addEventListener(){},
      },
      window: {
        location: {
          href: 'http://localhost/?interval=1m',
          origin: 'http://localhost',
          protocol: 'http:',
          host: 'localhost',
          pathname: '/',
          search: '?interval=1m',
        },
        addEventListener(){},
      },
      requestAnimationFrame(fn){ fn(); return 1; },
      cancelAnimationFrame(){},
      setInterval(){ return 1; },
      clearInterval(){},
      setTimeout(){ return 1; },
      clearTimeout(){},
      getComputedStyle(){ return { getPropertyValue(){ return '#111'; } }; },
      fetch(){ return Promise.resolve({ ok: true, json: async () => sample }); },
      WebSocket: function(){ return { addEventListener(){}, close(){} }; },
      EventSource: function(){ return { addEventListener(){}, close(){} }; },
    };
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(script, sandbox);
    assert.ok(true);
    """
    result = subprocess.run(
        [node, "-e", textwrap.dedent(harness)],
        cwd=".",
        env={"DASHBOARD_SCRIPT": _dashboard_script()},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_refresh_tolerates_missing_optional_refresh_controls(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for dashboard JS unit test")

    harness = r"""
    const assert = require('assert');
    const vm = require('vm');
    const script = process.env.DASHBOARD_SCRIPT;

    function makeElement(id) {
      const ctx = {
        scale(){}, clearRect(){}, beginPath(){}, arc(){}, stroke(){}, fillText(){},
        fillRect(){}, moveTo(){}, lineTo(){}, strokeRect(){}, closePath(){}, fill(){},
      };
      return {
        id,
        tagName: id === 'top-timeframe' ? 'A' : 'DIV',
        href: '',
        textContent: '',
        innerHTML: '',
        children: [],
        disabled: false,
        value: '',
        style: {},
        dataset: {},
        className: '',
        classList: { toggle(){}, add(){}, remove(){} },
        addEventListener(){},
        setAttribute(name, value){ this[name] = value; },
        appendChild(child){ this.children.push(child); },
        querySelector(){ return null; },
        querySelectorAll(){ return []; },
        getBoundingClientRect(){ return { width: 1200, height: 520, left: 0, top: 0 }; },
        getContext(){ return ctx; },
        parentElement: { querySelector(){ return { remove(){} }; } },
      };
    }

    const ids = [
      'theme-toggle','bot-toggle-btn','ai-toggle-btn','reset-layout-btn',
      'events-first-btn','events-prev-btn','events-next-btn','events-last-btn',
      'orders-tab-open-btn','orders-tab-history-btn','orders-filter-buy-btn',
      'orders-filter-sell-btn','orders-first-btn','orders-prev-btn',
      'orders-next-btn','orders-last-btn','config-save-btn','dashboard',
      'summary-card','market-card','intelligence-card','regime-card','macro-card',
      'events-card','orders-card','config-card','status-card','fresh-label',
      'server-time','sticky-summary','trading-state-label','state-mode',
      'state-risk','state-exposure','state-action','chart-price-pill',
      'chart-quote-line','status-list','events-body','events-page-indicator',
      'orders-body','orders-page-indicator','timeframe-controls','news-stack',
      'signal-table','final-regime-title','final-regime-copy','regime-updated',
      'macro-calendar','config-form-grid','market-legend','hover-ohlcv',
      'latest-candle','market-chart','chart-stream-status','boot-error',
      'top-timeframe'
    ];
    const elements = new Map(ids.map(id => [id, makeElement(id)]));
    const cards = [
      'summary-card','market-card','intelligence-card','regime-card','macro-card',
      'events-card','orders-card','config-card','status-card',
    ].map(id => elements.get(id));
    cards.forEach(card => {
      card.dataset.defaultSpan = '8';
      card.dataset.defaultCol = '1';
      card.parentElement = { children: cards, querySelector(){ return null; } };
      card.querySelector = sel => {
        if (sel === '.card-head') return makeElement(card.id + '-head');
        if (sel === '.resize-handle') return null;
        return null;
      };
      card.querySelectorAll = () => [];
    });
    const dashboard = elements.get('dashboard');
    dashboard.querySelectorAll = sel => sel === '.card' ? cards : [];

    const sample = {
      status: {
        tsUtc: '2026-05-01T00:00:00+00:00',
        symbol: 'BTCUSDT',
        price: 100,
        equityUsdt: 500,
        btc: 0.1,
        usdt: 490,
        stats: { grid: {}, ai: {} },
        position: {},
      },
      state: {
        paused: false,
        aiEnabled: false,
        symbol: 'BTCUSDT',
        interval: '1m',
        gridMode: 'scalpy',
      },
      runtime: { savedAt: '2026-05-01T00:00:00+00:00', grid: { orders: [] } },
      cumulative: { realizedPnlUsdt: 0, feesPaidUsdt: 0 },
      events: [],
      ohlcv: [],
      freshnessSeconds: 0.1,
      serverTimeUtc: '2026-05-07T13:00:00+00:00',
      seq: 1,
      serverInstanceId: 'server-1',
      channel: 'dashboard',
      aiModels: [],
      aiEndpoints: [],
      aiEndpointModels: {},
      intelligence: {
        newsCards: [{ title: 'Cached item', source: 'Cache', sentiment: 'Neutral', impact: 3 }],
        rawNews: [
          { title: 'Bitcoin raw rise', source: 'RSS', publishedUtc: '2026-05-07T12:00:00+00:00' },
          { title: 'Exchange hack loss', source: 'RSS' },
          { title: 'Macro neutral', source: 'RSS' },
          { title: 'ETF flow update', source: 'RSS' },
        ],
      },
      refreshMs: 1000,
      dashboardRefreshMs: 60000,
    };

    const sandbox = {
      console,
      URL,
      URLSearchParams,
      Number,
      Math,
      Date,
      JSON,
      Object,
      Array,
      Set,
      Map,
      String,
      Error,
      AbortController,
      devicePixelRatio: 1,
      localStorage: { getItem(){ return null; }, setItem(){}, removeItem(){} },
      history: { replaceState(){} },
      document: {
        hidden: false,
        body: { classList: { toggle(){}, add(){}, remove(){} } },
        getElementById(id){ return elements.get(id) || null; },
        createElement(tag){ return makeElement(tag); },
        querySelector(){ return null; },
        querySelectorAll(sel){ return sel === '.card' ? cards : []; },
        addEventListener(){},
      },
      window: {
        location: {
          href: 'http://localhost/?interval=1m',
          origin: 'http://localhost',
          protocol: 'http:',
          host: 'localhost',
          pathname: '/',
          search: '?interval=1m',
        },
        addEventListener(){},
      },
      requestAnimationFrame(fn){ fn(); return 1; },
      cancelAnimationFrame(){},
      setInterval(){ return 1; },
      clearInterval(){},
      setTimeout(){ return 1; },
      clearTimeout(){},
      getComputedStyle(){ return { getPropertyValue(){ return '#111'; } }; },
      fetch(){ return Promise.resolve({ ok: true, json: async () => sample }); },
      WebSocket: function(){ return { addEventListener(){}, close(){} }; },
      EventSource: function(){ return { addEventListener(){}, close(){} }; },
    };
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(script + `
      globalThis.__refreshTest = {
        refresh,
        stateUi,
      };
    `, sandbox);

    [
      'bot-toggle-btn',
      'ai-toggle-btn',
      'fresh-label',
      'server-time',
      'orders-filter-buy-btn',
      'orders-filter-sell-btn',
    ].forEach(id => elements.delete(id));

    (async () => {
      const t = sandbox.__refreshTest;
      await assert.doesNotReject(async () => t.refresh());
      assert.strictEqual(t.stateUi.refreshInFlight, false);
    })().catch(err => {
      console.error(err);
      process.exit(1);
    });
    """
    result = subprocess.run(
        [node, "-e", textwrap.dedent(harness)],
        cwd=".",
        env={"DASHBOARD_SCRIPT": _dashboard_script()},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_ai_decisions_renderer_and_refresh_paths_are_safe(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for dashboard JS unit test")

    harness = r"""
    const assert = require('assert');
    const vm = require('vm');
    const script = process.env.DASHBOARD_SCRIPT;

    function makeElement(id) {
      const ctx = {
        scale(){}, clearRect(){}, beginPath(){}, arc(){}, stroke(){}, fillText(){},
        fillRect(){}, moveTo(){}, lineTo(){}, strokeRect(){}, closePath(){}, fill(){},
      };
      return {
        id,
        tagName: id === 'top-timeframe' ? 'A' : 'DIV',
        href: '',
        textContent: '',
        innerHTML: '',
        children: [],
        disabled: false,
        value: '',
        style: {},
        dataset: {},
        className: '',
        classList: { toggle(){}, add(){}, remove(){} },
        addEventListener(){},
        setAttribute(name, value){ this[name] = value; },
        appendChild(child){ this.children.push(child); },
        querySelector(){ return null; },
        querySelectorAll(){ return []; },
        getBoundingClientRect(){ return { width: 1200, height: 520, left: 0, top: 0 }; },
        getContext(){ return ctx; },
        parentElement: { querySelector(){ return { remove(){} }; } },
      };
    }

    const ids = [
      'theme-toggle','bot-toggle-btn','ai-toggle-btn','reset-layout-btn',
      'events-first-btn','events-prev-btn','events-next-btn','events-last-btn',
      'ai-decisions-first-btn','ai-decisions-prev-btn','ai-decisions-next-btn',
      'ai-decisions-last-btn','orders-tab-open-btn','orders-tab-history-btn',
      'agent-select','agent-thread-select','agent-configure-btn','agent-chat-messages',
      'agent-proposals','agent-chat-input','agent-chat-send-btn',
      'orders-filter-buy-btn','orders-filter-sell-btn','orders-first-btn',
      'orders-prev-btn','orders-next-btn','orders-last-btn','config-save-btn',
      'fresh-label','server-time','sticky-summary','trading-state-label',
      'state-mode','state-risk','state-exposure','state-action','chart-price-pill',
      'chart-quote-line','status-list','events-body','events-page-indicator',
      'ai-decisions-body','ai-decisions-page-indicator','orders-body',
      'orders-page-indicator','timeframe-controls','news-stack','signal-table',
      'final-regime-title','final-regime-copy','regime-updated','macro-calendar',
      'config-form-grid','market-legend','hover-ohlcv','latest-candle',
      'market-chart','chart-stream-status','boot-error','top-timeframe'
    ];
    const elements = new Map(ids.map(id => [id, makeElement(id)]));

    const dashboardSample = {
      status: {
        tsUtc: '2026-05-01T00:00:00+00:00',
        symbol: 'BTCUSDT',
        price: 100,
        equityUsdt: 500,
        btc: 0.1,
        usdt: 490,
        stats: { grid: {}, ai: {} },
        position: {},
      },
      state: { paused: false, aiEnabled: true, symbol: 'BTCUSDT', interval: '1m', gridMode: 'scalpy' },
      runtime: { savedAt: '2026-05-01T00:00:00+00:00', grid: { orders: [] } },
      cumulative: { realizedPnlUsdt: 0, feesPaidUsdt: 0 },
      events: [],
      aiDecisions: [{ decisionId: 'refresh-row', riskAction: 'allow_grid', note: 'from refresh' }],
      ohlcv: [],
      freshnessSeconds: 0.1,
      serverTimeUtc: '2026-05-07T13:00:00+00:00',
      seq: 2,
      serverInstanceId: 'server-1',
      channel: 'dashboard',
      aiModels: [],
      aiEndpoints: [],
      aiEndpointModels: {},
      intelligence: {
        newsCards: [{ title: 'Cached item', source: 'Cache', sentiment: 'Neutral', impact: 3 }],
        rawNews: [
          { title: 'Bitcoin raw rise', source: 'RSS', publishedUtc: '2026-05-07T12:00:00+00:00' },
          { title: 'Exchange hack loss', source: 'RSS' },
          { title: 'Macro neutral', source: 'RSS' },
          { title: 'ETF flow update', source: 'RSS' },
        ],
      },
      refreshMs: 1000,
      dashboardRefreshMs: 60000,
    };

    const sandbox = {
      console,
      URL,
      URLSearchParams,
      Number,
      Math,
      Date,
      JSON,
      Object,
      Array,
      Set,
      Map,
      String,
      Error,
      AbortController,
      devicePixelRatio: 1,
      localStorage: { getItem(){ return null; }, setItem(){}, removeItem(){} },
      history: { replaceState(){} },
      document: {
        hidden: false,
        body: { classList: { toggle(){}, add(){}, remove(){} } },
        getElementById(id){ return elements.get(id) || null; },
        createElement(tag){ return makeElement(tag); },
        querySelector(){ return null; },
        querySelectorAll(){ return []; },
        addEventListener(){},
      },
      window: {
        location: {
          href: 'http://localhost/?interval=1m',
          origin: 'http://localhost',
          protocol: 'http:',
          host: 'localhost',
          pathname: '/',
          search: '?interval=1m',
        },
        addEventListener(){},
      },
      requestAnimationFrame(fn){ fn(); return 1; },
      cancelAnimationFrame(){},
      setInterval(){ return 1; },
      clearInterval(){},
      setTimeout(){ return 1; },
      clearTimeout(){},
      getComputedStyle(){ return { getPropertyValue(){ return '#111'; } }; },
      fetch(){ return Promise.resolve({ ok: true, json: async () => dashboardSample }); },
      WebSocket: function(){ return { addEventListener(){}, close(){} }; },
      EventSource: function(){ return { addEventListener(){}, close(){} }; },
    };
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(script + `
      globalThis.__aiDecisionTest = {
        stateUi,
        renderAiDecisions,
        changeAiDecisionPage,
        applyLiveMarketPayload,
        refresh,
      };
    `, sandbox);

    const t = sandbox.__aiDecisionTest;
    const body = elements.get('ai-decisions-body');
    assert.doesNotThrow(() => t.renderAiDecisions([]));
    assert.ok(body.innerHTML.includes('No AI decisions yet'));
    assert.strictEqual(elements.get('ai-decisions-next-btn').disabled, true);

    const decisions = [
      {
        decision: {
          decisionId: 'nested-<bad>',
          tsUtc: '2026-05-01T00:00:00+00:00',
          riskAction: 'allow_grid',
          confidence: 0.25,
          note: '<script>bad</script>',
          agents: [{ role: 'risk_manager', recommendation: '<hold>', summary: 'watch <risk>' }],
          keyRisks: ['risk <x>'],
          strategyProfile: { name: 'Scalpy <fast>', spacingPct: 0.01, levels: 3 },
          validationReport: { passed: false, error: 'bad <err>' },
        },
      },
      { decisionId: 'flat-2', riskAction: 'pause_new_buys', note: 'flat row' },
      null,
      'bad',
    ];
    assert.doesNotThrow(() => t.renderAiDecisions(decisions));
    assert.strictEqual(t.stateUi.lastAiDecisions.length, 2);
    assert.ok(body.innerHTML.includes('nested-&lt;bad&gt;'));
    assert.ok(body.innerHTML.includes('&lt;script&gt;bad&lt;/script&gt;'));
    assert.ok(!body.innerHTML.includes('<script>'));
    assert.ok(elements.get('agent-select').innerHTML.includes('Risk Manager'));
    assert.ok(elements.get('agent-chat-messages').innerHTML.includes('watch &lt;risk&gt;'));
    assert.ok(!script.includes('/api/agent-chat'));
    assert.ok(!script.includes('/api/agents'));

    assert.doesNotThrow(() => t.changeAiDecisionPage('next'));
    assert.ok(body.innerHTML.includes('flat-2'));
    assert.strictEqual(elements.get('ai-decisions-prev-btn').disabled, false);
    assert.strictEqual(elements.get('ai-decisions-next-btn').disabled, true);

    elements.delete('ai-decisions-body');
    assert.doesNotThrow(() => t.renderAiDecisions(decisions));
    elements.set('ai-decisions-body', body);

    assert.doesNotThrow(() => t.applyLiveMarketPayload({
      ...dashboardSample,
      channel: 'status',
      seq: 1,
      serverTimeUtc: '2026-05-07T13:00:00+00:00',
      aiDecisions: [{ decisionId: 'live-row', riskAction: 'allow_grid', note: 'from live payload' }],
    }));
    assert.ok(body.innerHTML.includes('live-row'));
    assert.ok(elements.get('server-time').textContent.includes('GST'));
    assert.ok(elements.get('macro-calendar').innerHTML.includes('May 7'));
    assert.ok(elements.get('macro-calendar').innerHTML.includes('Completed -'));
    assert.ok(elements.get('macro-calendar').innerHTML.includes('Upcoming -'));
    assert.ok(!elements.get('macro-calendar').innerHTML.includes('May 1'));

    (async () => {
      await assert.doesNotReject(async () => t.refresh());
      assert.ok(body.innerHTML.includes('refresh-row'));
      assert.ok(elements.get('server-time').textContent.includes('GST'));
      assert.ok(elements.get('macro-calendar').innerHTML.includes('May 7'));
      assert.ok(!elements.get('macro-calendar').innerHTML.includes('May 1'));
      const renderedNews = elements.get('news-stack').innerHTML;
      assert.strictEqual((renderedNews.match(/class="news-card"/g) || []).length, 5);
      assert.ok(renderedNews.includes('Bitcoin raw rise'));
    })().catch(err => {
      console.error(err);
      process.exit(1);
    });
    """
    result = subprocess.run(
        [node, "-e", textwrap.dedent(harness)],
        cwd=".",
        env={"DASHBOARD_SCRIPT": _dashboard_script()},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_summary_and_chart_renderers_tolerate_missing_optional_targets(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for dashboard JS unit test")

    harness = r"""
    const assert = require('assert');
    const vm = require('vm');
    const script = process.env.DASHBOARD_SCRIPT;

    function makeElement(id) {
      const ctx = {
        scale(){}, clearRect(){}, beginPath(){}, arc(){}, stroke(){}, fillText(){},
        fillRect(){}, moveTo(){}, lineTo(){}, strokeRect(){}, closePath(){}, fill(){},
      };
      return {
        id,
        tagName: id === 'top-timeframe' ? 'A' : 'DIV',
        href: '',
        textContent: '',
        innerHTML: '',
        children: [],
        disabled: false,
        value: '',
        style: {},
        dataset: {},
        className: '',
        classList: { toggle(){}, add(){}, remove(){} },
        addEventListener(){},
        setAttribute(name, value){ this[name] = value; },
        appendChild(child){ this.children.push(child); },
        querySelector(){ return null; },
        querySelectorAll(){ return []; },
        getBoundingClientRect(){ return { width: 1200, height: 520, left: 0, top: 0 }; },
        getContext(){ return ctx; },
        parentElement: { querySelector(){ return { remove(){} }; } },
      };
    }

    const elements = new Map();
    const addElement = id => elements.set(id, makeElement(id));
    [
      'theme-toggle','bot-toggle-btn','ai-toggle-btn','reset-layout-btn',
      'events-first-btn','events-prev-btn','events-next-btn','events-last-btn',
      'orders-tab-open-btn','orders-tab-history-btn','orders-filter-buy-btn',
      'orders-filter-sell-btn','orders-first-btn','orders-prev-btn',
      'orders-next-btn','orders-last-btn','config-save-btn','market-chart',
      'state-mode','boot-error'
    ].forEach(addElement);

    const sandbox = {
      console,
      URL,
      URLSearchParams,
      Number,
      Math,
      Date,
      JSON,
      Object,
      Array,
      Set,
      Map,
      String,
      Error,
      AbortController,
      devicePixelRatio: 1,
      localStorage: { getItem(){ return null; }, setItem(){}, removeItem(){} },
      history: { replaceState(){} },
      document: {
        hidden: false,
        body: { classList: { toggle(){}, add(){}, remove(){} } },
        getElementById(id){ return elements.get(id) || null; },
        createElement(tag){ return makeElement(tag); },
        querySelector(){ return null; },
        querySelectorAll(){ return []; },
        addEventListener(){},
      },
      window: {
        location: {
          href: 'http://localhost/?interval=1m',
          origin: 'http://localhost',
          protocol: 'http:',
          host: 'localhost',
          pathname: '/',
          search: '?interval=1m',
        },
        addEventListener(){},
      },
      requestAnimationFrame(fn){ fn(); return 1; },
      cancelAnimationFrame(){},
      setInterval(){ return 1; },
      clearInterval(){},
      setTimeout(){ return 1; },
      clearTimeout(){},
      getComputedStyle(){ return { getPropertyValue(){ return '#111'; } }; },
      fetch(){ throw new Error('fetch not expected in unit test'); },
      WebSocket: function(){ throw new Error('websocket not expected in unit test'); },
      EventSource: function(){ throw new Error('eventsource not expected in unit test'); },
    };
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(script + `
      globalThis.__rendererTest = {
        stateUi,
        renderStickySummary,
        drawCandles,
        dashboardModeLabel,
        renderModeControl,
        ensureLayoutControls,
        applyCardHeight,
      };
    `, sandbox);

    const t = sandbox.__rendererTest;
    assert.strictEqual(t.dashboardModeLabel({ gridMode: 'scalpy' }, true), 'Scalpy + Local AI');
    assert.strictEqual(t.dashboardModeLabel({ gridMode: 'fatty' }, false), 'Fatty + Rules');
    assert.strictEqual(t.dashboardModeLabel({ gridMode: 'flexy' }, true), 'Optimized AI');
    assert.strictEqual(t.dashboardModeLabel({ gridMode: 'ai_optimized' }, false), 'Rules');
    assert.strictEqual(t.dashboardModeLabel({ gridMode: 'legacy' }, true), 'Grid + Local AI');
    t.stateUi.lastState = { aiEnabled: true, gridMode: 'flexy' };
    assert.doesNotThrow(() => t.renderModeControl(true));
    assert.ok(elements.get('state-mode').innerHTML.includes('Optimized AI'));
    assert.ok(elements.get('state-mode').innerHTML.includes('disabled'));
    const layoutCard = makeElement('layout-card');
    layoutCard.querySelector = sel => layoutCard.children.find(child =>
      (sel === '.snap-badge' && child.className === 'snap-badge') ||
      (sel === '.resize-handle' && child.className === 'resize-handle')
    ) || null;
    assert.doesNotThrow(() => t.ensureLayoutControls(layoutCard));
    assert.ok(layoutCard.children.some(child => child.className === 'snap-badge'));
    assert.ok(layoutCard.children.some(child => child.className === 'resize-handle'));
    t.applyCardHeight(layoutCard, 500);
    assert.strictEqual(layoutCard.style.height, '500px');
    t.stateUi.lastState = { aiEnabled: true, gridMode: 'scalpy' };
    assert.doesNotThrow(() => t.renderStickySummary(
      {
        price: 100,
        equityUsdt: 500,
        btc: 0.1,
        position: { unrealizedPnlUsdt: 1.25 },
        stats: { ai: { riskAction: 'allow_grid' } },
      },
      { realizedPnlUsdt: 2, feesPaidUsdt: 0.5 },
      {},
      { openOrders: 1 },
    ));
    assert.doesNotThrow(() => t.drawCandles([{
      openTimeMs: 1000,
      closeTimeMs: 1999,
      open: 99,
      high: 101,
      low: 98,
      close: 100,
      volumeUsdt: 25,
      symbol: 'BTCUSDT',
      interval: '1m',
    }]));
    """
    result = subprocess.run(
        [node, "-e", textwrap.dedent(harness)],
        cwd=".",
        env={"DASHBOARD_SCRIPT": _dashboard_script()},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_realtime_chart_bar_merge_semantics(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for dashboard JS unit test")

    harness = r"""
    const assert = require('assert');
    const vm = require('vm');
    const script = process.env.DASHBOARD_SCRIPT;

    function makeElement(id) {
      const ctx = {
        scale(){}, clearRect(){}, beginPath(){}, arc(){}, stroke(){}, fillText(){},
        fillRect(){}, moveTo(){}, lineTo(){}, strokeRect(){},
      };
      return {
        id,
        tagName: id === 'top-timeframe' ? 'A' : 'DIV',
        href: '',
        textContent: '',
        innerHTML: '',
        children: [],
        disabled: false,
        style: {},
        dataset: {},
        className: '',
        classList: { toggle(){}, add(){}, remove(){} },
        addEventListener(){},
        appendChild(child){ this.children.push(child); },
        querySelector(){ return null; },
        querySelectorAll(){ return []; },
        getBoundingClientRect(){ return { width: 1200, height: 520, left: 0 }; },
        getContext(){ return ctx; },
        parentElement: { querySelector(){ return { remove(){} }; } },
      };
    }

    const elements = new Map();
    const addElement = id => {
      const el = makeElement(id);
      elements.set(id, el);
      return el;
    };
    const getElement = id => elements.get(id);
    [
      'theme-toggle','bot-toggle-btn','ai-toggle-btn','reset-layout-btn','events-first-btn','events-prev-btn','events-next-btn','events-last-btn',
      'orders-tab-open-btn','orders-tab-history-btn','orders-filter-buy-btn','orders-filter-sell-btn','orders-first-btn','orders-prev-btn','orders-next-btn','orders-last-btn',
      'config-save-btn','sticky-summary','trading-state-label','state-mode','state-risk','state-exposure','state-action','chart-price-pill','chart-quote-line',
      'fresh-label','server-time','events-body','events-page-indicator','orders-body','orders-page-indicator','status-list','chart-stream-status'
    ].forEach(addElement);

    const sandbox = {
      console,
      URL,
      URLSearchParams,
      Number,
      Math,
      Date,
      JSON,
      Object,
      Array,
      Set,
      Map,
      String,
      Error,
      AbortController,
      devicePixelRatio: 1,
      localStorage: { getItem(){ return null; }, setItem(){}, removeItem(){} },
      document: {
        hidden: true,
        body: { classList: { toggle(){}, add(){}, remove(){} } },
        getElementById(id){ return elements.get(id) || null; },
        createElement(tag){ return makeElement(tag); },
        querySelector(){ return null; },
        querySelectorAll(){ return []; },
        addEventListener(){},
      },
      window: {
        location: { href: 'http://localhost/?interval=1s', origin: 'http://localhost' },
        addEventListener(){},
      },
      requestAnimationFrame(fn){ return 1; },
      cancelAnimationFrame(){},
      setInterval(){ return 1; },
      clearInterval(){},
      setTimeout(){ return 1; },
      clearTimeout(){},
      getComputedStyle(){ return { getPropertyValue(){ return '#111'; } }; },
      fetch(){ throw new Error('fetch not expected in unit test'); },
      WebSocket: function(){ throw new Error('websocket not expected in unit test'); },
    };
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(script + `
      globalThis.__chartTest = {
        stateUi,
        klineToBar,
        upsertRealtimeBar,
        handleTradeMessage,
        intervalDurationMs,
        applyLiveMarketPayload,
        chartWebSocketUrl,
        startChartStream,
        openChartSocket,
        scheduleChartReconnect,
      };
    `, sandbox);

    const t = sandbox.__chartTest;
    const bar = t.klineToBar({ data: { e: 'kline', k: {
      t: 1000, T: 1999, s: 'BTCUSDT', i: '1s',
      o: '10', h: '12', l: '9', c: '11', v: '2.5', q: '27.5',
    } } });
    const plain = value => JSON.parse(JSON.stringify(value));
    assert.deepStrictEqual(plain(bar), {
      openTimeMs: 1000,
      open: 10,
      high: 12,
      low: 9,
      close: 11,
      volumeBase: 2.5,
      closeTimeMs: 1999,
      volumeUsdt: 27.5,
      symbol: 'BTCUSDT',
      interval: '1s',
    });

    t.stateUi.timeframe = '1s';
    t.stateUi.candleLimit = 3;
    t.stateUi.lastOhlcv = [
      {
        openTimeMs: 0, closeTimeMs: 999, open: 9, high: 10, low: 8,
        close: 9.5, interval: '1s', symbol: 'BTCUSDT',
      },
      {
        openTimeMs: 1000, closeTimeMs: 1999, open: 10, high: 11, low: 9,
        close: 10.5, interval: '1s', symbol: 'BTCUSDT',
      },
    ];

    assert.strictEqual(t.upsertRealtimeBar(bar), true);
    assert.strictEqual(t.stateUi.lastOhlcv.length, 2);
    assert.strictEqual(t.stateUi.lastOhlcv[1].close, 11);
    assert.strictEqual(t.stateUi.lastOhlcv[1].high, 12);

    assert.strictEqual(t.upsertRealtimeBar({
      ...bar,
      openTimeMs: 2000,
      closeTimeMs: 2999,
      open: 11,
      high: 13,
      low: 10,
      close: 12,
    }), true);
    assert.strictEqual(t.stateUi.lastOhlcv.length, 3);
    assert.strictEqual(t.stateUi.lastOhlcv[2].openTimeMs, 2000);

    assert.strictEqual(t.upsertRealtimeBar({
      ...bar,
      openTimeMs: 3000,
      closeTimeMs: 3999,
      open: 12,
      high: 14,
      low: 11,
      close: 13,
    }), true);
    assert.deepStrictEqual(plain(t.stateUi.lastOhlcv.map(row => row.openTimeMs)), [0, 1000, 2000, 3000]);

    assert.strictEqual(t.upsertRealtimeBar({ ...bar, openTimeMs: 500, closeTimeMs: 999 }), false);
    assert.deepStrictEqual(plain(t.stateUi.lastOhlcv.map(row => row.openTimeMs)), [0, 1000, 2000, 3000]);

    t.stateUi.lastOhlcv = Array.from({ length: 30 }, (_, i) => ({
      openTimeMs: i * 1000,
      closeTimeMs: i * 1000 + 999,
      open: 10,
      high: 11,
      low: 9,
      close: 10,
      interval: '1s',
      symbol: 'BTCUSDT',
    }));
    assert.strictEqual(t.upsertRealtimeBar({
      ...bar,
      openTimeMs: 30000,
      closeTimeMs: 30999,
      open: 12,
      high: 14,
      low: 11,
      close: 13,
    }), true);
    assert.strictEqual(t.stateUi.lastOhlcv.length, 30);
    assert.strictEqual(t.stateUi.lastOhlcv[0].openTimeMs, 1000);
    assert.strictEqual(t.stateUi.lastOhlcv[29].openTimeMs, 30000);

    t.handleTradeMessage({ data: { e: 'aggTrade', p: '14.5', T: 30500 } });
    assert.strictEqual(t.stateUi.lastOhlcv[29].close, 14.5);
    assert.strictEqual(t.stateUi.lastOhlcv[29].high, 14.5);

    assert.strictEqual(t.intervalDurationMs('1s'), 1000);
    assert.strictEqual(t.intervalDurationMs('1m'), 60000);
    assert.strictEqual(t.intervalDurationMs('1M'), 31 * 24 * 60 * 60 * 1000);

    t.stateUi.timeframe = '1M';
    t.stateUi.lastOhlcv = [{
      openTimeMs: 1777593600000,
      closeTimeMs: 1780271999999,
      open: 78000,
      high: 78200,
      low: 77900,
      close: 78100,
      interval: '1M',
      symbol: 'BTCUSDT',
    }];
    t.handleTradeMessage({ data: { e: 'aggTrade', p: '78350.25', T: 1777654527000 } });
    assert.strictEqual(t.stateUi.lastOhlcv[0].close, 78350.25);
    assert.strictEqual(t.stateUi.lastOhlcv[0].high, 78350.25);

    t.handleTradeMessage({ data: { e: 'aggTrade', p: '79000', T: 1780272001000 } });
    assert.strictEqual(t.stateUi.lastOhlcv[0].close, 78350.25);

    t.stateUi.lastIntelligence = {};
    t.applyLiveMarketPayload({
      status: {
        tsUtc: '2026-05-01T00:00:01+00:00',
        symbol: 'BTCUSDT',
        price: 78000,
        equityUsdt: 500,
        btc: 0.001,
        position: { unrealizedPnlUsdt: 1.2 },
        stats: { grid: {} },
      },
      state: { aiEnabled: true, aiEndpointKey: 'local' },
      runtime: {
        savedAt: '2026-05-01T00:00:01+00:00',
        grid: { orders: [{ side: 'BUY', price: 77000, qty_btc: 0.001 }] },
      },
      cumulative: { realizedPnlUsdt: 2, feesPaidUsdt: 0.5 },
      events: [{ tsUtc: '2026-05-01T00:00:01+00:00', event: 'ENTER', price: 78000, qtyBtc: 0.001 }],
      ohlcv: [],
      refreshMs: 1000,
      serverTimeUtc: '2026-05-01T00:00:02+00:00',
      freshnessSeconds: 0.5,
    }, false);
    assert.strictEqual(getElement('events-body').children.length, 1);
    assert.strictEqual(getElement('orders-body').children.length, 1);
    assert.ok(getElement('status-list').innerHTML.includes('Status timestamp'));

    t.applyLiveMarketPayload({
      status: { price: 78100, stats: { grid: {} } },
      state: { aiEnabled: true },
      runtime: { grid: {} },
      cumulative: {},
      eventsPatch: {
        mode: 'snapshot',
        cursor: 1,
        items: [{
          _eventId: 1,
          tsUtc: '2026-05-01T00:00:01+00:00',
          event: 'ENTER',
          price: 78000,
          qtyBtc: 0.001,
        }],
      },
      ordersPatch: {
        mode: 'snapshot',
        signature: 'a',
        items: [{
          _orderKey: 'buy-1',
          side: 'BUY',
          price: 77000,
          qty_btc: 0.001,
          total: 77,
          type: 'LIMIT',
        }],
        ops: [],
      },
      refreshMs: 1000,
    }, false);
    t.applyLiveMarketPayload({
      status: { price: 78200, stats: { grid: {} } },
      state: { aiEnabled: true },
      runtime: { grid: {} },
      cumulative: {},
      eventsPatch: {
        mode: 'delta',
        cursor: 2,
        items: [{
          _eventId: 2,
          tsUtc: '2026-05-01T00:00:02+00:00',
          event: 'EXIT',
          price: 78200,
          qtyBtc: 0.001,
        }],
      },
      ordersPatch: { mode: 'delta', signature: 'b', items: [], ops: [
        { op: 'remove', key: 'buy-1' },
        {
          op: 'upsert',
          key: 'sell-1',
          item: {
            _orderKey: 'sell-1',
            side: 'SELL',
            price: 79000,
            qty_btc: 0.001,
            total: 79,
            type: 'LIMIT',
          },
        },
      ] },
      refreshMs: 1000,
    }, false);
    assert.strictEqual(t.stateUi.lastEvents.length, 2);
    assert.strictEqual(t.stateUi.lastEvents[1].event, 'EXIT');
    assert.strictEqual(t.stateUi.lastOrders.length, 1);
    assert.strictEqual(t.stateUi.lastOrders[0].side, 'SELL');

    assert.strictEqual(t.applyLiveMarketPayload({
      channel: 'status',
      seq: 10,
      status: { price: 1 },
      runtime: {},
      cumulative: {},
    }, false).status.price, 1);
    assert.strictEqual(t.applyLiveMarketPayload({
      channel: 'status',
      seq: 9,
      status: { price: 2 },
      runtime: {},
      cumulative: {},
    }, false), null);

    const sockets = [];
    const scheduled = [];
    const intervals = [];
    sandbox.WebSocket = function(url) {
      this.url = url;
      this.listeners = {};
      this.addEventListener = (name, fn) => { this.listeners[name] = fn; };
      this.close = () => {};
      sockets.push(this);
    };
    sandbox.setTimeout = (fn, delay) => { scheduled.push({ fn, delay }); return scheduled.length; };
    sandbox.clearTimeout = () => {};
    sandbox.setInterval = (fn, delay) => { intervals.push({ fn, delay }); return intervals.length; };
    sandbox.clearInterval = () => {};
    sandbox.fetch = async () => ({
      ok: true,
      json: async () => ({
        channel: 'status',
        seq: 11,
        status: { price: 1, stats: {} },
        state: {},
        runtime: {},
        cumulative: {},
        events: [],
        ohlcv: [],
        refreshMs: 1000,
      }),
    });

    t.stateUi.timeframe = '1s';
    t.stateUi.lastOhlcv = [];
    t.startChartStream('BTCUSDT', '1s');
    assert.ok(sockets[0].url.startsWith('ws://localhost/ws/chart?'));
    assert.ok(sockets[0].url.includes('interval=1s'));
    sockets[0].listeners.open();
    assert.strictEqual(t.stateUi.chartReconnectAttempt, 0);
    sockets[0].listeners.message({ data: JSON.stringify({
      channel: 'chart',
      seq: 1,
      bar: {
        openTimeMs: 31000,
        closeTimeMs: 31999,
        open: 13,
        high: 15,
        low: 12,
        close: 14,
        volumeBase: 0,
        volumeUsdt: 0,
        symbol: 'BTCUSDT',
        interval: '1s',
      },
      status: { price: 14, stats: {} },
      refreshMs: 1000,
    }) });
    assert.strictEqual(t.stateUi.lastOhlcv[t.stateUi.lastOhlcv.length - 1].close, 14);
    sockets[0].listeners.message({ data: JSON.stringify({
      channel: 'chart',
      seq: 1,
      bar: { openTimeMs: 32000, close: 999, interval: '1s', symbol: 'BTCUSDT' },
      refreshMs: 1000,
    }) });
    assert.notStrictEqual(t.stateUi.lastOhlcv[t.stateUi.lastOhlcv.length - 1].close, 999);
    sockets[0].listeners.close();
    assert.strictEqual(t.stateUi.chartFallbackActive, true);
    assert.strictEqual(scheduled[0].delay, 1000);
    scheduled[0].fn();
    assert.strictEqual(sockets.length, 2);
    t.stateUi.chartLastEventAt = Date.now() - 6000;
    intervals[intervals.length - 1].fn();
    assert.strictEqual(getElement('chart-stream-status').textContent, 'stale');
    """
    result = subprocess.run(
        [node, "-e", textwrap.dedent(harness)],
        cwd=".",
        env={"DASHBOARD_SCRIPT": _dashboard_script()},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
