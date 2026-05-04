import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import dashboard_server


def _start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _url(server, path):
    host, port = server.server_address
    return f"http://{host}:{port}{path}"


def _request(server, path, *, method="GET", body=None, headers=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        _url(server, path),
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return response.status, response.headers, response.read()


def _fake_read_json(path):
    if path == dashboard_server.STATUS_PATH:
        return {
            "symbol": "BTCUSDT",
            "interval": "1s",
            "price": 100.0,
            "equityUsdt": 500.0,
            "btc": 0.1,
            "usdt": 490.0,
            "tsUtc": "2026-05-02T00:00:00+00:00",
            "stats": {"grid": {}, "ai": {}},
            "position": {},
        }
    if path == dashboard_server.STATE_PATH:
        return {"symbol": "BTCUSDT", "interval": "1s", "aiEnabled": False, "paused": False}
    if path == dashboard_server.RUNTIME_PATH:
        return {"savedAt": "2026-05-02T00:00:00+00:00", "grid": {"orders": []}}
    if path == dashboard_server.CUM_PATH:
        return {"realizedPnlUsdt": 0.0, "feesPaidUsdt": 0.0, "trades": 0, "wins": 0, "losses": 0}
    return {}


def _fake_ohlcv(*args, **kwargs):
    return [{
        "openTimeMs": 1777680000000,
        "open": 99.0,
        "high": 101.0,
        "low": 98.0,
        "close": 100.0,
        "volumeBase": 1.0,
        "closeTimeMs": 1777680000999,
        "volumeUsdt": 100.0,
        "symbol": "BTCUSDT",
        "interval": "1s",
    }]


def _patch_dashboard_reads(monkeypatch):
    monkeypatch.setattr(dashboard_server, "read_json", _fake_read_json)
    monkeypatch.setattr(dashboard_server, "read_events", lambda: [])
    monkeypatch.setattr(dashboard_server, "get_ohlcv", _fake_ohlcv)
    monkeypatch.setattr(dashboard_server, "get_intelligence", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        dashboard_server,
        "ai_endpoint_payload",
        lambda state: ({"key": "local", "label": "Local"}, {"local": []}),
    )
    monkeypatch.setattr(dashboard_server, "update_history", lambda status: {"items": []})
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.1)


def test_root_dashboard_route_returns_html_shell(monkeypatch):
    _patch_dashboard_reads(monkeypatch)
    server = _start_server()
    try:
        status, headers, body = _request(server, "/?interval=1s")
    finally:
        server.shutdown()
        server.server_close()

    html = body.decode("utf-8")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert '<script src="/static/dashboard.v1.js' in html
    assert 'href="/static/dashboard.v1.css' in html
    assert "BTCUSDT" in html


def test_static_route_serves_dashboard_js_asset(monkeypatch, tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    asset = static_dir / "dashboard.v1.js"
    asset.write_text("console.log('dashboard');\n", encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "STATIC_DIR", static_dir)

    server = _start_server()
    try:
        status, headers, body = _request(server, "/static/dashboard.v1.js?v=4")
    finally:
        server.shutdown()
        server.server_close()

    assert status == 200
    assert headers["Content-Type"].startswith("application/javascript")
    assert headers["Cache-Control"] == "public, max-age=300"
    assert body == b"console.log('dashboard');\n"


def test_static_route_returns_404_for_missing_asset(monkeypatch, tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    monkeypatch.setattr(dashboard_server, "STATIC_DIR", static_dir)

    server = _start_server()
    try:
        try:
            _request(server, "/static/missing.css")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            assert exc.read() == b"Not found"
        else:
            raise AssertionError("expected 404")
    finally:
        server.shutdown()
        server.server_close()


def test_api_dashboard_route_returns_dashboard_json_contract(monkeypatch):
    _patch_dashboard_reads(monkeypatch)
    server = _start_server()
    try:
        status, headers, body = _request(server, "/api/dashboard?interval=1s&limit=5&offset=2&ohlcv=0")
    finally:
        server.shutdown()
        server.server_close()

    payload = json.loads(body.decode("utf-8"))
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["schemaVersion"] == "dashboard.snapshot.v1"
    assert payload["channel"] == "dashboard"
    assert payload["chartInterval"] == "1s"
    assert payload["chartLimit"] == 30
    assert payload["chartOffset"] == 2
    assert payload["ohlcv"] == []
    assert payload["state"]["aiEndpointKey"] == "local"


def test_api_control_updates_paused_state_when_token_unset(monkeypatch):
    monkeypatch.setattr(dashboard_server, "DASHBOARD_TOKEN", "")

    def update_state_locked(updater):
        return updater({"paused": False})

    monkeypatch.setattr(dashboard_server, "update_state_locked", update_state_locked)
    server = _start_server()
    try:
        status, headers, body = _request(server, "/api/control", method="POST", body={"paused": True})
    finally:
        server.shutdown()
        server.server_close()

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(body.decode("utf-8")) == {"ok": True, "paused": True}


def test_post_mutation_routes_return_403_when_token_missing(monkeypatch):
    monkeypatch.setattr(dashboard_server, "DASHBOARD_TOKEN", "secret")
    server = _start_server()
    try:
        for path in ("/api/control", "/api/config"):
            try:
                _request(server, path, method="POST", body={})
            except urllib.error.HTTPError as exc:
                assert exc.code == 403
                assert exc.read() == b"Forbidden"
            else:
                raise AssertionError(f"expected 403 for {path}")
    finally:
        server.shutdown()
        server.server_close()


def test_api_config_applies_patch_and_calls_sidecar_sync(monkeypatch):
    monkeypatch.setattr(dashboard_server, "DASHBOARD_TOKEN", "secret")
    sidecar_calls = []
    monkeypatch.setattr(
        dashboard_server,
        "_sync_ai_sidecar_for_state",
        lambda state, patch: sidecar_calls.append((state, patch)),
    )

    def update_state_locked(updater):
        return updater({"gridMode": "scalpy", "aiEnabled": False})

    monkeypatch.setattr(dashboard_server, "update_state_locked", update_state_locked)
    server = _start_server()
    try:
        status, headers, body = _request(
            server,
            "/api/config",
            method="POST",
            body={"gridMode": "fatty", "gridLevels": "12"},
            headers={"X-Tradebot-Token": "secret"},
        )
    finally:
        server.shutdown()
        server.server_close()

    payload = json.loads(body.decode("utf-8"))
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["ok"] is True
    assert payload["state"]["gridMode"] == "fatty"
    assert payload["state"]["gridLevels"] == 12
    assert sidecar_calls == [(payload["state"], {"gridMode": "fatty", "gridLevels": 12})]


def test_api_config_bad_payload_returns_current_500_error_contract(monkeypatch):
    monkeypatch.setattr(dashboard_server, "DASHBOARD_TOKEN", "")
    server = _start_server()
    try:
        try:
            _request(server, "/api/config", method="POST", body={"gridMode": "chaos"})
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 500
            assert "gridMode must be one of" in body["error"]
        else:
            raise AssertionError("expected 500")
    finally:
        server.shutdown()
        server.server_close()
