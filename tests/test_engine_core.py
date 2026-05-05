import json
from datetime import datetime, timezone

import pytest

import engine


def test_build_grid_orders_sorted_by_distance():
    orders = engine._build_grid_orders(anchor=100.0, spacing_pct=0.01, levels=2, qty_per_level=0.1)

    assert [o.side for o in orders] == ["BUY", "BUY", "SELL", "SELL"]
    assert [round(o.price, 4) for o in orders] == [99.0, 98.01, 101.0, 102.01]


def test_build_grid_orders_returns_empty_when_levels_are_zero():
    assert engine._build_grid_orders(anchor=100.0, spacing_pct=0.01, levels=0, qty_per_level=0.1) == []


def test_spacing_for_mode_accepts_scalpy():
    assert engine._spacing_for_mode(
        "scalpy",
        atr=1.0,
        price=1000.0,
        min_scalpy=0.003,
        min_fatty=0.01,
    ) == (pytest.approx(0.003), 14)
    assert engine._spacing_for_mode(
        "scalpy",
        atr=10.0,
        price=1000.0,
        min_scalpy=0.003,
        min_fatty=0.01,
    ) == (pytest.approx(0.008), 14)


def test_spacing_for_mode_accepts_fatty():
    assert engine._spacing_for_mode(
        "fatty",
        atr=10.0,
        price=1000.0,
        min_scalpy=0.003,
        min_fatty=0.01,
    ) == (pytest.approx(0.014), 8)


@pytest.mark.parametrize("mode", ["flexy", "chaos", "", None])
def test_spacing_for_mode_rejects_unsupported_modes(mode):
    with pytest.raises(ValueError):
        engine._spacing_for_mode(
            mode,
            atr=10.0,
            price=1000.0,
            min_scalpy=0.003,
            min_fatty=0.01,
        )


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


def test_env_required_numeric_and_date_helpers_cover_edge_paths(monkeypatch):
    monkeypatch.setenv("ENGINE_EDGE_FLOAT", "not-a-float")
    assert engine._env_float("ENGINE_EDGE_FLOAT", 1.25) == 1.25

    monkeypatch.delenv("ENGINE_REQUIRED_EDGE", raising=False)
    with pytest.raises(RuntimeError, match="Missing required env var: ENGINE_REQUIRED_EDGE"):
        engine._required("ENGINE_REQUIRED_EDGE")

    monkeypatch.setenv("ENGINE_REQUIRED_EDGE", "present")
    assert engine._required("ENGINE_REQUIRED_EDGE") == "present"

    assert engine._float_or_none(None) is None
    assert engine._float_or_none("12.5") == 12.5
    assert engine._float_or_none(object()) is None

    assert engine._relative_bps_moved(0.0, 0.0, 5) is False
    assert engine._relative_bps_moved(0.0, 1e-13, 5) is False
    assert engine._relative_bps_moved(0.0, 1e-11, 5) is True

    assert engine.PaperAccount(usdt=100.0, btc=0.25).equity(200.0) == 150.0
    assert engine._day_key(datetime(2026, 5, 5, 12, 30, tzinfo=timezone.utc)) == "2026-05-05"


def test_ema_and_atr_cover_success_and_insufficient_data():
    assert engine._ema([10.0, 12.0, 14.0], period=3) == pytest.approx(12.5)

    with pytest.raises(ValueError, match="Not enough values for EMA"):
        engine._ema([10.0], period=3)

    assert engine._atr(
        high=[11.0, 13.0, 15.0],
        low=[9.0, 10.0, 12.0],
        close=[10.0, 12.0, 14.0],
        period=2,
    ) == pytest.approx(3.0)

    with pytest.raises(ValueError, match="Not enough data for ATR"):
        engine._atr(high=[11.0, 13.0], low=[9.0, 10.0], close=[10.0, 12.0], period=2)


def test_resolve_grid_mode_uses_state_default_base_and_ai_override():
    assert engine._resolve_grid_mode({}, {}, ai_live=False) == "scalpy"
    assert engine._resolve_grid_mode({"gridMode": "fatty"}, {"recommendedMode": "scalpy"}, ai_live=False) == "fatty"
    assert engine._resolve_grid_mode({"gridMode": "fatty"}, {"recommendedMode": "scalpy"}, ai_live=True) == "scalpy"


def test_resolve_grid_mode_rejects_invalid_state_mode():
    with pytest.raises(ValueError, match="unsupported gridMode 'flexy'"):
        engine._resolve_grid_mode({"gridMode": "flexy"}, {}, ai_live=False)


