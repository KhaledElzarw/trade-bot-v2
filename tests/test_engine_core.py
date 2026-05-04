import json
import inspect

import pytest

import engine


def test_build_grid_orders_sorted_by_distance():
    orders = engine._build_grid_orders(anchor=100.0, spacing_pct=0.01, levels=2, qty_per_level=0.1)

    assert [o.side for o in orders] == ["BUY", "BUY", "SELL", "SELL"]
    assert [round(o.price, 4) for o in orders] == [99.0, 98.01, 101.0, 102.01]


def test_fill_order_paper_buy_accounts_for_fee_and_slippage():
    paper = engine.PaperAccount(usdt=100.0, btc=0.0)
    grid = engine.GridState(
        anchor=100.0,
        spacing_pct=0.01,
        levels=1,
        max_exposure_pct=1.0,
        reserved_usdt=0.0,
        reserved_btc=0.0,
        cost_basis_usdt=0.0,
        orders=[],
        active=True,
    )
    order = engine.GridOrder(side="BUY", price=100.0, qty_btc=0.5)

    event = engine._fill_order_paper(paper, grid, order, fill_price=100.0, fee_bps=10, slip_bps=10)

    assert event is not None
    assert event["event"] == "ENTER"
    assert round(event["price"], 4) == 100.1
    assert event["feeUsdt"] == pytest.approx(0.05005)
    assert paper.usdt == pytest.approx(49.89995)
    assert round(paper.btc, 6) == 0.5
    assert grid.cost_basis_usdt == pytest.approx(50.10005)


def test_fill_order_paper_sell_allocates_cost_basis():
    paper = engine.PaperAccount(usdt=0.0, btc=1.0)
    grid = engine.GridState(
        anchor=100.0,
        spacing_pct=0.01,
        levels=1,
        max_exposure_pct=1.0,
        reserved_usdt=0.0,
        reserved_btc=1.0,
        cost_basis_usdt=80.0,
        orders=[],
        active=True,
    )
    order = engine.GridOrder(side="SELL", price=100.0, qty_btc=0.25)

    event = engine._fill_order_paper(paper, grid, order, fill_price=100.0, fee_bps=10)

    assert event is not None
    assert event["event"] == "EXIT"
    assert round(event["grossRealizedPnlUsdt"], 4) == 5.0
    assert round(event["realizedPnlUsdt"], 4) == 4.975
    assert round(paper.btc, 6) == 0.75
    assert round(grid.cost_basis_usdt, 4) == 60.0


def test_reconcile_accounting_from_trade_log(tmp_path, monkeypatch):
    trades = tmp_path / "trades.jsonl"
    rows = [
        {"event": "ENGINE_START", "tsUtc": "2026-01-01T00:00:00+00:00"},
        {"event": "ENTER", "qtyBtc": 1.0, "notionalUsdt": 100.0, "feeUsdt": 1.0},
        {
            "event": "EXIT",
            "qtyBtc": 0.5,
            "notionalUsdt": 70.0,
            "feeUsdt": 0.7,
            "grossRealizedPnlUsdt": 19.5,
            "realizedPnlUsdt": 18.8,
        },
    ]
    trades.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    monkeypatch.setattr(engine, "TRADES_PATH", str(trades))

    result = engine._reconcile_accounting_from_trade_log(start_usdt=500.0, start_btc=0.0)

    assert round(result["paper"]["usdt"], 4) == 468.3
    assert round(result["paper"]["btc"], 4) == 0.5
    assert round(result["openCostBasisUsdt"], 4) == 50.5
    assert result["cumulative"]["trades"] == 1
    assert result["cumulative"]["wins"] == 1
    assert round(result["cumulative"]["feesPaidUsdt"], 4) == 1.7


