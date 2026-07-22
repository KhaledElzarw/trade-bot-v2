"""Tests for the wallet drill-down analytics: reconstructed time series,
per-strategy chart overlays, trade markers, and the strategy metric panel.

These cover the pieces the price/performance/exposure charts and the
strategy-aware panels are built from, plus the two new API routes.
"""

import datetime as dt
from decimal import Decimal

from fastapi.testclient import TestClient

from tradebot.api import chart_overlays, strategy_panel, timeseries
from tradebot.api.app import create_app
from tradebot.api.devserver import build_market
from tradebot.api.security import ApiSettings
from tradebot.api.views import InMemoryPortfolioView
from tradebot.application.portfolio import seed_portfolio
from tradebot.domain.market import MarketSnapshot
from tradebot.strategies.builtin import BUILTIN_STRATEGIES

NOW = dt.datetime(2026, 7, 17)


def _candles(n=6, step_ms=300_000, start=1_000_000_000_000):
    out = []
    price = Decimal("100")
    for i in range(n):
        close = price + Decimal(i)
        open_ms = start + i * step_ms
        out.append(MarketSnapshot(
            snapshot_id=f"c{i}", source="t", symbol="BTCUSDT", interval="5m",
            open_time_ms=open_ms, close_time_ms=open_ms + step_ms, is_closed=True,
            open=close, high=close + 1, low=close - 1, close=close,
            volume=Decimal("10"), retrieved_at_ms=open_ms + step_ms,
            source_time_ms=open_ms + step_ms))
    return tuple(out)


def _fill(candle, side, qty, price, *, fee="0.10", realized="0", status="filled"):
    iso = (dt.datetime.fromtimestamp(candle.close_time_ms / 1000, dt.timezone.utc)
           .replace(tzinfo=None).isoformat() + "Z")
    return {"filled_at": iso, "side": side, "filled_qty": qty, "price": price,
            "fee": fee, "realized_pnl": realized, "status": status,
            "reason": f"{side.lower()}_test"}


# -- timeseries reconstruction ------------------------------------------------

def test_series_flat_line_with_no_fills():
    candles = _candles()
    series = timeseries.build_series(candles, [])
    assert len(series) == len(candles)
    assert all(p["equity"] == "10000.00" for p in series)
    assert all(p["exposure_pct"] == "0.00" for p in series)
    assert series[0]["time"] == candles[0].close_time_ms // 1000


def test_series_reflects_a_buy_then_gains_exposure():
    candles = _candles()
    fills = [_fill(candles[1], "BUY", "10", "100", fee="1")]
    series = timeseries.build_series(candles, fills)
    # Before the buy: flat. After: cash reduced, BTC held, exposure > 0.
    assert series[0]["trade_count"] == 0
    assert series[2]["trade_count"] == 1
    assert Decimal(series[2]["btc_qty"]) == Decimal("10")
    assert Decimal(series[2]["exposure_pct"]) > 0
    # Equity ≈ cash (10000 - 1000 - 1 fee) + 10 * close.
    assert Decimal(series[2]["fees"]) == Decimal("1.00")


def test_empty_candles_yield_empty_series():
    assert timeseries.build_series((), [_fill(_candles()[0], "BUY", "1", "1")]) == []


def test_activity_stats_classifies_wins_and_losses():
    c = _candles()
    fills = [
        _fill(c[0], "BUY", "1", "100"),
        _fill(c[1], "SELL", "1", "110", realized="10"),   # win
        _fill(c[2], "SELL", "1", "90", realized="-5"),    # loss
    ]
    stats = timeseries.activity_stats(fills)
    assert stats["buy_count"] == 1 and stats["sell_count"] == 2
    assert stats["win_count"] == 1 and stats["loss_count"] == 1
    assert stats["win_rate"] == "50.0%"
    assert stats["avg_win"] == "10.00" and stats["avg_loss"] == "5.00"
    assert stats["profit_factor"] == "2.00"


def test_reason_breakdown_counts_only_fills():
    c = _candles()
    fills = [_fill(c[0], "BUY", "1", "1"), _fill(c[1], "BUY", "1", "1"),
             _fill(c[2], "SELL", "1", "1", status="rejected")]
    br = timeseries.reason_breakdown(fills)
    assert {"reason": "buy_test", "count": 2} in br
    assert all(r["reason"] != "sell_test" for r in br)  # rejected excluded


# -- chart overlays + markers -------------------------------------------------

def test_grid_overlays_live_on_price_pane():
    candles = build_market()  # long enough to warm indicators
    ov = chart_overlays.overlays_for("VolAdaptiveGrid", candles)
    assert ov, "grid should emit SMA regime overlays"
    assert all(o["pane"] == "price" for o in ov)
    labels = {o["label"] for o in ov}
    assert any("SMA" in label for label in labels)