def test_attach_ai_event_fields_preserves_empty_and_adds_known_fields():
    event = {"event": "ENTER"}

    assert engine._attach_ai_event_fields(event, {}) is event
    assert event == {"event": "ENTER"}

    enriched = engine._attach_ai_event_fields(
        {"event": "EXIT"},
        {
            "decisionId": "decision-1",
            "riskAction": "sells_only",
            "promptVersion": "prompt-v2",
            "confidence": 0.0,
            "model": "test-model",
        },
    )

    assert enriched == {
        "event": "EXIT",
        "aiDecisionId": "decision-1",
        "aiRiskAction": "sells_only",
        "aiPromptVersion": "prompt-v2",
        "aiConfidence": 0.0,
        "aiModel": "test-model",
    }


def test_fill_order_paper_returns_none_for_unfillable_edges():
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

    buy_paper = engine.PaperAccount(usdt=100.0, btc=0.0)
    assert (
        engine._fill_order_paper(
            buy_paper,
            grid,
            engine.GridOrder(side="BUY", price=100.0, qty_btc=0.0),
            fill_price=100.0,
            fee_bps=10,
        )
        is None
    )
    assert buy_paper.usdt == 100.0
    assert buy_paper.btc == 0.0

    sell_paper = engine.PaperAccount(usdt=0.0, btc=0.0)
    assert (
        engine._fill_order_paper(
            sell_paper,
            grid,
            engine.GridOrder(side="SELL", price=100.0, qty_btc=0.1),
            fill_price=100.0,
            fee_bps=10,
        )
        is None
    )
    assert sell_paper.usdt == 0.0
    assert sell_paper.btc == 0.0


def test_fill_order_paper_partial_buy_shrinks_to_cover_fee():
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

    event = engine._fill_order_paper(
        paper,
        grid,
        engine.GridOrder(side="BUY", price=100.0, qty_btc=2.0),
        fill_price=100.0,
        fee_bps=100,
    )

    assert event is not None
    assert event["event"] == "ENTER"
    assert event["qtyBtc"] == pytest.approx(100.0 / 101.0)
    assert event["notionalUsdt"] == pytest.approx(10000.0 / 101.0)
    assert event["feeUsdt"] == pytest.approx(100.0 / 101.0)
    assert paper.usdt == pytest.approx(0.0)
    assert paper.btc == pytest.approx(100.0 / 101.0)
    assert grid.cost_basis_usdt == pytest.approx(100.0)


def test_normalize_cumulative_derives_realized_and_coerces_counts():
    result = engine._normalize_cumulative(
        {
            "sinceUtc": "2026-01-01T00:00:00+00:00",
            "grossRealizedPnlUsdt": "10.5",
            "feesPaidUsdt": "0.5",
            "trades": "3",
            "wins": None,
            "losses": "1",
        }
    )

    assert result["realizedPnlUsdt"] == 10.0
    assert result["trades"] == 3
    assert result["wins"] == 0
    assert result["losses"] == 1


def test_grid_serialization_round_trips_trailing_fields():
    grid = engine.GridState(
        anchor=100.0,
        spacing_pct=0.01,
        levels=2,
        max_exposure_pct=0.35,
        reserved_usdt=50.0,
        reserved_btc=0.1,
        cost_basis_usdt=25.0,
        orders=[engine.GridOrder(side="BUY", price=99.0, qty_btc=0.01)],
        active=True,
        last_recenter_utc="2026-01-01T00:00:00+00:00",
    )
    grid.__dict__["trail_armed"] = True
    grid.__dict__["trail_stop"] = 95.5

    payload = engine._serialize_grid(grid)
    restored = engine._deserialize_grid(payload)

    assert engine._serialize_grid(None) is None
    assert engine._deserialize_grid(None) is None
    assert restored == grid
    assert restored.__dict__["trail_armed"] is True
    assert restored.__dict__["trail_stop"] == 95.5


