from urllib.parse import urlparse

import dashboard_server


class DummyHandler:
    def __init__(self, headers):
        self.headers = headers


def test_coerce_state_patch_validates_bounds():
    patch = dashboard_server.coerce_state_patch({"maxDailyLossPct": "0.25", "gridLevels": "12"})

    assert patch == {"gridLevels": 12, "maxDailyLossPct": 0.25}


def test_coerce_state_patch_accepts_local_ai_fields():
    patch = dashboard_server.coerce_state_patch({
        "aiProvider": "ollama",
        "aiBaseUrl": "http://127.0.0.1:11434/v1",
        "aiQuickModel": "qwen2.5:3b",
        "aiDeepModel": "qwen3.6:35b",
        "aiDryRun": "true",
        "aiShadowMode": "false",
        "aiTimeoutSeconds": "120",
        "aiTemperature": "0.2",
    })

    assert patch["aiProvider"] == "ollama"
    assert patch["aiDryRun"] is True
    assert patch["aiShadowMode"] is False
    assert patch["aiTimeoutSeconds"] == 120.0


def test_coerce_state_patch_applies_named_ai_endpoint():
    patch = dashboard_server.coerce_state_patch({
        "aiEndpointKey": "battlestation_gpu",
        "aiProvider": "openai",
        "aiBaseUrl": "http://stale-host:9999/v1",
    })

    assert patch["aiEndpointKey"] == "battlestation_gpu"
    assert patch["aiProvider"] == "ollama"
    assert patch["aiBaseUrl"] == "http://192.168.1.20:11435/v1"


def test_coerce_state_patch_keeps_custom_ai_endpoint_url():
    patch = dashboard_server.coerce_state_patch({
        "aiEndpointKey": "custom",
        "aiBaseUrl": "http://10.0.0.5:11434/v1",
    })

    assert patch["aiEndpointKey"] == "custom"
    assert patch["aiProvider"] == "ollama"
    assert patch["aiBaseUrl"] == "http://10.0.0.5:11434/v1"


def test_infer_ai_endpoint_treats_stale_preset_key_as_custom():
    endpoint_key = dashboard_server.infer_ai_endpoint_key({
        "aiEndpointKey": "local",
        "aiBaseUrl": "http://10.0.0.5:11434/v1",
    })

    assert endpoint_key == "custom"


def test_get_ai_endpoint_models_lists_all_named_endpoints(monkeypatch):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, timeout):
        if "11434" in url:
            return Response({"data": [{"id": "qwen3.5:9b"}]})
        if "11435" in url:
            return Response({"models": [{"name": "qwen3.6:35b"}]})
        if "11436" in url:
            return Response({"models": [{"model": "llama3.2:3b"}]})
        raise RuntimeError(url)

    monkeypatch.setattr(dashboard_server.requests, "get", fake_get)
    dashboard_server._ai_model_cache.clear()

    models = dashboard_server.get_ai_endpoint_models({
        "aiEndpointKey": "local",
        "aiBaseUrl": "http://127.0.0.1:11434/v1",
    })

    assert models["local"] == ["qwen3.5:9b"]
    assert models["battlestation_gpu"] == ["qwen3.6:35b"]
    assert models["battlestation_cpu"] == ["llama3.2:3b"]


def test_coerce_state_patch_rejects_bad_choice():
    try:
        dashboard_server.coerce_state_patch({"gridMode": "chaos"})
    except ValueError as exc:
        assert "gridMode must be one of" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_mutation_auth_allows_when_token_unset(monkeypatch):
    monkeypatch.setattr(dashboard_server, "DASHBOARD_TOKEN", "")

    assert dashboard_server.Handler._mutation_authorized(DummyHandler({}), urlparse("/api/config"))


def test_mutation_auth_checks_header_and_query(monkeypatch):
    monkeypatch.setattr(dashboard_server, "DASHBOARD_TOKEN", "secret")

    assert dashboard_server.Handler._mutation_authorized(
        DummyHandler({"X-Tradebot-Token": "secret"}),
        urlparse("/api/config"),
    )
    assert dashboard_server.Handler._mutation_authorized(DummyHandler({}), urlparse("/api/config?token=secret"))
    assert not dashboard_server.Handler._mutation_authorized(
        DummyHandler({"X-Tradebot-Token": "wrong"}),
        urlparse("/api/config"),
    )