def test_snapshot_gate_throttles_identical_payloads_until_refresh_interval():
    gate = engine._SnapshotChangeGate(min_interval_seconds=10, max_interval_seconds=60, market_change_bps=5)

    first = gate.evaluate(
        critical_signature="accounting-a",
        market_signature="candle-a",
        market_values={"price": 100.0},
        now_monotonic=0.0,
    )
    assert first.should_persist is True
    assert first.reason == "initial"
    gate.record(first)

    throttled = gate.evaluate(
        critical_signature="accounting-a",
        market_signature="candle-a",
        market_values={"price": 100.0},
        now_monotonic=1.0,
    )
    assert throttled.should_persist is False
    assert throttled.reason == "throttled"

    refreshed = gate.evaluate(
        critical_signature="accounting-a",
        market_signature="candle-a",
        market_values={"price": 100.0},
        now_monotonic=60.0,
    )
    assert refreshed.should_persist is True
    assert refreshed.reason == "refresh_interval"


def test_snapshot_gate_persists_critical_changes_immediately_and_market_after_min_interval():
    gate = engine._SnapshotChangeGate(min_interval_seconds=10, max_interval_seconds=60, market_change_bps=5)
    first = gate.evaluate(
        critical_signature="accounting-a",
        market_signature="candle-a",
        market_values={"price": 100.0},
        now_monotonic=0.0,
    )
    gate.record(first)

    critical_change = gate.evaluate(
        critical_signature="accounting-b",
        market_signature="candle-a",
        market_values={"price": 100.0},
        now_monotonic=1.0,
    )
    assert critical_change.should_persist is True
    assert critical_change.reason == "state_changed"

    market_too_soon = gate.evaluate(
        critical_signature="accounting-a",
        market_signature="candle-a",
        market_values={"price": 100.10},
        now_monotonic=5.0,
    )
    assert market_too_soon.should_persist is False

    market_after_min = gate.evaluate(
        critical_signature="accounting-a",
        market_signature="candle-a",
        market_values={"price": 100.10},
        now_monotonic=10.0,
    )
    assert market_after_min.should_persist is True
    assert market_after_min.reason == "market_moved"


def test_runtime_snapshot_signature_ignores_saved_at_but_keeps_grid_changes():
    runtime = {
        "enginePid": 123,
        "paper": {"usdt": 100.0, "btc": 0.1},
        "stats": {"trades": 1, "pnl_usdt": 2.0},
        "market": {"price": 100.0, "candle": {"openTimeMs": 1, "closeTimeMs": 2}},
        "grid": {"active": True, "orders": [{"side": "BUY", "price": 99.0, "qty_btc": 0.01}]},
        "ai": {"enabled": True, "riskAction": "allow_grid"},
        "savedAt": "2026-01-01T00:00:00+00:00",
    }
    changed_timestamp = {**runtime, "savedAt": "2026-01-01T00:00:01+00:00"}
    changed_grid = {
        **runtime,
        "grid": {"active": True, "orders": [{"side": "SELL", "price": 101.0, "qty_btc": 0.01}]},
    }

    assert (
        engine._runtime_snapshot_change_inputs(runtime)[0]
        == engine._runtime_snapshot_change_inputs(changed_timestamp)[0]
    )
    assert engine._runtime_snapshot_change_inputs(runtime)[0] != engine._runtime_snapshot_change_inputs(changed_grid)[0]


def test_heartbeat_log_gate_can_be_quiet_without_affecting_runtime_loop():
    assert engine._should_emit_heartbeat_log(None, 0.0, now_monotonic=0.0) is False
    assert engine._should_emit_heartbeat_log(None, 60.0, now_monotonic=0.0) is True
    assert engine._should_emit_heartbeat_log(0.0, 60.0, now_monotonic=30.0) is False
    assert engine._should_emit_heartbeat_log(0.0, 60.0, now_monotonic=60.0) is True


def test_engine_runtime_no_longer_constructs_baserow_sync():
    source = inspect.getsource(engine)

    assert "BaserowSync" not in source
    assert "sync_event(" not in source
    assert "sync_tick(" not in source
