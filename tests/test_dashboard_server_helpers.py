from collections import deque
from datetime import datetime, timezone

import pytest

import dashboard_server


def test_data_adapter_wrappers_delegate_to_adapter(tmp_path, monkeypatch):
    calls = []

    class Adapter:
        def write_json(self, path, payload):
            calls.append(("write", path, payload))

        def update_state(self, path, updater):
            calls.append(("update", path))
            return updater({"paused": False})

    monkeypatch.setattr(dashboard_server, "DATA_ADAPTER", Adapter())

    target = tmp_path / "state.json"
    dashboard_server.write_json(target, {"ok": True})
    updated = dashboard_server.update_state_locked(
        lambda current: {**current, "paused": True}
    )

    assert calls == [
        ("write", target, {"ok": True}),
        ("update", dashboard_server.STATE_PATH),
    ]
    assert updated == {"paused": True}


def test_state_ai_models_dedupes_and_strips():
    state = {
        "aiModel": " qwen ",
        "aiQuickModel": "llama",
        "aiDeepModel": "qwen",
        "aiFallbackModel": "",
    }

    assert dashboard_server._state_ai_models(state) == ["qwen", "llama"]


def test_stop_local_ollama_models_uses_patched_subprocess_only(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[-1] == "failing":
            raise RuntimeError("stop failed")

    monkeypatch.setattr(dashboard_server.subprocess, "run", fake_run)

    dashboard_server._stop_local_ollama_models({
        "aiEndpointKey": "custom",
        "aiBaseUrl": "http://localhost:11434/v1",
        "aiModel": "qwen",
        "aiQuickModel": "llama",
        "aiDeepModel": "qwen",
        "aiFallbackModel": "failing",
    })

    assert [cmd for cmd, _kwargs in calls] == [
        ["ollama", "stop", "qwen"],
        ["ollama", "stop", "llama"],
        ["ollama", "stop", "failing"],
    ]
    assert all(kwargs["cwd"] == str(dashboard_server.BASE_DIR) for _cmd, kwargs in calls)
    assert all(kwargs["timeout"] == 5 for _cmd, kwargs in calls)

    dashboard_server._stop_local_ollama_models({
        "aiEndpointKey": "custom",
        "aiBaseUrl": "http://10.0.0.5:11434/v1",
        "aiModel": "remote-model",
    })

    assert len(calls) == 3


def test_sync_ai_sidecar_for_state_uses_patched_boundaries(monkeypatch):
    run_calls = []
    writes = []
    stopped_states = []

    def fake_run(cmd, **kwargs):
        run_calls.append((cmd, kwargs))
        raise RuntimeError("subprocess disabled in tests")

    monkeypatch.setattr(dashboard_server.subprocess, "run", fake_run)
    monkeypatch.setattr(
        dashboard_server,
        "write_json",
        lambda path, payload: writes.append((path, payload)),
    )
    monkeypatch.setattr(
        dashboard_server,
        "_stop_local_ollama_models",
        lambda state: stopped_states.append(state),
    )

    state = {
        "aiEnabled": True,
        "aiEndpointKey": "custom",
        "aiBaseUrl": "http://127.0.0.1:11434/v1",
        "aiModel": "qwen",
    }

    dashboard_server._sync_ai_sidecar_for_state(state, {"aiEnabled": False})
    dashboard_server._sync_ai_sidecar_for_state(state, {"aiEnabled": True})
    dashboard_server._sync_ai_sidecar_for_state(state, {"aiModel": "llama"})

    assert [cmd[-1] for cmd, _kwargs in run_calls] == ["stop", "start", "restart"]
    assert writes[0][0] == dashboard_server.AI_SIGNAL_PATH
    assert writes[0][1]["enabled"] is False
    assert writes[0][1]["source"] == "disabled"
    assert stopped_states == [state]


def test_coerce_state_patch_rejects_bounds_and_skips_unknown_caster(monkeypatch):
    monkeypatch.setitem(dashboard_server.EDITABLE_STATE_FIELDS, "unknownField", object)

    assert dashboard_server.coerce_state_patch({"unknownField": "ignored"}) == {}

    with pytest.raises(ValueError, match="gridLevels outside allowed range"):
        dashboard_server.coerce_state_patch({"gridLevels": "101"})


def test_ai_model_fetch_all_failures_are_cached(monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append((url, timeout))
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(dashboard_server.requests, "get", fake_get)
    dashboard_server._ai_model_cache.clear()
    try:
        assert dashboard_server.fetch_ai_models("http://ai.local", force=True) == []
        assert [url for url, _timeout in calls] == [
            "http://ai.local/models",
            "http://ai.local/v1/models",
            "http://ai.local/api/tags",
        ]

        calls.clear()
        assert dashboard_server.fetch_ai_models("http://ai.local") == []
        assert calls == []
    finally:
        dashboard_server._ai_model_cache.clear()


def test_get_ai_endpoint_models_leaves_custom_empty_when_not_selected(monkeypatch):
    calls = []

    def fake_fetch(base_url, force=False):
        calls.append((base_url, force))
        return [f"model:{base_url}"]

    monkeypatch.setattr(dashboard_server, "fetch_ai_models", fake_fetch)

    models = dashboard_server.get_ai_endpoint_models({
        "aiEndpointKey": "local",
        "aiBaseUrl": "http://127.0.0.1:11434/v1",
    })

    assert models["custom"] == []
    assert models["local"] == ["model:http://127.0.0.1:11434/v1"]
    assert all(base_url for base_url, _force in calls)


def test_backfill_history_from_tmp_engine_log(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_server, "BASE_DIR", tmp_path)

    full = deque(({"ts": str(i)} for i in range(180)), maxlen=5000)
    dashboard_server._backfill_history_from_logs(full)
    assert len(full) == 180

    missing_log_items = deque()
    dashboard_server._backfill_history_from_logs(missing_log_items)
    assert missing_log_items == deque()

    (tmp_path / "engine.log").write_text(
        "\n".join([
            "[2026-05-01 00:00:00 UTC] GRID_INIT ok anchor=100.50",
            "[2026-05-01 00:00:01 UTC] GRID_TRAIL_STOP hit price=99.25",
            "unrelated line",
        ]),
        encoding="utf-8",
    )
    items = deque([{"ts": "2026-05-01T00:00:00+00:00"}], maxlen=5000)

    dashboard_server._backfill_history_from_logs(items)

    assert list(items) == [
        {"ts": "2026-05-01T00:00:00+00:00"},
        {"ts": "2026-05-01T00:00:01+00:00", "price": 99.25, "equity": 500.0},
    ]


def test_update_history_imports_legacy_temp_history_and_upserts_status(
    tmp_path,
    monkeypatch,
):
    history_path = tmp_path / "dashboard_history.json"
    history_path.write_text("{}", encoding="utf-8")
    legacy_items = [{"ts": "legacy", "price": 100.0, "equity": 500.0}]
    imports = []
    upserts = []

    monkeypatch.setattr(dashboard_server, "HISTORY_PATH", history_path)
    monkeypatch.setattr(
        dashboard_server.sqlite_store,
        "read_history",
        lambda limit: {"items": []},
    )
    monkeypatch.setattr(
        dashboard_server,
        "read_json",
        lambda path: {"items": legacy_items} if path == history_path else {},
    )
    monkeypatch.setattr(
        dashboard_server.sqlite_store,
        "import_history_items",
        lambda items: imports.append(list(items)),
    )
    monkeypatch.setattr(
        dashboard_server.sqlite_store,
        "upsert_history_item",
        lambda ts, price, equity: upserts.append((ts, price, equity)),
    )
    monkeypatch.setattr(
        dashboard_server,
        "_backfill_history_from_logs",
        lambda items: items.append({
            "ts": "backfilled",
            "price": 101.0,
            "equity": 501.0,
        }),
    )

    payload = dashboard_server.update_history({
        "tsUtc": "fresh",
        "price": 102.0,
        "equityUsdt": 502.0,
    })

    assert payload["items"] == [
        {"ts": "legacy", "price": 100.0, "equity": 500.0},
        {"ts": "backfilled", "price": 101.0, "equity": 501.0},
        {"ts": "fresh", "price": 102.0, "equity": 502.0},
    ]
    assert imports[0] == legacy_items
    assert imports[-1] == payload["items"]
    assert upserts == [("fresh", 102.0, 502.0)]
    assert '"fresh"' in history_path.read_text(encoding="utf-8")


def test_freshness_seconds_handles_empty_invalid_naive_and_aware():
    aware = datetime.now(timezone.utc).isoformat()
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    assert dashboard_server.freshness_seconds(None) is None
    assert dashboard_server.freshness_seconds("not a timestamp") is None
    assert isinstance(dashboard_server.freshness_seconds(aware), float)
    assert isinstance(dashboard_server.freshness_seconds(naive), float)


def test_get_ohlcv_uses_cache_offset_and_exception_fallback_without_binance(
    monkeypatch,
):
    class Client:
        def __init__(self):
            self.calls = []
            self.fail = False

        def klines(self, **kwargs):
            self.calls.append(kwargs)
            if self.fail:
                raise RuntimeError("binance disabled in tests")
            return [
                [1, "100", "105", "99", "101", "1", 999, "101"],
                [1000, "101", "106", "100", "102", "2", 1999, "204"],
                [2000, "102", "107", "101", "103", "3", 2999, "309"],
            ]

    client = Client()
    monkeypatch.setattr(dashboard_server, "get_md_client", lambda interval: client)
    dashboard_server._ohlcv_cache.clear()
    dashboard_server._ohlcv_cache_at.clear()
    try:
        rows = dashboard_server.get_ohlcv("BTCUSDT", "1m", limit=30, offset=1)
        assert [row["close"] for row in rows] == [101.0, 102.0]
        assert client.calls == [{
            "symbol": "BTCUSDT",
            "interval": "1m",
            "limit": 31,
        }]

        cached = dashboard_server.get_ohlcv("BTCUSDT", "1m", limit=30, offset=1)
        assert cached == rows
        assert len(client.calls) == 1

        stale_key = ("ETHUSDT", "1m", 30, 0)
        dashboard_server._ohlcv_cache[stale_key] = [{"cached": True}]
        dashboard_server._ohlcv_cache_at[stale_key] = 0.0
        client.fail = True

        assert dashboard_server.get_ohlcv("ETHUSDT", "1m", limit=30) == [
            {"cached": True}
        ]
    finally:
        dashboard_server._ohlcv_cache.clear()
        dashboard_server._ohlcv_cache_at.clear()


def test_parse_json_object_embedded_empty_and_missing():
    assert dashboard_server._parse_json_object('prefix {"ok": true} suffix') == {
        "ok": True
    }

    with pytest.raises(ValueError, match="empty local AI response"):
        dashboard_server._parse_json_object("")
    with pytest.raises(ValueError, match="no JSON object"):
        dashboard_server._parse_json_object("plain text")


def test_fallback_intelligence_scores_news_sentiment_without_padding():
    ohlcv = [
        {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
        {"open": 101.0, "high": 112.0, "low": 100.0, "close": 111.0},
    ]

    payload = dashboard_server._fallback_intelligence(
        {"price": 111.0, "equityUsdt": 1000.0, "btc": 0.1},
        ohlcv,
        [
            {
                "title": "Bitcoin surge as ETF inflow grows",
                "source": "Fake",
                "publishedUtc": "2026-05-01T00:00:00+00:00",
            },
            {"title": "Exchange hack loss rattles crypto", "source": "Fake"},
        ],
        "local AI disabled",
    )
    down_rows, down_final = dashboard_server._deterministic_regime_rows(
        {"price": 90.0},
        [
            {"open": 100.0, "high": 101.0, "low": 89.0, "close": 90.0},
        ],
    )

    assert [card["sentiment"] for card in payload["newsCards"]] == ["Bullish", "Bearish"]
    assert payload["newsCards"][0]["impact"] == 6
    assert payload["source"] == "deterministic_fallback"
    assert payload["regimeSignals"][-1]["status"] == "Watch"
    assert down_rows[0]["status"] == "Downtrend"
    assert down_final["title"] == "Downtrend"


def test_gst_server_time_and_macro_calendar_update_daily():
    rendered_time = dashboard_server.format_gst_datetime(
        datetime(2026, 5, 7, 17, 30, 45, tzinfo=timezone.utc)
    )
    naive_time = dashboard_server.format_gst_datetime(
        datetime(2026, 5, 7, 0, 0, 0)
    )

    assert rendered_time == "May 7 2026, 9:30:45 PM GST"
    assert naive_time == "May 7 2026, 4:00:00 AM GST"

    events = dashboard_server._macro_calendar_events(
        datetime(2026, 5, 7, 13, 0, 0, tzinfo=timezone.utc)
    )
    naive_events = dashboard_server._macro_calendar_events(
        datetime(2026, 5, 7, 13, 0, 0)
    )
    assert len(events) > 3600
    assert min(event["year"] for event in events) == 2025
    assert max(event["year"] for event in events) == 2027
    assert any(event["date"] == "May 7, 2026" for event in naive_events)
    page_rows, total_pages, page, total_events = dashboard_server._macro_calendar_page(
        events
    )
    assert page == 0
    assert total_pages > 300
    assert total_events == len(events)
    assert len(page_rows) == 10
    assert [event["status"] for event in page_rows[:5]] == ["Completed"] * 5
    assert [event["status"] for event in page_rows[5:]] == ["Upcoming"] * 5
    assert page_rows[0]["sortTs"] > page_rows[1]["sortTs"]
    assert page_rows[5]["sortTs"] < page_rows[6]["sortTs"]

    may_2026_us_data = [
        event for event in events
        if event["month"] == 5
        and event["year"] == 2026
        and event["title"] == "US Data Window"
    ]
    filtered_rows, filtered_pages, _page, _total = dashboard_server._macro_calendar_page(
        may_2026_us_data
    )
    assert filtered_pages >= 3
    assert len(filtered_rows) == 10
    assert {event["title"] for event in filtered_rows} == {"US Data Window"}
    completed_only_rows, completed_only_pages, completed_only_page, completed_total = (
        dashboard_server._macro_calendar_page(
            [{"status": "Completed", "sortTs": idx} for idx in range(12)],
            page=1,
        )
    )
    empty_rows, empty_pages, empty_page, empty_total = dashboard_server._macro_calendar_page([])
    assert [event["sortTs"] for event in completed_only_rows] == [1, 0]
    assert completed_only_pages == 2
    assert completed_only_page == 1
    assert completed_total == 12
    assert empty_rows == []
    assert empty_pages == 1
    assert empty_page == 0
    assert empty_total == 0

    completed_rows, completed_pages, completed_page, completed_total = dashboard_server._macro_calendar_status_page(
        events,
        "Completed",
    )
    upcoming_rows, upcoming_pages, upcoming_page, upcoming_total = dashboard_server._macro_calendar_status_page(
        events,
        "Upcoming",
    )
    assert completed_page == 0
    assert upcoming_page == 0
    assert completed_pages > 50
    assert upcoming_pages > 50
    assert completed_total + upcoming_total == len(events)
    assert len(completed_rows) == 33
    assert len(upcoming_rows) == 32
    assert {event["status"] for event in completed_rows} == {"Completed"}
    assert {event["status"] for event in upcoming_rows} == {"Upcoming"}
    assert completed_rows[0]["sortTs"] > completed_rows[1]["sortTs"]
    assert upcoming_rows[0]["sortTs"] < upcoming_rows[1]["sortTs"]
    assert {(event["month"], event["day"]) for event in completed_rows} == {
        (5, 1),
        (5, 2),
        (5, 3),
        (5, 4),
        (5, 5),
        (5, 6),
        (5, 7),
    }
    assert {(event["month"], event["day"]) for event in upcoming_rows} == {
        (5, 7),
        (5, 8),
        (5, 9),
        (5, 10),
        (5, 11),
        (5, 12),
        (5, 13),
    }

    completed_html = dashboard_server._render_macro_calendar(
        "Completed",
        datetime(2026, 5, 7, 13, 0, 0, tzinfo=timezone.utc)
    )
    upcoming_html = dashboard_server._render_macro_calendar(
        "Upcoming",
        datetime(2026, 5, 7, 13, 0, 0, tzinfo=timezone.utc)
    )
    naive_rendered_html = dashboard_server._render_macro_calendar(
        "Completed",
        datetime(2026, 5, 7, 13, 0, 0)
    )
    assert "US Data Window" in completed_html
    assert "US Data Window" in naive_rendered_html
    assert "Completed - US data window passed" in completed_html
    assert "Upcoming - Watch ETF flow" in upcoming_html
    assert (
        'US Data Window <span class="calendar-event-flag" '
        'title="United States">🇺🇸</span>'
    ) in completed_html
    assert (
        'Europe Macro / Yields Check <span class="calendar-event-flag" '
        'title="Europe">🇪🇺</span>'
    ) in completed_html
    assert (
        'Asia Liquidity Open <span class="calendar-event-flag" '
        'title="Asia session">🇯🇵</span>'
    ) in upcoming_html
    assert (
        'Daily Close Risk Review <span class="calendar-event-flag" '
        'title="Global crypto close">🇺🇳</span>'
    ) in upcoming_html
    assert completed_html.count('class="calendar-day-group') == 7
    assert upcoming_html.count('class="calendar-day-group') == 7
    assert completed_html.count('class="calendar-row') == 33
    assert upcoming_html.count('class="calendar-row') == 32
    assert 'class="status-chip' not in completed_html
    assert 'class="status-chip' not in upcoming_html
    assert completed_html.count('class="calendar-icon-day">7</span>') == 1
    assert 'class="calendar-icon-day">6</span>' in completed_html
    assert 'class="calendar-icon-day">1</span>' in completed_html
    assert 'class="calendar-icon-month">May</span>' in completed_html
    assert 'class="calendar-icon-delta"' not in completed_html
    assert "-0h30m" not in completed_html
    assert 'class="calendar-icon-delta">0h30m</span>' in upcoming_html
    assert 'class="calendar-event-time">5:30 PM GST (0h30m)</div>' in upcoming_html
    assert 'class="calendar-event-time">4:30 PM GST</div>' in completed_html
    assert '<span class="calendar-meta">May 7, 2026' not in completed_html
    assert '<span class="calendar-meta">May 7, 2026' not in upcoming_html
    assert dashboard_server._macro_calendar_delta_label(
        {"sortTs": datetime(2026, 5, 20, 13, 0, 0, tzinfo=timezone.utc).timestamp()},
        datetime(2026, 5, 7, 13, 0, 0, tzinfo=timezone.utc),
    ) == "13d0h"


def test_format_and_server_render_helpers_cover_empty_and_value_rows():
    assert dashboard_server._fmt_num("bad") == "--"
    assert dashboard_server._fmt_pct("bad") == "--"
    assert dashboard_server._signed_class("bad") == ""
    assert dashboard_server._strip_html("<b>Hello</b>\n<span>world</span>") == (
        "Hello world"
    )
    assert dashboard_server._render_server_chart_svg([], None) == (
        '<div class="server-chart-fallback"></div>'
    )

    events_html = dashboard_server._render_server_events([
        {
            "tsUtc": "2026-05-01T00:00:00+00:00",
            "event": "ENTER",
            "price": 100.0,
            "qtyBtc": 0.1234567,
        }
    ])
    orders_html = dashboard_server._render_server_orders([
        {
            "side": "BUY",
            "price": 90.0,
            "qty_btc": 0.1,
            "type": "LIMIT",
        }
    ], 100.0)
    impact_html = dashboard_server._render_impact_bars("bad", "green", size=4)
    news_html = dashboard_server._render_server_news({
        "newsCards": [{
            "title": "Bitcoin rally",
            "source": "Fake",
            "age": "now",
            "sentiment": "Bullish",
            "impact": 4,
            "url": "https://example.test/story",
        }]
    })
    signals_html = dashboard_server._render_server_signals({
        "regimeSignals": [{
            "signal": "Trend",
            "status": "Range",
            "note": "Calm",
            "tone": "blue",
        }]
    })
    many_news_html = dashboard_server._render_server_news({
        "newsCards": [
            {
                "title": f"Story {idx}",
                "source": "Fake",
                "age": "now",
                "sentiment": "Neutral",
                "impact": 2,
            }
            for idx in range(6)
        ]
    })
    raw_padded_news_html = dashboard_server._render_server_news({
        "newsCards": [{"title": "Existing", "source": "Fake"}],
        "rawNews": [
            {"title": "Bitcoin raw rise", "source": "RSS", "publishedUtc": "2026-05-07T12:00:00+00:00"},
            {"title": "Exchange hack loss", "source": "RSS"},
            {"title": "Macro neutral", "source": "RSS"},
            {"title": "ETF flow update", "source": "RSS"},
        ],
    })

    assert "ENTER" in events_html
    assert "Below market 10.00%" in orders_html
    assert impact_html.count("on green") == 3
    assert '<a href="https://example.test/story"' in news_html
    assert many_news_html.count('class="news-card"') == 5
    assert raw_padded_news_html.count('class="news-card"') == 5
    assert "Bitcoin raw rise" in raw_padded_news_html
    assert "Exchange hack loss" in raw_padded_news_html
    assert "var(--blue)" in signals_html


def test_fetch_news_items_parses_fake_rss_and_dedupes(monkeypatch):
    class Response:
        content = b"""<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>Bitcoin rally gains steam</title>
    <link>https://example.test/bitcoin</link>
    <description>ETF inflow and BTC rise</description>
    <pubDate>Mon, 04 May 2026 12:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Bitcoin rally gains steam</title>
    <link>https://example.test/duplicate</link>
    <description>ETF inflow and BTC liquidity duplicate</description>
    <pubDate>Mon, 04 May 2026 12:01:00 GMT</pubDate>
  </item>
  <item>
    <title>Macro rates watch</title>
    <link>https://example.test/macro</link>
    <description>Inflation and dollar liquidity</description>
    <pubDate>not-a-date</pubDate>
  </item>
  <item>
    <title>BTC liquidity build</title>
    <link>https://example.test/btc-liquidity</link>
    <description>Liquidity improves while BTC holds bid</description>
    <pubDate>Mon, 04 May 2026 12:02:00</pubDate>
  </item>
  <item>
    <title>Old BTC cycle recap</title>
    <link>https://example.test/old-btc</link>
    <description>Stale item outside dashboard history window</description>
    <pubDate>Mon, 01 Dec 2025 12:02:00 GMT</pubDate>
  </item>
  <item>
    <title></title>
    <description>Untitled item is skipped</description>
  </item>
</channel></rss>"""

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        assert kwargs["headers"]["User-Agent"] == "tradebot-dashboard/1.0"
        if url.endswith("/bad"):
            raise RuntimeError("rss disabled in tests")
        return Response()

    monkeypatch.setattr(
        dashboard_server,
        "NEWS_SOURCES",
        [
            ("Fake RSS", "https://example.test/good"),
            ("Broken RSS", "https://example.test/bad"),
        ],
    )
    monkeypatch.setattr(dashboard_server.requests, "get", fake_get)

    items = dashboard_server._fetch_news_items(
        limit=3,
        now=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    items_by_title = {item["title"]: item for item in items}

    assert set(items_by_title) == {
        "BTC liquidity build",
        "Macro rates watch",
        "Bitcoin rally gains steam",
    }
    assert "Old BTC cycle recap" not in items_by_title
    assert [item["title"] for item in items].count("Bitcoin rally gains steam") == 1
    assert items_by_title["Macro rates watch"]["publishedUtc"] == "not-a-date"
    assert items_by_title["BTC liquidity build"]["publishedUtc"] == (
        "2026-05-04T12:02:00+00:00"
    )
    assert items_by_title["Bitcoin rally gains steam"]["source"] == "Fake RSS"
    assert all("sortTs" not in item and "score" not in item for item in items)


def test_merge_news_history_keeps_recent_cached_items_and_drops_old():
    merged = dashboard_server._merge_news_history(
        [{"title": "Fresh story", "source": "RSS", "publishedUtc": "2026-05-08T10:00:00+00:00"}],
        [
            {"title": "Cached recent", "source": "Cache", "publishedUtc": "2026-04-10T10:00:00+00:00"},
            {"title": "Too old", "source": "Cache", "publishedUtc": "2025-12-01T10:00:00+00:00"},
        ],
        now=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )

    assert [item["title"] for item in merged] == ["Fresh story", "Cached recent"]


def test_local_ai_assess_intelligence_uses_fake_ollama_and_openai(monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(url, timeout, **kwargs):
        calls.append((url, timeout, kwargs["json"]))
        if url.endswith("/api/chat"):
            return Response({
                "message": {
                    "content": (
                        'prefix {"newsCards": [], "regimeSignals": [], '
                        '"finalRegime": {"title": "Ollama", "copy": "ok"}} suffix'
                    )
                }
            })
        return Response({
            "choices": [{
                "message": {
                    "content": (
                        '{"newsCards": [], "regimeSignals": [], '
                        '"finalRegime": {"title": "OpenAI", "copy": "ok"}}'
                    )
                }
            }]
        })

    monkeypatch.setattr(dashboard_server.requests, "post", fake_post)

    ollama = dashboard_server._local_ai_assess_intelligence(
        {
            "aiProvider": "ollama",
            "aiBaseUrl": "http://127.0.0.1:11434/v1",
            "aiModel": "qwen",
            "aiTimeoutSeconds": 1,
        },
        {"symbol": "BTCUSDT"},
        [],
        [],
    )
    openai = dashboard_server._local_ai_assess_intelligence(
        {
            "aiProvider": "openai",
            "aiBaseUrl": "http://ai.local/v1",
            "aiModel": "gpt",
            "aiTimeoutSeconds": 999,
        },
        {"symbol": "BTCUSDT"},
        [],
        [],
    )

    assert ollama["source"] == "local_ai"
    assert ollama["model"] == "qwen"
    assert openai["finalRegime"]["title"] == "OpenAI"
    assert calls[0][0] == "http://127.0.0.1:11434/api/chat"
    assert calls[0][1] == 10.0
    assert calls[1][0] == "http://ai.local/v1/chat/completions"
    assert calls[1][1] == 120.0


def test_refresh_and_get_intelligence_use_patched_boundaries(monkeypatch):
    writes = []
    news_items = [{"title": "Bitcoin surge", "source": "Fake"}]

    monkeypatch.setattr(dashboard_server, "_fetch_news_items", lambda: news_items)
    monkeypatch.setattr(
        dashboard_server,
        "write_json",
        lambda path, payload: writes.append((path, payload)),
    )

    disabled = dashboard_server.refresh_intelligence(
        {"aiEnabled": False},
        {"price": 100.0},
        [],
    )
    assert disabled["source"] == "deterministic_fallback"
    assert disabled["error"] == "local AI disabled"

    def fail_assess(*args, **kwargs):
        raise RuntimeError("local AI disabled in tests")

    monkeypatch.setattr(
        dashboard_server,
        "_local_ai_assess_intelligence",
        fail_assess,
    )
    failed = dashboard_server.refresh_intelligence(
        {"aiEnabled": True},
        {"price": 100.0},
        [],
    )
    assert failed["source"] == "deterministic_fallback"
    assert failed["error"] == "local AI disabled in tests"
    assert len(writes) == 2

    cached = {"generatedAtUtc": datetime.now(timezone.utc).isoformat(), "ok": True}
    monkeypatch.setattr(dashboard_server, "read_json", lambda path: cached)
    monkeypatch.setattr(
        dashboard_server,
        "refresh_intelligence",
        lambda *args, **kwargs: {"forced": True},
    )

    assert dashboard_server.get_intelligence({}, {}, []) == cached
    assert dashboard_server.get_intelligence({}, {}, [], force=True) == {
        "forced": True
    }


def test_build_market_payload_defaults_bad_limit_and_offset(monkeypatch):
    calls = []

    def fake_read_json(path):
        if path == dashboard_server.STATUS_PATH:
            return {
                "symbol": "BTCUSDT",
                "interval": "1s",
                "price": 100.0,
                "tsUtc": "2026-05-02T00:00:00+00:00",
                "stats": {"grid": {}},
            }
        if path == dashboard_server.STATE_PATH:
            return {"symbol": "BTCUSDT", "interval": "1s", "aiEndpointKey": "local"}
        if path == dashboard_server.RUNTIME_PATH:
            return {"grid": {"orders": []}}
        if path == dashboard_server.CUM_PATH:
            return {"realizedPnlUsdt": 0.0, "feesPaidUsdt": 0.0}
        return {}

    def fake_get_ohlcv(symbol, interval, limit, offset):
        calls.append((symbol, interval, limit, offset))
        return [{
            "openTimeMs": 1777680000000,
            "open": 99.0,
            "high": 101.0,
            "low": 98.0,
            "close": 100.0,
            "volumeBase": 1.0,
            "closeTimeMs": 1777680000999,
            "volumeUsdt": 100.0,
            "symbol": symbol,
            "interval": interval,
        }]

    monkeypatch.setattr(dashboard_server, "read_json", fake_read_json)
    monkeypatch.setattr(dashboard_server, "get_ohlcv", fake_get_ohlcv)
    monkeypatch.setattr(dashboard_server, "read_events", lambda: [])
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.1)

    payload = dashboard_server.build_market_payload({
        "interval": ["1s"],
        "limit": ["bad"],
        "offset": ["bad"],
    })

    assert calls == [("BTCUSDT", "1s", 240, 0)]
    assert payload["chartLimit"] == 240
    assert payload["chartOffset"] == 0
