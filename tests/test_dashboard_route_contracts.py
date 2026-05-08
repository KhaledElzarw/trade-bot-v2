import io
import json
import socket
import threading
import urllib.error
import urllib.parse
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


def _request(server, path, *, method="GET", body=None, headers=None, raw_body=None):
    data = raw_body
    if raw_body is None:
        data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        _url(server, path),
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return response.status, response.headers, response.read()


def _raw_http_request(server, request_text):
    host, port = server.server_address
    with socket.create_connection((host, port), timeout=2) as sock:
        sock.settimeout(2)
        sock.sendall(request_text.format(host=host, port=port).encode("ascii"))
        chunks = []
        while True:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


class _BrokenPipeWriter:
    def write(self, body):
        raise BrokenPipeError

    def flush(self):
        return None


class _DirectHandler:
    def __init__(self, wfile=None):
        self.headers = {}
        self.wfile = wfile or io.BytesIO()
        self.sent = []

    def send_response(self, *args):
        self.sent.append(("response", args))

    def send_header(self, *args):
        self.sent.append(("header", args))

    def end_headers(self):
        self.sent.append(("end",))


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
    assert '<script src="/static/dashboard.v1.js?v=24"></script>' in html
    assert 'href="/static/dashboard.v1.css?v=24"' in html
    assert "Server Time" in html
    assert "May 1 2026 UTC" not in html
    assert "BTCUSDT" in html
    required_ids = [
        "sticky-summary",
        "summary-equity",
        "summary-realized",
        "summary-unrealized",
        "summary-fees",
        "trading-state-label",
        "state-mode",
        "state-risk",
        "state-exposure",
        "state-action",
        "market-chart",
        "market-legend",
        "chart-price-pill",
        "hover-ohlcv",
        "news-stack",
        "news-first-btn",
        "news-prev-btn",
        "news-next-btn",
        "news-last-btn",
        "news-page-indicator",
        "signal-table",
        "completed-macro-card",
        "completed-macro-calendar",
        "completed-macro-month-filter",
        "completed-macro-year-filter",
        "completed-macro-event-filter",
        "completed-macro-first-btn",
        "completed-macro-prev-btn",
        "completed-macro-next-btn",
        "completed-macro-last-btn",
        "completed-macro-page-indicator",
        "upcoming-macro-card",
        "upcoming-macro-calendar",
        "upcoming-macro-month-filter",
        "upcoming-macro-year-filter",
        "upcoming-macro-event-filter",
        "upcoming-macro-first-btn",
        "upcoming-macro-prev-btn",
        "upcoming-macro-next-btn",
        "upcoming-macro-last-btn",
        "upcoming-macro-page-indicator",
        "config-open-btn",
        "config-modal",
        "config-modal-title",
        "config-close-btn",
        "config-form-grid",
        "config-save-btn",
        "ai-decisions-card",
        "ai-decisions-body",
        "ai-decisions-page-indicator",
        "ai-decisions-first-btn",
        "ai-decisions-prev-btn",
        "ai-decisions-next-btn",
        "ai-decisions-last-btn",
        "agent-select",
        "agent-thread-select",
        "agent-configure-btn",
        "agent-chat-messages",
        "agent-proposals",
        "agent-chat-input",
        "agent-chat-send-btn",
    ]
    for element_id in required_ids:
        assert f'id="{element_id}"' in html
    assert 'class="candle-details" id="hover-ohlcv"' in html


