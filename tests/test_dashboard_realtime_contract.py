import base64
import json
import socket
import threading
from http.server import ThreadingHTTPServer

import dashboard_server
from dashboard_contracts import validate_market_payload


def _fake_read_json(path):
    if path == dashboard_server.STATUS_PATH:
        return {
            "symbol": "BTCUSDT",
            "interval": "1s",
            "price": 100.0,
            "tsUtc": "2026-05-02T00:00:00+00:00",
            "stats": {"grid": {}},
        }
    if path == dashboard_server.STATE_PATH:
        return {"symbol": "BTCUSDT", "interval": "1s", "aiEnabled": False}
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


def _start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_market_payload_matches_explicit_contract(monkeypatch):
    monkeypatch.setattr(dashboard_server, "read_json", _fake_read_json)
    monkeypatch.setattr(dashboard_server, "get_ohlcv", _fake_ohlcv)
    monkeypatch.setattr(
        dashboard_server,
        "read_events",
        lambda: [{"tsUtc": "2026-05-02T00:00:00+00:00", "event": "ENTER", "price": 100.0}],
    )
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.1)

    payload = dashboard_server.build_market_payload({"interval": ["1s"], "ohlcv": ["0"]})
    validated = validate_market_payload(payload)

    assert validated["schemaVersion"] == "dashboard.snapshot.v1"
    assert validated["channel"] == "status"
    assert validated["seq"] > 0
    assert validated["ohlcv"] == []
    assert validated["events"][0]["event"] == "ENTER"


def test_realtime_server_sse_and_polling_fallback(monkeypatch):
    monkeypatch.setattr(dashboard_server, "read_json", _fake_read_json)
    monkeypatch.setattr(dashboard_server, "get_ohlcv", _fake_ohlcv)
    monkeypatch.setattr(dashboard_server, "read_events", lambda: [])
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.1)
    server = _start_server()
    host, port = server.server_address
    try:
        import urllib.request

        with urllib.request.urlopen(f"http://{host}:{port}/api/market?interval=1s&ohlcv=0", timeout=2) as response:
            polled = json.loads(response.read().decode("utf-8"))
        assert polled["channel"] == "status"
        assert polled["ohlcv"] == []

        with urllib.request.urlopen(f"http://{host}:{port}/api/live/events?interval=1s&ohlcv=0", timeout=2) as response:
            lines = []
            while len(lines) < 2:
                lines.append(response.readline().decode("utf-8").strip())
        assert lines[0] == "event: market"
        assert lines[1].startswith("data: ")
        streamed = json.loads(lines[1][len("data: "):])
        assert streamed["channel"] == "status"
        assert streamed["seq"] > polled["seq"]
        assert streamed["ohlcv"] == []
        assert streamed["eventsPatch"]["mode"] == "snapshot"
        assert streamed["ordersPatch"]["mode"] == "snapshot"
    finally:
        server.shutdown()
        server.server_close()


def test_sse_stream_sends_event_and_order_deltas(monkeypatch):
    runtime_state = {"savedAt": "2026-05-02T00:00:00+00:00", "grid": {"orders": [
        {"side": "BUY", "price": 99.0, "qty_btc": 0.01, "total": 0.99, "type": "LIMIT"}
    ]}}
    events = [{"_eventId": 1, "tsUtc": "2026-05-02T00:00:00+00:00", "event": "ENTER", "price": 100.0}]

    def read_json(path):
        if path == dashboard_server.RUNTIME_PATH:
            return runtime_state
        return _fake_read_json(path)

    monkeypatch.setattr(dashboard_server, "read_json", read_json)
    monkeypatch.setattr(dashboard_server, "get_ohlcv", _fake_ohlcv)
    monkeypatch.setattr(dashboard_server, "read_events", lambda: list(events))
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.1)
    server = _start_server()
    host, port = server.server_address

    def read_frame(response):
        data = None
        while data is None:
            line = response.readline().decode("utf-8").strip()
            if line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        return data

    try:
        import urllib.request

        with urllib.request.urlopen(f"http://{host}:{port}/api/live/events?interval=1s&ohlcv=0", timeout=4) as response:
            first = read_frame(response)
            events.append({"_eventId": 2, "tsUtc": "2026-05-02T00:00:01+00:00", "event": "EXIT", "price": 101.0})
            runtime_state["grid"]["orders"] = [
                {"side": "SELL", "price": 102.0, "qty_btc": 0.01, "total": 1.02, "type": "LIMIT"}
            ]
            second = read_frame(response)

        assert first["eventsPatch"]["mode"] == "snapshot"
        assert [event["event"] for event in first["eventsPatch"]["items"]] == ["ENTER"]
        assert first["ordersPatch"]["mode"] == "snapshot"
        assert first["runtime"]["grid"].get("orders") is None
        assert second["eventsPatch"]["mode"] == "delta"
        assert [event["event"] for event in second["eventsPatch"]["items"]] == ["EXIT"]
        assert second["ordersPatch"]["mode"] == "delta"
        assert {op["op"] for op in second["ordersPatch"]["ops"]} == {"remove", "upsert"}
        assert second["events"] == []
        assert second["runtime"]["grid"].get("orders") is None
    finally:
        server.shutdown()
        server.server_close()


def test_chart_websocket_streams_chart_contract(monkeypatch):
    monkeypatch.setattr(dashboard_server, "read_json", _fake_read_json)
    monkeypatch.setattr(dashboard_server, "get_ohlcv", _fake_ohlcv)
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.1)
    server = _start_server()
    host, port = server.server_address
    try:
        key = base64.b64encode(b"tradebot-test-key").decode("ascii")
        with socket.create_connection((host, port), timeout=2) as sock:
            request = (
                "GET /ws/chart?interval=1s HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(request.encode("ascii"))
            headers = sock.recv(4096)
            assert b"101 Switching Protocols" in headers
            first = sock.recv(2)
            assert first[0] == 0x81
            length = first[1] & 0x7F
            if length == 126:
                length = int.from_bytes(sock.recv(2), "big")
            body = sock.recv(length)
        payload = json.loads(body.decode("utf-8"))
        assert payload["channel"] == "chart"
        assert payload["bar"]["close"] == 100.0
        assert payload["seq"] > 0
    finally:
        server.shutdown()
        server.server_close()