def test_snapshot_signature_and_rounding_are_stable():
    assert engine._stable_signature({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert engine._round_snapshot_value(
        {"b": {"x": 2.34567}, "a": (1.23456, True, None)},
        4,
    ) == {"a": [1.2346, True, None], "b": {"x": 2.3457}}


def test_market_movement_helpers_cover_threshold_edges():
    assert engine._relative_bps_moved(None, None, 5) is False
    assert engine._relative_bps_moved(None, 1, 5) is True
    assert engine._relative_bps_moved(100.0, 100.04, 5) is False
    assert engine._relative_bps_moved(100.0, 100.2, 5) is True
    assert engine._relative_bps_moved(1.0, 1.0, 0) is False
    assert engine._relative_bps_moved(1.0, 1.01, 0) is True
    assert engine._market_values_moved({"price": 100.0}, {"price": 100.01}, 5) is False
    assert engine._market_values_moved({"price": 100.0}, {"other": 100.0}, 5) is True


def test_snapshot_gate_force_market_window_and_record_copy():
    gate = engine._SnapshotChangeGate(10, 60, 5)
    first = gate.evaluate(
        critical_signature="a",
        market_signature="w1",
        market_values={"price": 100.0},
        now_monotonic=0.0,
    )
    gate.record(first)
    first.market_values["price"] = 999.0

    assert gate.last_market_values == {"price": 100.0}

    forced = gate.evaluate(
        critical_signature="a",
        market_signature="w1",
        force=True,
        now_monotonic=1.0,
    )
    assert forced.should_persist is True
    assert forced.reason == "forced"

    window_changed = gate.evaluate(
        critical_signature="a",
        market_signature="w2",
        market_values={"price": 100.0},
        now_monotonic=10.0,
    )
    assert window_changed.should_persist is True
    assert window_changed.reason == "market_window_changed"


def test_ai_control_signature_filters_and_rounds_stable_fields():
    signature = engine._ai_control_signature(
        {
            "enabled": True,
            "confidence": 0.123456789,
            "riskBudgetPct": 0.987654321,
            "promptVersion": "ignored",
        }
    )

    assert signature == {
        "enabled": True,
        "confidence": 0.12345679,
        "riskBudgetPct": 0.98765432,
    }


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


def test_reconcile_accounting_flags_anomalies_and_loss_edges(monkeypatch):
    monkeypatch.setattr(
        engine,
        "_load_trade_events",
        lambda: [
            {"event": "ENGINE_START", "tsUtc": "2026-01-01T00:00:00+00:00"},
            {"event": "ENGINE_START", "tsUtc": "2026-01-01T00:00:01+00:00"},
            {"event": "EXIT", "tsUtc": "2026-01-01T00:00:02+00:00", "qtyBtc": 0.0},
            {"event": "ENTER", "qtyBtc": 1.0, "notionalUsdt": 100.0, "feeUsdt": 1.0},
            {
                "event": "EXIT",
                "tsUtc": "2026-01-01T00:00:03+00:00",
                "qtyBtc": 2.0,
                "notionalUsdt": 90.0,
                "feeUsdt": 0.9,
            },
            {
                "event": "EXIT",
                "qtyBtc": 0.5,
                "notionalUsdt": 40.0,
                "feeUsdt": 0.4,
                "grossRealizedPnlUsdt": -10.5,
                "realizedPnlUsdt": -10.9,
            },
        ],
    )

    result = engine._reconcile_accounting_from_trade_log(start_usdt=200.0, start_btc=0.0)

    assert result["paper"]["usdt"] == pytest.approx(138.6)
    assert result["paper"]["btc"] == pytest.approx(0.5)
    assert result["openCostBasisUsdt"] == pytest.approx(50.5)
    assert result["cumulative"]["trades"] == 1
    assert result["cumulative"]["losses"] == 1
    assert result["cumulative"]["wins"] == 0
    assert result["cumulative"]["feesPaidUsdt"] == pytest.approx(1.4)
    assert result["anomalies"] == [
        "duplicate_engine_start:2026-01-01T00:00:01+00:00",
        "oversell:2026-01-01T00:00:03+00:00 qty=2.0 btc_before=1.0",
    ]


def test_reconcile_accounting_normalizes_tiny_negative_residuals(monkeypatch):
    monkeypatch.setattr(
        engine,
        "_load_trade_events",
        lambda: [
            {"event": "ENTER", "qtyBtc": 0.3, "notionalUsdt": 0.3, "feeUsdt": 0.0},
            {"event": "EXIT", "qtyBtc": 0.1, "notionalUsdt": 0.11, "feeUsdt": 0.0},
            {"event": "EXIT", "qtyBtc": 0.1, "notionalUsdt": 0.11, "feeUsdt": 0.0},
            {
                "event": "EXIT",
                "qtyBtc": 0.1000000000005,
                "notionalUsdt": 0.11,
                "feeUsdt": 0.0,
            },
        ],
    )

    result = engine._reconcile_accounting_from_trade_log(start_usdt=0.0, start_btc=0.0)

    assert result["paper"]["btc"] == 0.0
    assert result["openCostBasisUsdt"] == 0.0
    assert result["anomalies"] == []


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