def test_static_route_serves_dashboard_js_asset(monkeypatch, tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    asset = static_dir / "dashboard.v1.js"
    asset.write_text("console.log('dashboard');\n", encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "STATIC_DIR", static_dir)

    server = _start_server()
    try:
        status, headers, body = _request(server, "/static/dashboard.v1.js?v=24")
    finally:
        server.shutdown()
        server.server_close()

    assert status == 200
    assert headers["Content-Type"].startswith("application/javascript")
    assert headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert headers["Pragma"] == "no-cache"
    assert headers["Expires"] == "0"
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


def test_health_and_unknown_get_routes_return_expected_responses():
    server = _start_server()
    try:
        status, headers, body = _request(server, "/health")
        assert status == 200
        assert headers["Content-Type"].startswith("application/json")
        assert body == b'{"status":"ok"}'

        try:
            _request(server, "/not-a-route")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            assert exc.read() == b"Not found"
        else:
            raise AssertionError("expected 404")
    finally:
        server.shutdown()
        server.server_close()


def test_get_route_exception_returns_current_500_json_contract(monkeypatch):
    def fail_read_json(path):
        raise RuntimeError("dashboard read failed")

    monkeypatch.setattr(dashboard_server, "read_json", fail_read_json)
    server = _start_server()
    try:
        try:
            _request(server, "/")
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 500
            assert body == {"error": "dashboard read failed"}
        else:
            raise AssertionError("expected 500")
    finally:
        server.shutdown()
        server.server_close()


def test_api_dashboard_route_returns_dashboard_json_contract(monkeypatch, tmp_path):
    _patch_dashboard_reads(monkeypatch)
    decisions_path = tmp_path / "ai_decisions.jsonl"
    decisions_path.write_text(
        json.dumps({
            "decision": {
                "decisionId": "route-decision",
                "tsUtc": "2026-05-02T00:00:00+00:00",
                "riskAction": "allow_grid",
            }
        })
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "AI_DECISIONS_PATH", decisions_path)
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
    assert payload["aiDecisions"][0]["decisionId"] == "route-decision"


def test_api_dashboard_defaults_bad_query_values_and_merges_ohlcv(monkeypatch):
    _patch_dashboard_reads(monkeypatch)
    ohlcv_calls = []
    merge_calls = []

    def fake_get_ohlcv(symbol, interval, limit, offset):
        ohlcv_calls.append((symbol, interval, limit, offset))
        return _fake_ohlcv()

    def fake_apply_status_price_to_ohlcv(rows, status, interval):
        merge_calls.append((status["symbol"], interval))
        merged = [dict(row) for row in rows]
        merged[-1]["close"] = 123.0
        return merged

    monkeypatch.setattr(dashboard_server, "get_ohlcv", fake_get_ohlcv)
    monkeypatch.setattr(
        dashboard_server,
        "apply_status_price_to_ohlcv",
        fake_apply_status_price_to_ohlcv,
    )
    server = _start_server()
    try:
        status, headers, body = _request(
            server,
            "/api/dashboard?interval=1s&limit=bad&offset=bad",
        )
    finally:
        server.shutdown()
        server.server_close()

    payload = json.loads(body.decode("utf-8"))
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert ohlcv_calls == [("BTCUSDT", "1s", 240, 0)]
    assert merge_calls == [("BTCUSDT", "1s")]
    assert payload["chartLimit"] == 240
    assert payload["chartOffset"] == 0
    assert payload["ohlcv"][-1]["close"] == 123.0


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


def test_api_control_treats_bad_json_body_as_empty_payload(monkeypatch):
    monkeypatch.setattr(dashboard_server, "DASHBOARD_TOKEN", "")

    def update_state_locked(updater):
        return updater({"paused": True})

    monkeypatch.setattr(dashboard_server, "update_state_locked", update_state_locked)
    server = _start_server()
    try:
        status, headers, body = _request(
            server,
            "/api/control",
            method="POST",
            raw_body=b"{not-json",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(body.decode("utf-8")) == {"ok": True, "paused": False}


def test_api_control_treats_bad_content_length_as_empty_payload(monkeypatch):
    monkeypatch.setattr(dashboard_server, "DASHBOARD_TOKEN", "")

    def update_state_locked(updater):
        return updater({"paused": True})

    monkeypatch.setattr(dashboard_server, "update_state_locked", update_state_locked)
    server = _start_server()
    try:
        response = _raw_http_request(
            server,
            "POST /api/control HTTP/1.1\r\n"
            "Host: {host}:{port}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: invalid\r\n"
            "Connection: close\r\n"
            "\r\n",
        )
    finally:
        server.shutdown()
        server.server_close()

    headers, _separator, body = response.partition(b"\r\n\r\n")
    assert headers.startswith(b"HTTP/1.0 200 OK")
    assert json.loads(body.decode("utf-8")) == {"ok": True, "paused": False}


def test_post_unknown_route_returns_404_when_authorized(monkeypatch):
    monkeypatch.setattr(dashboard_server, "DASHBOARD_TOKEN", "")
    server = _start_server()
    try:
        try:
            _request(server, "/api/not-found", method="POST", body={})
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            assert exc.read() == b"Not found"
        else:
            raise AssertionError("expected 404")
    finally:
        server.shutdown()
        server.server_close()


def test_chart_websocket_route_requires_key_header():
    server = _start_server()
    try:
        try:
            _request(server, "/ws/chart")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert exc.read() == b"Missing Sec-WebSocket-Key"
        else:
            raise AssertionError("expected 400")
    finally:
        server.shutdown()
        server.server_close()


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


def test_send_helpers_swallow_broken_pipe(monkeypatch, tmp_path):
    handler = _DirectHandler(_BrokenPipeWriter())

    dashboard_server.Handler._send(
        handler,
        200,
        b"body",
        "text/plain; charset=utf-8",
    )

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "dashboard.v1.css").write_text("body{}\n", encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "STATIC_DIR", static_dir)

    static_handler = _DirectHandler(_BrokenPipeWriter())
    dashboard_server.Handler._send_static(
        static_handler,
        urllib.parse.urlparse("/static/dashboard.v1.css"),
    )


def test_websocket_frame_helper_covers_short_and_large_lengths():
    small = _DirectHandler()
    dashboard_server.Handler._send_ws_frame(small, {"ok": True})
    small_frame = small.wfile.getvalue()
    small_body = json.dumps({"ok": True}, separators=(",", ":")).encode("utf-8")
    assert small_frame[:2] == bytes([0x81, len(small_body)])
    assert small_frame[2:] == small_body

    large = _DirectHandler()
    dashboard_server.Handler._send_ws_frame(large, {"blob": "x" * 66000})
    large_frame = large.wfile.getvalue()
    assert large_frame[:2] == b"\x81\x7f"
    assert int.from_bytes(large_frame[2:10], "big") == len(large_frame) - 10


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


def test_api_config_flexy_grid_mode_returns_current_500_error_contract(monkeypatch):
    monkeypatch.setattr(dashboard_server, "DASHBOARD_TOKEN", "")
    server = _start_server()
    try:
        try:
            _request(server, "/api/config", method="POST", body={"gridMode": "flexy"})
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 500
            assert "gridMode must be one of" in body["error"]
        else:
            raise AssertionError("expected 500")
    finally:
        server.shutdown()
        server.server_close()
