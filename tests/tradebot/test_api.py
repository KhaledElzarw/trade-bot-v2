"""API schema + security tests (Phase 11). Closes A12/A13/A09 surface."""

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from tradebot.api.app import create_app
from tradebot.api.security import (
    ApiSettings,
    InsecureBindError,
    origin_allowed,
    token_matches,
    validate_startup,
)
from tradebot.api.views import InMemoryPortfolioView
from tradebot.application.portfolio import seed_portfolio
from tradebot.strategies.builtin import BUILTIN_STRATEGIES

NOW = dt.datetime(2026, 7, 17)
TOKEN = "x" * 40


def make_view():
    names = [c().metadata().name for c in BUILTIN_STRATEGIES]
    portfolio = seed_portfolio(names, now=NOW, id_factory=lambda h: f"w-{h}")
    return InMemoryPortfolioView(portfolio=portfolio,
                                 mark_price=Decimal("60000"), now=NOW)


def client(**kw):
    settings = ApiSettings(**{"host": "127.0.0.1", "auth_token": TOKEN, **kw})
    return TestClient(create_app(make_view(), settings))


# ---- bind security ----------------------------------------------------------

def test_remote_bind_without_token_refuses_startup():
    with pytest.raises(InsecureBindError, match="requires TRADEBOT_API_TOKEN"):
        validate_startup(ApiSettings(host="0.0.0.0", auth_token=None))


def test_remote_bind_with_weak_token_refuses_startup():
    with pytest.raises(InsecureBindError, match="at least 32"):
        validate_startup(ApiSettings(host="0.0.0.0", auth_token="short"))


def test_remote_bind_with_strong_token_starts():
    validate_startup(ApiSettings(host="0.0.0.0", auth_token=TOKEN))


def test_loopback_default_needs_no_token():
    validate_startup(ApiSettings())
    assert ApiSettings().is_loopback is True
    assert ApiSettings(host="0.0.0.0").is_loopback is False
    assert ApiSettings(host="::1").is_loopback is True
    assert ApiSettings(host="not-an-ip").is_loopback is False


# ---- A12: mutations fail closed --------------------------------------------

def test_mutation_without_token_is_denied():
    r = client().post("/api/v2/reports/daily/refresh", json={"date": "2026-07-16"})
    assert r.status_code == 401


def test_mutation_with_wrong_token_is_denied():
    r = client().post("/api/v2/reports/daily/refresh", json={"date": "2026-07-16"},
                      headers={"Authorization": "Bearer wrong" + "y" * 35})
    assert r.status_code == 401