def test_oscillator_overlays_use_a_lower_pane_with_thresholds():
    candles = build_market()
    ov = chart_overlays.overlays_for("OscillatorExhaustion", candles)
    panes = {o["pane"] for o in ov}
    assert "lower" in panes
    kinds = {o["kind"] for o in ov}
    assert "line" in kinds and "threshold" in kinds


def test_unknown_strategy_has_no_overlays():
    assert chart_overlays.overlays_for("DarkHorse", _candles()) == []
    assert chart_overlays.overlays_for("VolAdaptiveGrid", ()) == []


def test_markers_classify_buy_open_and_sell_win_loss():
    c = _candles()
    fills = [
        _fill(c[0], "BUY", "1", "100"),
        _fill(c[1], "SELL", "1", "110", realized="10"),
        _fill(c[2], "SELL", "1", "90", realized="-5"),
        _fill(c[3], "SELL", "1", "90", realized="0"),
    ]
    markers = chart_overlays.markers_from_fills(fills)
    results = [m["result"] for m in markers]
    assert results == ["open", "win", "loss", "flat"]
    assert markers == sorted(markers, key=lambda m: m["time"])


def test_ladder_lines_from_open_orders_and_avg_cost():
    orders = [{"order_id": "o1", "side": "BUY", "limit_price": "95"},
              {"order_id": "o2", "side": "SELL", "limit_price": "115"}]
    lines = chart_overlays.ladder_lines(orders, "100")
    assert all(line["kind"] == "threshold" for line in lines)
    assert any(line["id"] == "avg_cost" for line in lines)
    assert {line["value"] for line in lines} >= {95.0, 115.0, 100.0}


# -- strategy panel -----------------------------------------------------------

def test_grid_panel_has_params_and_live_blocks():
    candles = build_market()
    blocks = strategy_panel.strategy_metrics(
        "VolAdaptiveGrid", candles, base_qty=Decimal("0.1"),
        avg_cost=Decimal("60000"), mark_price=Decimal("61000"),
        quote_cash=Decimal("5000"))
    titles = {b["title"] for b in blocks}
    assert "Grid parameters" in titles and "Grid — now" in titles


def test_param_only_strategy_panel():
    blocks = strategy_panel.strategy_metrics(
        "EmaPullback", (), base_qty=Decimal("0"), avg_cost=Decimal("0"),
        mark_price=Decimal("60000"), quote_cash=Decimal("10000"))
    assert blocks and blocks[0]["title"] == "Parameters"
    labels = {r["label"] for r in blocks[0]["rows"]}
    assert "Fast EMA" in labels and "Slow EMA" in labels


def test_permanent_wallet_panel():
    blocks = strategy_panel.strategy_metrics(
        "DarkHorse", (), base_qty=Decimal("0"), avg_cost=Decimal("0"),
        mark_price=Decimal("60000"), quote_cash=Decimal("10000"))
    assert blocks and "Committee" in blocks[0]["title"]


# -- API routes ---------------------------------------------------------------

def _client_with_candles():
    names = [c().metadata().name for c in BUILTIN_STRATEGIES]
    portfolio = seed_portfolio(names, now=NOW, id_factory=lambda h: f"w-{h}")
    view = InMemoryPortfolioView(portfolio=portfolio, mark_price=Decimal("60000"),
                                 now=NOW, candles=build_market())
    settings = ApiSettings(host="127.0.0.1", auth_token="x" * 40)
    wid = view.wallets()[0]["wallet_id"]
    return TestClient(create_app(view, settings)), wid


def test_chart_endpoint_shape():
    client, wid = _client_with_candles()
    r = client.get(f"/api/v2/wallets/{wid}/chart")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"candles", "overlays", "markers"}
    assert body["candles"] and set(body["candles"][0]) == {
        "time", "open", "high", "low", "close"}


def test_timeseries_endpoint_shape():
    client, wid = _client_with_candles()
    r = client.get(f"/api/v2/wallets/{wid}/timeseries")
    assert r.status_code == 200
    points = r.json()["points"]
    assert points
    assert set(points[0]) >= {"time", "equity", "realized_pnl", "unrealized_pnl",
                              "fees", "exposure_pct", "btc_qty", "trade_count"}


def test_wallet_detail_carries_strategy_metrics_and_activity():
    client, wid = _client_with_candles()
    body = client.get(f"/api/v2/wallets/{wid}").json()
    assert "strategy_metrics" in body and "activity" in body
    assert "reason_breakdown" in body


def test_unknown_wallet_chart_is_empty_not_error():
    client, _ = _client_with_candles()
    r = client.get("/api/v2/wallets/does-not-exist/chart")
    assert r.status_code == 200
    assert r.json() == {"candles": [], "overlays": [], "markers": []}