def test_market_payload_can_skip_ohlcv_for_status_poll(monkeypatch):
    def fake_read_json(path):
        if path == dashboard_server.STATUS_PATH:
            return {"symbol": "BTCUSDT", "price": 100.0, "tsUtc": "2026-05-01T00:00:00+00:00"}
        if path == dashboard_server.STATE_PATH:
            return {"symbol": "BTCUSDT", "interval": "1s"}
        return {}

    def fail_get_ohlcv(*args, **kwargs):
        raise AssertionError("ohlcv should not be fetched")

    monkeypatch.setattr(dashboard_server, "read_json", fake_read_json)
    monkeypatch.setattr(dashboard_server, "get_ohlcv", fail_get_ohlcv)
    monkeypatch.setattr(dashboard_server, "read_events", lambda: [{"event": "ENTER", "price": 100.0}])
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.5)

    payload = dashboard_server.build_market_payload({"interval": ["1s"], "ohlcv": ["0"]})

    assert payload["status"]["price"] == 100.0
    assert payload["ohlcv"] == []
    assert payload["events"] == [{"event": "ENTER", "price": 100.0}]
    assert payload["chartInterval"] == "1s"
    assert payload["refreshMs"] >= 1000


def test_market_payload_uses_fresh_status_price_for_active_candle(monkeypatch):
    def fake_read_json(path):
        if path == dashboard_server.STATUS_PATH:
            return {
                "symbol": "BTCUSDT",
                "price": 100.0,
                "usdt": 10.0,
                "btc": 2.0,
                "position": {"qtyBtc": 2.0, "entryPrice": 90.0},
                "tsUtc": "2026-05-01T00:00:00+00:00",
            }
        if path == dashboard_server.STATE_PATH:
            return {"symbol": "BTCUSDT", "interval": "1s"}
        return {}

    def fake_get_ohlcv(*args, **kwargs):
        return [{
            "openTimeMs": 1,
            "open": 99.0,
            "high": 102.0,
            "low": 98.0,
            "close": 101.0,
            "volumeUsdt": 1000.0,
            "symbol": "BTCUSDT",
            "interval": "1s",
        }]

    monkeypatch.setattr(dashboard_server, "read_json", fake_read_json)
    monkeypatch.setattr(dashboard_server, "get_ohlcv", fake_get_ohlcv)
    monkeypatch.setattr(dashboard_server, "read_events", lambda: [])
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.5)

    payload = dashboard_server.build_market_payload({"interval": ["1s"], "ohlcv": ["1"]})

    assert payload["ohlcv"][-1]["close"] == 100.0
    assert payload["status"]["price"] == 100.0
    assert payload["status"]["equityUsdt"] == 210.0
    assert payload["status"]["position"]["unrealizedPnlUsdt"] == 20.0


def test_market_payload_applies_fresh_status_price_to_active_candle(monkeypatch):
    def fake_read_json(path):
        if path == dashboard_server.STATUS_PATH:
            return {
                "symbol": "BTCUSDT",
                "price": 105.0,
                "tsUtc": "2026-05-02T00:00:30+00:00",
            }
        if path == dashboard_server.STATE_PATH:
            return {"symbol": "BTCUSDT", "interval": "1m"}
        return {}

    def fake_get_ohlcv(*args, **kwargs):
        return [{
            "openTimeMs": 1777680000000,
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "volumeUsdt": 1000.0,
            "symbol": "BTCUSDT",
            "interval": "1m",
        }]

    monkeypatch.setattr(dashboard_server, "read_json", fake_read_json)
    monkeypatch.setattr(dashboard_server, "get_ohlcv", fake_get_ohlcv)
    monkeypatch.setattr(dashboard_server, "read_events", lambda: [])
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.5)

    payload = dashboard_server.build_market_payload({"interval": ["1m"], "ohlcv": ["1"]})

    assert payload["status"]["price"] == 105.0
    assert payload["ohlcv"][-1]["close"] == 105.0
    assert payload["ohlcv"][-1]["high"] == 105.0