def test_mutation_with_valid_token_succeeds():
    r = client().post("/api/v2/reports/daily/refresh", json={"date": "2026-07-16"},
                      headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert r.json()["queued"] is True


def test_token_in_query_string_rejected():
    r = client().post(f"/api/v2/reports/daily/refresh?token={TOKEN}",
                      json={"date": "2026-07-16"},
                      headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 400
    assert "query" in r.json()["detail"]


def test_empty_control_payload_rejected():
    r = client().post("/api/v2/reports/daily/refresh", json={},
                      headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 422


def test_cross_origin_mutation_rejected():
    r = client().post("/api/v2/reports/daily/refresh", json={"date": "2026-07-16"},
                      headers={"Authorization": f"Bearer {TOKEN}",
                               "Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_token_compare_fails_closed_on_missing_values():
    assert token_matches(None, "x") is False
    assert token_matches("x", None) is False
    assert token_matches(None, None) is False
    assert token_matches("abc", "abc") is True


def test_origin_allowed_rules():
    s = ApiSettings(port=8787)
    assert origin_allowed(None, s) is True  # curl / same-origin
    assert origin_allowed("http://localhost:8787", s) is True
    assert origin_allowed("https://evil.example.com", s) is False


# ---- A13: no raw exception disclosure --------------------------------------

def test_unhandled_error_is_redacted_with_correlation_id():
    class ExplodingView(InMemoryPortfolioView):
        def readiness(self):
            raise RuntimeError("secret db path /srv/prod/tradebot.db")

    base = make_view()
    view = ExplodingView(portfolio=base.portfolio, mark_price=base.mark_price,
                         now=base.now)
    app = create_app(view, ApiSettings(auth_token=TOKEN))
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/v2/system/readiness")
    assert r.status_code == 500
    body = r.json()
    assert body["error"] == "request could not be completed"
    assert "secret db path" not in r.text
    assert "RuntimeError" not in r.text
    assert len(body["correlation_id"]) == 16


# ---- security headers -------------------------------------------------------

def test_security_headers_present():
    r = client().get("/api/v2/system/health")
    h = r.headers
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "no-referrer"
    assert h["x-frame-options"] == "DENY"
    assert h["cache-control"] == "no-store"


def test_oversized_body_rejected():
    r = client(max_body_bytes=10).post(
        "/api/v2/reports/daily/refresh", json={"date": "x" * 500},
        headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 413


# ---- A09: no model endpoint editing ----------------------------------------

def test_llm_status_is_read_only_and_has_no_editing_route():
    c = client()
    r = c.get("/api/v2/llm/status")
    assert r.status_code == 200
    assert r.json()["provider"] == "llama_cpp"
    # There is no route to change the endpoint.
    for method, path in (("post", "/api/v2/llm/config"),
                         ("put", "/api/v2/llm/status"),
                         ("post", "/api/v2/data-sources/allowlist")):
        assert getattr(c, method)(path, json={"aiBaseUrl": "http://evil"}
                                  ).status_code in (404, 405)


# ---- read routes ------------------------------------------------------------

def test_health_and_readiness():
    c = client()
    assert c.get("/api/v2/system/health").json() == {"status": "ok"}
    assert c.get("/api/v2/system/readiness").json()["ready"] is True


def test_portfolio_summary_separates_active_and_shadow():
    body = client().get("/api/v2/portfolio/summary").json()
    assert body["active"]["starting_capital"] == "140000.00"
    assert body["active"]["current_equity"] == "140000.00"
    assert body["shadow"]["virtual_equity"] == "120000.00"
    # Shadow is a separate section; it is never folded into the active total.
    assert body["active"]["current_equity"] != body["shadow"]["virtual_equity"]
    assert body["dark_horse"]["display_name"] == "Dark Horse"
    assert body["dark_horse_daily"]["display_name"] == "Darkhorse - Daily"


def test_wallet_filters():
    c = client()
    assert len(c.get("/api/v2/wallets").json()["wallets"]) == 26
    assert len(c.get("/api/v2/wallets?kind=active").json()["wallets"]) == 12
    assert len(c.get("/api/v2/wallets?kind=shadow").json()["wallets"]) == 12
    dh = c.get("/api/v2/wallets?kind=dark_horse").json()["wallets"]
    assert len(dh) == 1 and dh[0]["display_name"] == "Dark Horse"
    dhd = c.get("/api/v2/wallets?kind=dark_horse_daily").json()["wallets"]
    assert len(dhd) == 1 and dhd[0]["display_name"] == "Darkhorse - Daily"


def test_wallet_detail_and_404():
    c = client()
    wallets = c.get("/api/v2/wallets?kind=active").json()["wallets"]
    wid = wallets[0]["wallet_id"]
    assert c.get(f"/api/v2/wallets/{wid}").json()["wallet_id"] == wid
    assert c.get("/api/v2/wallets/nope").status_code == 404


def test_portfolio_insights_shape_and_route():
    view = make_view()
    ins = view.portfolio_insights()
    assert set(ins) >= {
        "net_pnl", "realized_pnl", "unrealized_pnl", "top_performer",
        "worst_performer", "wallets_in_profit", "total_fills", "total_fees",
        "btc_exposure", "open_orders", "most_active", "dark_horse",
        "dark_horse_daily"}
    # Real book = 12 active + Dark Horse + Darkhorse - Daily.
    assert ins["wallets_in_profit"]["total"] == 14
    oo = ins["open_orders"]
    assert oo["total"] == oo["buys"] + oo["sells"]
    assert "%" in ins["btc_exposure"]["pct_in_btc"]
    assert client().get("/api/v2/portfolio/insights").status_code == 200


def test_insights_count_fills_in_trailing_24h_by_side():
    view = make_view()
    wid = view.portfolio.active[0].wallet.wallet_id
    recent = (NOW - dt.timedelta(hours=2)).isoformat() + "Z"
    old = (NOW - dt.timedelta(hours=30)).isoformat() + "Z"
    view.trades_by_wallet[wid] = [
        {"status": "filled", "side": "BUY", "filled_at": recent},
        {"status": "filled", "side": "BUY", "filled_at": recent},
        {"status": "filled", "side": "SELL", "filled_at": recent},
        {"status": "filled", "side": "SELL", "filled_at": old},   # outside 24h
        {"status": "rejected", "side": "BUY", "filled_at": None},  # never filled
        {"status": "expired", "side": "SELL", "filled_at": None},
    ]
    day = view.portfolio_insights()["fills_24h"]
    assert day == {"total": 3, "buys": 2, "sells": 1}


def test_wallet_drilldown_exposes_orders_insights_and_description():
    view = make_view()
    wid = view.portfolio.active[0].wallet.wallet_id
    svid = view.portfolio.active[0].strategy_version_id
    view.strategy_descriptions[svid] = "Trades the edge on pullbacks."
    view.trades_by_wallet[wid] = [
        {"order_id": "o1", "side": "BUY", "status": "filled",
         "realized_pnl": None, "placed_at": "2026-07-20T00:00:00Z"},
        {"order_id": "o2", "side": "SELL", "status": "filled",
         "realized_pnl": "12.50", "placed_at": "2026-07-20T01:00:00Z"},
        {"order_id": "o3", "side": "SELL", "status": "rejected",
         "realized_pnl": None, "placed_at": "2026-07-20T02:00:00Z"},
    ]
    detail = view.wallet(wid)
    assert detail["strategy_description"] == "Trades the edge on pullbacks."
    ins = detail["insights"]
    assert ins["trade_count"] == 2  # rejected order excluded from fills
    assert ins["buy_count"] == 1 and ins["sell_count"] == 1
    assert ins["win_count"] == 1 and ins["win_rate"] == "100.0%"
    # Order history is newest-first and includes the rejected attempt.
    orders = view.wallet_orders(wid)
    assert [o["order_id"] for o in orders] == ["o3", "o2", "o1"]
    # Fills exclude rejects; open orders default empty.
    assert {o["order_id"] for o in view.wallet_fills(wid)} == {"o1", "o2"}
    assert view.wallet_open_orders(wid) == []


def test_wallet_naming_rule_in_api():
    w = client().get("/api/v2/wallets?kind=active").json()["wallets"][0]
    assert w["display_name"] == f"{w['strategy_name']}_0"
    assert w["days_since_assignment_changed"] == 0


def test_all_read_routes_respond():
    c = client()
    wid = c.get("/api/v2/wallets?kind=active").json()["wallets"][0]["wallet_id"]
    for path in (
        "/api/v2/portfolio/summary", "/api/v2/wallets",
        f"/api/v2/wallets/{wid}/equity", f"/api/v2/wallets/{wid}/orders",
        f"/api/v2/wallets/{wid}/fills", f"/api/v2/wallets/{wid}/ledger",
        "/api/v2/strategies", "/api/v2/lineage", "/api/v2/evaluations",
        "/api/v2/promotions", "/api/v2/reports/daily", "/api/v2/reports/weekly",
        "/api/v2/quarantines", "/api/v2/data-sources/status", "/api/v2/llm/status",
    ):
        assert c.get(path).status_code == 200, path


def test_strategy_detail_and_404():
    c = client()
    sid = c.get("/api/v2/strategies").json()["strategies"][0]["strategy_version_id"]
    assert c.get(f"/api/v2/strategies/{sid}").status_code == 200
    assert c.get("/api/v2/strategies/nope").status_code == 404


def test_view_filters_and_empty_paths():
    view = make_view()
    view.daily_reports = [{"date": "2026-07-16"}, {"date": "2026-07-15"}]
    view.weekly_reports = [{"evaluation_window": "2026-W29"}]
    view.evaluation_records = [{"evaluation_window": "2026-W29", "wallet_id": "a"}]
    assert len(view.reports_daily("2026-07-16")) == 1
    assert len(view.reports_daily()) == 2
    assert len(view.reports_weekly("2026-W29")) == 1
    assert len(view.reports_weekly()) == 1
    assert len(view.evaluations("2026-W29")) == 1
    assert len(view.evaluations()) == 1
    assert view.wallet_equity("missing") == []
    assert view.wallet_orders("x") == [] and view.wallet_fills("x") == []
    assert view.wallet_ledger("x") == [] and view.promotions() == []
    assert view.quarantines() == [] and view.lineage() == []
    assert view.data_sources() == []


def test_degraded_llm_status_is_truthful():
    view = make_view()
    view.llm_healthy = False
    c = TestClient(create_app(view, ApiSettings(auth_token=TOKEN)))
    assert c.get("/api/v2/llm/status").json()["status"] == "degraded"
    assert c.get("/api/v2/system/readiness").json()["local_model"] == "degraded"


def test_portfolio_without_dark_horse_reports_none():
    view = make_view()
    view.portfolio.dark_horse = None
    body = TestClient(create_app(view, ApiSettings(auth_token=TOKEN))).get(
        "/api/v2/portfolio/summary").json()
    assert body["dark_horse"] is None


# ---- Phase-13 verifier regressions -----------------------------------------

def test_oversized_body_rejected_without_content_length():
    """A chunked/streamed request omits Content-Length; the cap must still
    apply (the guard used to only run `if content-length` -> bypassable)."""

    settings = ApiSettings(host="127.0.0.1", auth_token=TOKEN, max_body_bytes=64)
    c = TestClient(create_app(make_view(), settings))

    def chunked():
        yield b"x" * 5000

    r = c.post("/api/v2/reports/daily/refresh", content=chunked(),
               headers={"Authorization": f"Bearer {TOKEN}",
                        "Content-Type": "application/json"})
    assert r.status_code == 413
    assert "Content-Length" not in r.request.headers


def test_413_response_carries_security_headers():
    """Early returns used to skip the header middleware entirely."""

    r = client(max_body_bytes=10).post(
        "/api/v2/reports/daily/refresh", json={"date": "x" * 500},
        headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 413
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_malformed_content_length_rejected():
    c = client(max_body_bytes=64)
    r = c.post("/api/v2/reports/daily/refresh",
               content=b'{"date": "2026-07-16"}',
               headers={"Authorization": f"Bearer {TOKEN}",
                        "Content-Type": "application/json",
                        "Content-Length": "not-a-number"})
    assert r.status_code in (400, 413)


def test_normal_sized_body_still_passes():
    r = client().post("/api/v2/reports/daily/refresh", json={"date": "2026-07-16"},
                      headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


# ---- live-loop + awareness read models --------------------------------------

def test_live_status_route_empty_in_static_mode():
    r = client().get("/api/v2/system/live")
    assert r.status_code == 200
    assert r.json() == {"live": {}}


def test_awareness_route_reflects_published_block():
    view = make_view()
    view.live_status = {"mode": "live", "last_tick_ms": 123, "caught_up": True,
                        "total_fills": 7}
    view.awareness = {"status": "ok", "summary": "calm", "domains": []}
    c = TestClient(create_app(view, ApiSettings(auth_token=TOKEN)))
    assert c.get("/api/v2/system/live").json()["live"]["total_fills"] == 7
    assert c.get("/api/v2/system/awareness").json()["awareness"]["summary"] == "calm"
