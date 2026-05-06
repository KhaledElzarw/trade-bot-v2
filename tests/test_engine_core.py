import json
from datetime import datetime, timezone

import pytest

import engine


def _engine_test_cum() -> dict:
    return {
        "sinceUtc": "2026-05-06T00:00:00+00:00",
        "realizedPnlUsdt": 0.0,
        "grossRealizedPnlUsdt": 0.0,
        "feesPaidUsdt": 0.0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
    }


def _engine_test_klines(*, price: float = 100.0, count: int = 210) -> list:
    rows = []
    for idx in range(count):
        rows.append([
            1_700_000_000_000 + idx,
            str(price),
            str(price + 1.0),
            str(price - 1.0),
            str(price),
            "1.0",
            1_700_000_060_000 + idx,
            "100.0",
        ])
    return rows


def _fake_engine_tick_deps(
    *,
    klines: list | None = None,
    ai_decision: dict | None = None,
    trade_events: list[dict] | None = None,
    cum: dict | None = None,
    monotonic_values: list[float] | None = None,
    now: datetime | None = None,
) -> tuple[engine._EngineTickDeps, dict]:
    calls = {
        "fetch_klines": [],
        "state_writes": [],
        "cum_reads": [],
        "cum_writes": [],
        "trade_event_loads": [],
        "status_writes": [],
        "runtime_writes": [],
        "maybe_runtime_calls": [],
        "trade_appends": [],
        "logs": [],
        "sleeps": [],
        "ai_decision_calls": [],
        "monotonic_calls": [],
    }
    kline_payload = klines if klines is not None else _engine_test_klines()
    ai_payload = ai_decision if ai_decision is not None else {"enabled": False, "source": "disabled"}
    event_payload = trade_events if trade_events is not None else []
    cum_payload = cum if cum is not None else _engine_test_cum()
    monotonic_payload = list(monotonic_values if monotonic_values is not None else [0.0])
    now_value = now or datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)

    def fetch_klines(symbol: str, interval: str, limit: int) -> list:
        calls["fetch_klines"].append((symbol, interval, limit))
        return kline_payload

    def write_state(state: dict) -> None:
        calls["state_writes"].append(dict(state))

    def read_cum() -> dict:
        calls["cum_reads"].append(True)
        return dict(cum_payload)

    def write_cum(payload: dict) -> None:
        calls["cum_writes"].append(dict(payload))

    def load_trade_events() -> list[dict]:
        calls["trade_event_loads"].append(True)
        return list(event_payload)

    def write_status(payload: dict) -> None:
        calls["status_writes"].append(payload)

    def write_runtime_state(payload: dict) -> None:
        calls["runtime_writes"].append(payload)

    def maybe_write_runtime_state(gate: engine._SnapshotChangeGate, payload: dict) -> tuple[bool, str]:
        calls["maybe_runtime_calls"].append((gate, payload))
        return True, "test"

    def append_trade(event: dict) -> None:
        calls["trade_appends"].append(dict(event))

    def log(message: str) -> None:
        calls["logs"].append(message)

    def sleep(seconds: float) -> None:
        calls["sleeps"].append(seconds)

    def monotonic() -> float:
        calls["monotonic_calls"].append(True)
        if monotonic_payload:
            return monotonic_payload.pop(0)
        return 0.0

    def read_ai_decision(state: dict) -> dict:
        calls["ai_decision_calls"].append(dict(state))
        return dict(ai_payload)

    deps = engine._EngineTickDeps(
        fetch_klines=fetch_klines,
        write_state=write_state,
        read_cum=read_cum,
        write_cum=write_cum,
        load_trade_events=load_trade_events,
        write_status=write_status,
        write_runtime_state=write_runtime_state,
        maybe_write_runtime_state=maybe_write_runtime_state,
        append_trade=append_trade,
        log=log,
        sleep=sleep,
        monotonic=monotonic,
        utc_now=lambda: now_value,
        read_ai_decision=read_ai_decision,
    )
    return deps, calls


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


@pytest.mark.parametrize(
    ("mode", "expected_spacing", "expected_levels"),
    [("scalpy", 0.008, 14), ("fatty", 0.014, 8)],
)
def test_compute_grid_plan_non_ai_uses_mode_spacing_and_state_exposure(
    mode,
    expected_spacing,
    expected_levels,
):
    plan = engine._compute_grid_plan(
        {
            "gridMinSpacingPctScalpy": 0.006,
            "gridMinSpacingPctFatty": 0.010,
            "gridMaxExposurePct": 0.12,
        },
        {
            "recommendedSpacingPct": 0.03,
            "recommendedLevels": 4,
            "recommendedMaxExposurePct": 0.60,
        },
        False,
        grid_mode=mode,
        atr=10.0,
        price=1000.0,
    )

    assert plan["spacing_pct"] == pytest.approx(expected_spacing)
    assert plan["levels"] == expected_levels
    assert plan["max_expo"] == pytest.approx(0.12)
    assert plan["min_scalpy"] == pytest.approx(0.006)
    assert plan["min_fatty"] == pytest.approx(0.010)


def test_compute_grid_plan_ai_live_clamps_recommended_values():
    plan = engine._compute_grid_plan(
        {
            "gridMinSpacingPctScalpy": 0.006,
            "gridMinSpacingPctFatty": 0.010,
            "gridMaxExposurePct": 0.10,
        },
        {
            "recommendedSpacingPct": 0.20,
            "recommendedLevels": 99,
            "recommendedMaxExposurePct": 0.90,
        },
        True,
        grid_mode="scalpy",
        atr=1.0,
        price=1000.0,
    )

    assert plan["spacing_pct"] == pytest.approx(0.03)
    assert plan["levels"] == 24
    assert plan["max_expo"] == pytest.approx(0.60)


def test_compute_grid_plan_ai_reduce_exposure_limits_max_exposure_to_risk_budget():
    plan = engine._compute_grid_plan(
        {"gridMaxExposurePct": 0.50},
        {
            "recommendedMaxExposurePct": 0.40,
            "reduceExposure": True,
            "riskBudgetPct": 0.15,
        },
        True,
        grid_mode="scalpy",
        atr=1.0,
        price=1000.0,
    )

    assert plan["max_expo"] == pytest.approx(0.15)


def test_compute_grid_plan_ai_reduce_exposure_preserves_zero_risk_budget():
    plan = engine._compute_grid_plan(
        {"gridMaxExposurePct": 0.50},
        {
            "recommendedMaxExposurePct": 0.40,
            "reduceExposure": True,
            "riskBudgetPct": 0.0,
        },
        True,
        grid_mode="scalpy",
        atr=1.0,
        price=1000.0,
    )

    assert plan["max_expo"] == pytest.approx(0.0)


def test_compute_grid_plan_ai_ignores_zero_risk_budget_without_reduce_exposure():
    plan = engine._compute_grid_plan(
        {"gridMaxExposurePct": 0.50},
        {
            "recommendedMaxExposurePct": 0.40,
            "riskBudgetPct": 0.0,
        },
        True,
        grid_mode="scalpy",
        atr=1.0,
        price=1000.0,
    )

    assert plan["max_expo"] == pytest.approx(0.40)


@pytest.mark.parametrize(
    ("min_scalpy", "expected_spacing"),
    [(0.012, 0.006), (0.004, 0.003)],
)
def test_compute_grid_plan_ai_spacing_lower_clamp_uses_half_scalpy_or_floor(
    min_scalpy,
    expected_spacing,
):
    plan = engine._compute_grid_plan(
        {
            "gridMinSpacingPctScalpy": min_scalpy,
            "gridMinSpacingPctFatty": 0.010,
        },
        {"recommendedSpacingPct": 0.001},
        True,
        grid_mode="scalpy",
        atr=0.0,
        price=1000.0,
    )

    assert plan["spacing_pct"] == pytest.approx(expected_spacing)


def test_spacing_fee_floor_decision_uses_default_scalpy_floor():
    should_skip, required_floor = engine._spacing_fee_floor_decision({}, 0.005)

    assert should_skip is True
    assert required_floor == pytest.approx(0.006)


def test_spacing_fee_floor_decision_uses_fee_plus_profit_floor_when_higher():
    should_skip, required_floor = engine._spacing_fee_floor_decision(
        {
            "feeBps": 20,
            "gridTrailMinNetProfitPct": 0.002,
            "gridMinSpacingPctScalpy": 0.001,
        },
        0.005,
    )

    assert should_skip is True
    assert required_floor == pytest.approx(0.006)


def test_spacing_fee_floor_decision_allows_spacing_equal_to_floor():
    should_skip, required_floor = engine._spacing_fee_floor_decision({}, 0.006)

    assert should_skip is False
    assert required_floor == pytest.approx(0.006)


def test_spacing_fee_floor_decision_coerces_string_values_like_main():
    should_skip, required_floor = engine._spacing_fee_floor_decision(
        {
            "feeBps": "20",
            "gridTrailMinNetProfitPct": "0.002",
            "gridMinSpacingPctScalpy": "0.001",
        },
        0.005,
    )

    assert should_skip is True
    assert required_floor == pytest.approx(0.006)


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


def test_select_crossed_grid_orders_buy_uses_candle_cross_not_latest_price():
    order = engine.GridOrder(side="BUY", price=100.0, qty_btc=0.5)
    paper = engine.PaperAccount(usdt=100.0, btc=0.0)

    selected = engine._select_crossed_grid_orders(
        [order],
        candle_lo=99.0,
        candle_hi=101.0,
        price=98.0,
        paper=paper,
        ai_pause_new_buys=False,
        fee_rate=0.001,
    )

    assert selected == [order]


def test_select_crossed_grid_orders_sell_uses_candle_cross_not_latest_price():
    order = engine.GridOrder(side="SELL", price=100.0, qty_btc=0.5)
    paper = engine.PaperAccount(usdt=0.0, btc=0.5)

    selected = engine._select_crossed_grid_orders(
        [order],
        candle_lo=99.0,
        candle_hi=101.0,
        price=102.0,
        paper=paper,
        ai_pause_new_buys=False,
        fee_rate=0.001,
    )

    assert selected == [order]


def test_select_crossed_grid_orders_blocks_buy_when_ai_pauses_new_buys():
    order = engine.GridOrder(side="BUY", price=100.0, qty_btc=0.5)
    paper = engine.PaperAccount(usdt=100.0, btc=0.0)

    selected = engine._select_crossed_grid_orders(
        [order],
        candle_lo=99.0,
        candle_hi=101.0,
        price=100.0,
        paper=paper,
        ai_pause_new_buys=True,
        fee_rate=0.001,
    )

    assert selected == []


def test_select_crossed_grid_orders_blocks_buy_without_fee_coverage():
    order = engine.GridOrder(side="BUY", price=100.0, qty_btc=1.0)
    paper = engine.PaperAccount(usdt=100.0, btc=0.0)

    selected = engine._select_crossed_grid_orders(
        [order],
        candle_lo=99.0,
        candle_hi=101.0,
        price=100.0,
        paper=paper,
        ai_pause_new_buys=False,
        fee_rate=0.001,
    )

    assert selected == []


def test_select_crossed_grid_orders_blocks_sell_without_btc_coverage():
    order = engine.GridOrder(side="SELL", price=100.0, qty_btc=0.5)
    paper = engine.PaperAccount(usdt=0.0, btc=0.25)

    selected = engine._select_crossed_grid_orders(
        [order],
        candle_lo=99.0,
        candle_hi=101.0,
        price=100.0,
        paper=paper,
        ai_pause_new_buys=False,
        fee_rate=0.001,
    )

    assert selected == []


def test_select_crossed_grid_orders_skips_non_crossed_order():
    order = engine.GridOrder(side="BUY", price=100.0, qty_btc=0.5)
    paper = engine.PaperAccount(usdt=100.0, btc=0.0)

    selected = engine._select_crossed_grid_orders(
        [order],
        candle_lo=101.0,
        candle_hi=102.0,
        price=100.0,
        paper=paper,
        ai_pause_new_buys=False,
        fee_rate=0.001,
    )

    assert selected == []


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


def test_roll_stats_if_new_day_resets_daily_peak_and_counters():
    stats = engine.Stats(
        day="2026-05-05",
        trades=3,
        wins=2,
        losses=1,
        pnl_usdt=25.0,
        max_drawdown_pct=0.12,
        peak_equity=12_000.0,
        cooldown_until=datetime(2026, 5, 5, 23, 30, tzinfo=timezone.utc),
    )

    rolled = engine._roll_stats_if_new_day(
        stats,
        datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc),
        equity=10_000.0,
    )

    assert rolled.day == "2026-05-06"
    assert rolled.peak_equity == pytest.approx(10_000.0)
    assert rolled.trades == 0
    assert rolled.wins == 0
    assert rolled.losses == 0
    assert rolled.pnl_usdt == 0.0
    assert rolled.max_drawdown_pct == 0.0
    assert rolled.cooldown_until is None


def test_roll_stats_if_new_day_keeps_same_day_stats():
    stats = engine.Stats(day="2026-05-06", peak_equity=12_000.0, trades=3)

    rolled = engine._roll_stats_if_new_day(
        stats,
        datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
        equity=10_000.0,
    )

    assert rolled is stats


def test_update_equity_drawdown_initializes_peak_when_missing():
    stats = engine.Stats(day="2026-05-06", peak_equity=0.0)

    updated = engine._update_equity_drawdown(stats, equity=1000.0)

    assert updated is stats
    assert stats.peak_equity == pytest.approx(1000.0)
    assert stats.max_drawdown_pct == pytest.approx(0.0)


def test_update_equity_drawdown_raises_peak_on_new_high():
    stats = engine.Stats(day="2026-05-06", peak_equity=900.0)

    updated = engine._update_equity_drawdown(stats, equity=1000.0)

    assert updated is stats
    assert stats.peak_equity == pytest.approx(1000.0)
    assert stats.max_drawdown_pct == pytest.approx(0.0)


def test_update_equity_drawdown_records_larger_drawdown():
    stats = engine.Stats(day="2026-05-06", peak_equity=1000.0, max_drawdown_pct=0.05)

    updated = engine._update_equity_drawdown(stats, equity=800.0)

    assert updated is stats
    assert stats.peak_equity == pytest.approx(1000.0)
    assert stats.max_drawdown_pct == pytest.approx(0.20)


def test_update_equity_drawdown_does_not_reduce_existing_max_drawdown():
    stats = engine.Stats(day="2026-05-06", peak_equity=1000.0, max_drawdown_pct=0.20)

    updated = engine._update_equity_drawdown(stats, equity=950.0)

    assert updated is stats
    assert stats.peak_equity == pytest.approx(1000.0)
    assert stats.max_drawdown_pct == pytest.approx(0.20)


def test_daily_stop_decision_no_peak_returns_zero_loss_without_stop():
    stats = engine.Stats(day="2026-05-06", peak_equity=0.0, max_drawdown_pct=0.20)
    before = stats.__dict__.copy()

    daily_stop_hit, daily_loss_pct = engine._daily_stop_decision(
        stats,
        equity=900.0,
        max_daily_loss_pct=0.10,
    )

    assert daily_stop_hit is False
    assert daily_loss_pct == pytest.approx(0.0)
    assert stats.__dict__ == before


def test_daily_stop_decision_below_threshold_does_not_stop():
    stats = engine.Stats(day="2026-05-06", peak_equity=1000.0, max_drawdown_pct=0.20)
    before = stats.__dict__.copy()

    daily_stop_hit, daily_loss_pct = engine._daily_stop_decision(
        stats,
        equity=950.0,
        max_daily_loss_pct=0.10,
    )

    assert daily_stop_hit is False
    assert daily_loss_pct == pytest.approx(0.05)
    assert stats.__dict__ == before


def test_daily_stop_decision_at_threshold_stops():
    stats = engine.Stats(day="2026-05-06", peak_equity=1000.0, max_drawdown_pct=0.20)
    before = stats.__dict__.copy()

    daily_stop_hit, daily_loss_pct = engine._daily_stop_decision(
        stats,
        equity=900.0,
        max_daily_loss_pct=0.10,
    )

    assert daily_stop_hit is True
    assert daily_loss_pct == pytest.approx(0.10)
    assert stats.__dict__ == before


def test_daily_stop_decision_equity_above_peak_clamps_loss_to_zero():
    stats = engine.Stats(day="2026-05-06", peak_equity=1000.0, max_drawdown_pct=0.20)
    before = stats.__dict__.copy()

    daily_stop_hit, daily_loss_pct = engine._daily_stop_decision(
        stats,
        equity=1100.0,
        max_daily_loss_pct=0.10,
    )

    assert daily_stop_hit is False
    assert daily_loss_pct == pytest.approx(0.0)
    assert stats.__dict__ == before


def test_inactive_reason_returns_paused():
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    stats = engine.Stats(day="2026-05-06")

    assert engine._inactive_reason({"paused": True}, stats, now) == "paused"


def test_inactive_reason_paused_wins_over_cooldown():
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    stats = engine.Stats(
        day="2026-05-06",
        cooldown_until=datetime(2026, 5, 6, 12, 30, tzinfo=timezone.utc),
    )

    assert engine._inactive_reason({"paused": True}, stats, now) == "paused"


def test_inactive_reason_returns_cooldown_for_future_cooldown():
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    stats = engine.Stats(
        day="2026-05-06",
        cooldown_until=datetime(2026, 5, 6, 12, 30, tzinfo=timezone.utc),
    )

    assert engine._inactive_reason({"paused": False}, stats, now) == "cooldown_after_loss"


def test_inactive_reason_returns_none_for_past_or_missing_cooldown():
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    past_cooldown = engine.Stats(
        day="2026-05-06",
        cooldown_until=datetime(2026, 5, 6, 11, 59, tzinfo=timezone.utc),
    )
    missing_cooldown = engine.Stats(day="2026-05-06")

    assert engine._inactive_reason({"paused": False}, past_cooldown, now) is None
    assert engine._inactive_reason({"paused": False}, missing_cooldown, now) is None


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


def test_market_indicators_from_klines_coerces_kline_strings_and_matches_existing_formulas():
    klines = []
    for i in range(120):
        close = 100.0 + i
        klines.append([
            1_700_000_000_000 + i,
            f"{close - 0.5}",
            f"{close + 1.5}",
            f"{close - 2.0}",
            f"{close}",
            "1.0",
            1_700_000_060_000 + i,
            "100.0",
        ])

    market = engine._market_indicators_from_klines(klines)
    close = [float(k[4]) for k in klines]
    high = [float(k[2]) for k in klines]
    low = [float(k[3]) for k in klines]
    ema20 = engine._ema(close[-60:], period=20)
    ema50 = engine._ema(close[-120:], period=50)

    assert market["close"] == close
    assert market["high"] == high
    assert market["low"] == low
    assert all(isinstance(value, float) for value in market["close"])
    assert all(isinstance(value, float) for value in market["high"])
    assert all(isinstance(value, float) for value in market["low"])
    assert market["price"] == pytest.approx(close[-1])
    assert market["candle_hi"] == pytest.approx(high[-1])
    assert market["candle_lo"] == pytest.approx(low[-1])
    assert market["atr"] == pytest.approx(engine._atr(high, low, close, period=14))
    assert market["ema20"] == pytest.approx(ema20)
    assert market["ema50"] == pytest.approx(ema50)
    assert market["trend_strength"] == pytest.approx(abs(ema20 - ema50) / close[-1])


def test_market_indicators_from_klines_preserves_short_data_error():
    klines = [
        [1, "100.0", "101.0", "99.0", "100.5"],
        [2, "100.5", "102.0", "100.0", "101.0"],
    ]

    with pytest.raises(ValueError, match="Not enough data for ATR"):
        engine._market_indicators_from_klines(klines)


def test_resolve_grid_mode_uses_state_default_base_and_ai_override():
    assert engine._resolve_grid_mode({}, {}, ai_live=False) == "scalpy"
    assert engine._resolve_grid_mode({"gridMode": "fatty"}, {"recommendedMode": "scalpy"}, ai_live=False) == "fatty"
    assert engine._resolve_grid_mode({"gridMode": "fatty"}, {"recommendedMode": "scalpy"}, ai_live=True) == "scalpy"


def test_resolve_grid_mode_rejects_invalid_state_mode():
    with pytest.raises(ValueError, match="unsupported gridMode 'flexy'"):
        engine._resolve_grid_mode({"gridMode": "flexy"}, {}, ai_live=False)


@pytest.mark.parametrize("btc", [0.0, -0.1])
def test_position_payload_returns_none_without_btc(btc):
    paper = engine.PaperAccount(usdt=100.0, btc=btc)

    assert engine._position_payload(paper, None, price=120.0) is None


def test_position_payload_reports_grid_cost_basis_unrealized_fields():
    paper = engine.PaperAccount(usdt=100.0, btc=0.5)
    grid = engine.GridState(
        anchor=100.0,
        spacing_pct=0.01,
        levels=2,
        max_exposure_pct=0.5,
        reserved_usdt=25.0,
        reserved_btc=0.5,
        cost_basis_usdt=50.0,
        orders=[],
        active=True,
        last_recenter_utc="2026-05-06T00:00:00+00:00",
    )
    grid.__dict__["trail_stop"] = 95.0

    assert engine._position_payload(paper, grid, price=120.0) == {
        "entryPrice": 100.0,
        "qtyBtc": 0.5,
        "stop": 95.0,
        "tp": None,
        "entryTimeUtc": "2026-05-06T00:00:00+00:00",
        "unrealizedPnlUsdt": 10.0,
        "unrealizedPnlPct": pytest.approx(0.2),
    }


def test_position_payload_without_grid_uses_zero_unrealized_fields():
    paper = engine.PaperAccount(usdt=100.0, btc=0.5)

    assert engine._position_payload(paper, None, price=120.0) == {
        "entryPrice": None,
        "qtyBtc": 0.5,
        "stop": None,
        "tp": None,
        "entryTimeUtc": None,
        "unrealizedPnlUsdt": 0.0,
        "unrealizedPnlPct": 0.0,
    }


def test_skip_position_payload_returns_none_without_btc():
    paper = engine.PaperAccount(usdt=100.0, btc=0.0)

    assert engine._skip_position_payload(paper, None) is None


def test_skip_position_payload_preserves_manual_zero_unrealized_shape():
    paper = engine.PaperAccount(usdt=100.0, btc=0.25)
    grid = engine.GridState(
        anchor=100.0,
        spacing_pct=0.01,
        levels=2,
        max_exposure_pct=0.5,
        reserved_usdt=25.0,
        reserved_btc=0.25,
        cost_basis_usdt=50.0,
        orders=[],
        active=False,
        last_recenter_utc="2026-05-06T00:00:00+00:00",
    )

    assert engine._skip_position_payload(paper, grid) == {
        "entryPrice": 200.0,
        "qtyBtc": 0.25,
        "stop": 0.0,
        "tp": None,
        "entryTimeUtc": "2026-05-06T00:00:00+00:00",
        "unrealizedPnlUsdt": 0.0,
        "unrealizedPnlPct": 0.0,
    }


def test_skip_position_payload_preserves_grid_trail_stop_and_entry_time_fields():
    paper = engine.PaperAccount(usdt=100.0, btc=0.5)
    grid = engine.GridState(
        anchor=100.0,
        spacing_pct=0.01,
        levels=2,
        max_exposure_pct=0.5,
        reserved_usdt=25.0,
        reserved_btc=0.5,
        cost_basis_usdt=50.0,
        orders=[],
        active=False,
        last_recenter_utc="2026-05-06T00:00:00+00:00",
    )
    grid.__dict__["trail_stop"] = 95.0

    payload = engine._skip_position_payload(paper, grid)

    assert payload["entryPrice"] == 100.0
    assert payload["stop"] == 95.0
    assert payload["entryTimeUtc"] == "2026-05-06T00:00:00+00:00"


def test_skip_position_payload_without_grid_uses_none_entry_fields_and_zero_unrealized():
    paper = engine.PaperAccount(usdt=100.0, btc=0.5)

    assert engine._skip_position_payload(paper, None) == {
        "entryPrice": None,
        "qtyBtc": 0.5,
        "stop": None,
        "tp": None,
        "entryTimeUtc": None,
        "unrealizedPnlUsdt": 0.0,
        "unrealizedPnlPct": 0.0,
    }


def test_grid_telemetry_reports_effective_ai_mode_and_preserves_inputs():
    payload = engine._grid_telemetry(
        state={"gridMode": "scalpy"},
        ai_signal={"recommendedMode": "fatty"},
        effective_mode="fatty",
        spacing_pct=0.012,
        levels=8,
        open_orders=4,
    )

    assert payload == {
        "mode": "fatty",
        "configuredMode": "scalpy",
        "aiRecommendedMode": "fatty",
        "spacingPct": 0.012,
        "levels": 8,
        "openOrders": 4,
    }


def test_grid_telemetry_configured_mode_can_ignore_ai_recommendation():
    payload = engine._grid_telemetry(
        state={"gridMode": "scalpy"},
        ai_signal={"recommendedMode": "fatty"},
        effective_mode="scalpy",
        spacing_pct=0.006,
        levels=14,
        open_orders=0,
    )

    assert payload["mode"] == "scalpy"
    assert payload["configuredMode"] == "scalpy"
    assert payload["aiRecommendedMode"] == "fatty"


def test_grid_telemetry_unsupported_raw_branch_uses_configured_mode():
    payload = engine._grid_telemetry(
        state={"gridMode": "flexy"},
        ai_signal={"recommendedMode": "fatty"},
        effective_mode=None,
        skipped=True,
        skipReason="unsupported_grid_mode",
        error="unsupported gridMode 'flexy'",
    )

    assert payload["mode"] == "flexy"
    assert payload["configuredMode"] == "flexy"
    assert payload["aiRecommendedMode"] == "fatty"
    assert payload["skipped"] is True
    assert payload["skipReason"] == "unsupported_grid_mode"
    assert payload["error"] == "unsupported gridMode 'flexy'"


def test_status_stats_payload_uses_cumulative_values_for_status_counters():
    stats = engine.Stats(
        day="2026-05-06",
        trades=1,
        wins=0,
        losses=1,
        pnl_usdt=-5.0,
        max_drawdown_pct=0.07,
    )
    cum = {
        "trades": 4,
        "wins": 3,
        "losses": 1,
        "realizedPnlUsdt": 12.5,
    }

    payload = engine._status_stats_payload(
        stats=stats,
        cum=cum,
        entries_count=2,
        exits_count=1,
        has_open_position=True,
        trend_strength=0.015,
    )

    assert payload == {
        "day": "2026-05-06",
        "trades": 4,
        "closedTrades": 4,
        "entries": 2,
        "exits": 1,
        "hasOpenPosition": True,
        "wins": 3,
        "losses": 1,
        "pnlUsdt": 12.5,
        "maxDrawdownPct": 0.07,
        "trendStrength": 0.015,
    }


def test_status_stats_payload_uses_stats_values_without_cumulative():
    stats = engine.Stats(
        day="2026-05-06",
        trades=3,
        wins=2,
        losses=1,
        pnl_usdt=7.25,
        max_drawdown_pct=0.03,
    )

    payload = engine._status_stats_payload(stats=stats)

    assert payload == {
        "day": "2026-05-06",
        "trades": 3,
        "wins": 2,
        "losses": 1,
        "pnlUsdt": 7.25,
        "maxDrawdownPct": 0.03,
    }


def test_status_stats_payload_includes_cooldown_only_when_requested():
    cooldown_until = datetime(2026, 5, 6, 12, 30, tzinfo=timezone.utc)
    stats = engine.Stats(day="2026-05-06", cooldown_until=cooldown_until)

    without_cooldown = engine._status_stats_payload(stats=stats)
    with_cooldown = engine._status_stats_payload(stats=stats, include_cooldown=True)

    assert "cooldownUntil" not in without_cooldown
    assert with_cooldown["cooldownUntil"] == "2026-05-06T12:30:00+00:00"


def test_status_stats_payload_passes_through_grid_and_ai_payloads():
    stats = engine.Stats(day="2026-05-06")
    grid_payload = {"mode": "scalpy", "skipped": True}
    ai_signal = {"decision": "hold", "confidence": 0.8}

    payload = engine._status_stats_payload(
        stats=stats,
        grid_payload=grid_payload,
        ai_signal=ai_signal,
    )

    assert payload["grid"] is grid_payload
    assert payload["ai"] is ai_signal


def test_status_stats_payload_omits_detail_counters_when_not_supplied():
    stats = engine.Stats(day="2026-05-06", trades=1, wins=1, pnl_usdt=2.0)
    cum = {
        "trades": 5,
        "wins": 4,
        "losses": 1,
        "realizedPnlUsdt": 18.0,
    }

    payload = engine._status_stats_payload(stats=stats, cum=cum, trend_strength=0.02)

    assert payload["trades"] == 5
    assert payload["wins"] == 4
    assert payload["losses"] == 1
    assert payload["pnlUsdt"] == 18.0
    assert payload["trendStrength"] == 0.02
    assert "closedTrades" not in payload
    assert "entries" not in payload
    assert "exits" not in payload
    assert "hasOpenPosition" not in payload


def test_status_payload_builds_top_level_status_envelope(monkeypatch):
    fixed_now = datetime(2026, 5, 6, 12, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(engine, "_utc_now", lambda: fixed_now)
    paper = engine.PaperAccount(usdt=1000.0, btc=0.25)
    position_payload = {"qtyBtc": 0.25, "entryPrice": 40000.0}
    stats_payload = {"day": "2026-05-06", "trades": 2}

    payload = engine._status_payload(
        state={"mode": "paper"},
        symbol="BTCUSDT",
        interval="15m",
        price=41000.0,
        paper=paper,
        position_payload=position_payload,
        stats_payload=stats_payload,
        last_event="TICK",
    )

    assert payload == {
        "tsUtc": "2026-05-06T12:30:00+00:00",
        "mode": "paper",
        "symbol": "BTCUSDT",
        "interval": "15m",
        "price": 41000.0,
        "equityUsdt": 11250.0,
        "usdt": 1000.0,
        "btc": 0.25,
        "position": position_payload,
        "stats": stats_payload,
        "lastEvent": "TICK",
    }
    assert payload["position"] is position_payload
    assert payload["stats"] is stats_payload


def test_status_payload_supports_none_position_and_preserves_last_event(monkeypatch):
    fixed_now = datetime(2026, 5, 6, 12, 31, tzinfo=timezone.utc)
    monkeypatch.setattr(engine, "_utc_now", lambda: fixed_now)
    stats_payload = {"day": "2026-05-06", "grid": {"skipped": True}}

    payload = engine._status_payload(
        state={"mode": "paper"},
        symbol="BTCUSDT",
        interval="1m",
        price=100.0,
        paper=engine.PaperAccount(usdt=50.0, btc=0.0),
        position_payload=None,
        stats_payload=stats_payload,
        last_event="GRID_SKIP",
    )

    assert payload["position"] is None
    assert payload["stats"] is stats_payload
    assert payload["lastEvent"] == "GRID_SKIP"


def test_runtime_payload_normal_tick_uses_stats_values_without_cum():
    stats = engine.Stats(day="2026-05-06", trades=3, wins=2, losses=1, pnl_usdt=7.25)

    payload = engine._runtime_payload(
        engine_pid=123,
        paper=engine.PaperAccount(usdt=1000.0, btc=0.25),
        stats=stats,
        entries_count=4,
        exits_count=3,
        has_open_position=True,
        market_payload={"price": 41000.0},
        grid=None,
        ai_signal={},
        saved_at="2026-05-06T12:32:00+00:00",
    )

    assert payload["stats"]["trades"] == 3
    assert payload["stats"]["closedTrades"] == 3
    assert payload["stats"]["wins"] == 2
    assert payload["stats"]["losses"] == 1
    assert payload["stats"]["pnl_usdt"] == 7.25


def test_runtime_payload_uses_cumulative_values_when_supplied():
    stats = engine.Stats(day="2026-05-06", trades=1, wins=0, losses=1, pnl_usdt=-1.0)
    cum = {
        "trades": 6,
        "wins": 5,
        "losses": 1,
        "realizedPnlUsdt": 18.75,
    }

    payload = engine._runtime_payload(
        engine_pid=123,
        paper=engine.PaperAccount(usdt=1000.0, btc=0.25),
        stats=stats,
        entries_count=7,
        exits_count=6,
        has_open_position=True,
        market_payload={"price": 41000.0},
        grid=None,
        ai_signal={},
        cum=cum,
        saved_at="2026-05-06T12:33:00+00:00",
    )

    assert payload["stats"]["trades"] == 6
    assert payload["stats"]["closedTrades"] == 6
    assert payload["stats"]["wins"] == 5
    assert payload["stats"]["losses"] == 1
    assert payload["stats"]["pnl_usdt"] == 18.75


def test_runtime_payload_includes_entry_exit_and_open_position_counts():
    payload = engine._runtime_payload(
        engine_pid=123,
        paper=engine.PaperAccount(usdt=1000.0, btc=0.0),
        stats=engine.Stats(day="2026-05-06"),
        entries_count=8,
        exits_count=5,
        has_open_position=False,
        market_payload={"price": 41000.0},
        grid=None,
        ai_signal={},
        saved_at="2026-05-06T12:34:00+00:00",
    )

    assert payload["stats"]["entries"] == 8
    assert payload["stats"]["exits"] == 5
    assert payload["stats"]["hasOpenPosition"] is False


def test_runtime_payload_preserves_drawdown_peak_and_cooldown():
    cooldown_until = datetime(2026, 5, 6, 13, 0, tzinfo=timezone.utc)
    stats = engine.Stats(
        day="2026-05-06",
        max_drawdown_pct=0.04,
        peak_equity=12_500.0,
        cooldown_until=cooldown_until,
    )

    payload = engine._runtime_payload(
        engine_pid=123,
        paper=engine.PaperAccount(usdt=1000.0, btc=0.0),
        stats=stats,
        entries_count=0,
        exits_count=0,
        has_open_position=False,
        market_payload={"price": 41000.0},
        grid=None,
        ai_signal={},
        saved_at="2026-05-06T12:35:00+00:00",
    )

    assert payload["stats"]["max_drawdown_pct"] == 0.04
    assert payload["stats"]["peak_equity"] == 12_500.0
    assert payload["stats"]["cooldown_until"] == "2026-05-06T13:00:00+00:00"


def test_runtime_payload_serializes_grid_with_orders_and_trailing_fields():
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
        last_recenter_utc="2026-05-06T12:00:00+00:00",
    )
    grid.__dict__["trail_armed"] = True
    grid.__dict__["trail_stop"] = 95.5

    payload = engine._runtime_payload(
        engine_pid=123,
        paper=engine.PaperAccount(usdt=1000.0, btc=0.1),
        stats=engine.Stats(day="2026-05-06"),
        entries_count=1,
        exits_count=0,
        has_open_position=True,
        market_payload={"price": 101.0},
        grid=grid,
        ai_signal={},
        saved_at="2026-05-06T12:36:00+00:00",
    )

    assert payload["grid"] == engine._serialize_grid(grid)
    assert payload["grid"]["orders"] == [{"side": "BUY", "price": 99.0, "qty_btc": 0.01}]
    assert payload["grid"]["trail_armed"] is True
    assert payload["grid"]["trail_stop"] == 95.5


def test_runtime_payload_preserves_market_ai_and_injected_saved_at():
    market_payload = {"price": 41000.0, "candle": {"openTimeMs": 1}}
    ai_signal = {"enabled": True, "riskAction": "allow_grid"}

    payload = engine._runtime_payload(
        engine_pid=456,
        paper=engine.PaperAccount(usdt=1000.0, btc=0.25),
        stats=engine.Stats(day="2026-05-06"),
        entries_count=1,
        exits_count=0,
        has_open_position=True,
        market_payload=market_payload,
        grid=None,
        ai_signal=ai_signal,
        saved_at="2026-05-06T12:37:00+00:00",
    )

    assert payload["enginePid"] == 456
    assert payload["paper"] == {"usdt": 1000.0, "btc": 0.25}
    assert payload["market"] is market_payload
    assert payload["ai"] is ai_signal
    assert payload["savedAt"] == "2026-05-06T12:37:00+00:00"


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


def test_bootstrap_runtime_state_restores_reconciled_paper_and_stats():
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    reconciled = {
        "paper": {"usdt": 900.0, "btc": 0.25},
        "cumulative": {
            "realizedPnlUsdt": 12.5,
            "grossRealizedPnlUsdt": 15.0,
            "feesPaidUsdt": 2.5,
            "trades": 4,
            "wins": 3,
            "losses": 1,
        },
        "openCostBasisUsdt": 50.0,
        "anomalies": [],
    }

    bootstrap = engine._bootstrap_runtime_state(
        state={"paperStartUsdt": 10_000.0, "paperStartBtc": 1.0},
        runtime_state={
            "paper": {"usdt": 7_000.0, "btc": 0.75},
            "stats": {"day": "2026-05-05", "max_drawdown_pct": 0.08, "peak_equity": 11_000.0},
        },
        reconciled=reconciled,
        persisted_cum={},
        engine_pid=123,
        is_fresh_start=False,
        symbol="BTCUSDT",
        interval="15m",
        now=now,
    )

    assert bootstrap.paper == engine.PaperAccount(usdt=900.0, btc=0.25)
    assert bootstrap.stats.day == "2026-05-05"
    assert bootstrap.stats.trades == 4
    assert bootstrap.stats.wins == 3
    assert bootstrap.stats.losses == 1
    assert bootstrap.stats.pnl_usdt == 12.5
    assert bootstrap.stats.max_drawdown_pct == 0.08
    assert bootstrap.stats.peak_equity == 11_000.0


def test_bootstrap_runtime_state_preserves_existing_cumulative_since_utc():
    since = "2026-05-05T00:00:00+00:00"
    reconciled = {
        "paper": {"usdt": 1000.0, "btc": 0.0},
        "cumulative": {
            "sinceUtc": None,
            "realizedPnlUsdt": 3.0,
            "grossRealizedPnlUsdt": 4.0,
            "feesPaidUsdt": 1.0,
            "trades": 2,
            "wins": 1,
            "losses": 1,
        },
        "openCostBasisUsdt": 0.0,
        "anomalies": [],
    }
    persisted = {
        "sinceUtc": since,
        "realizedPnlUsdt": 3.0,
        "grossRealizedPnlUsdt": 4.0,
        "feesPaidUsdt": 1.0,
        "trades": 2,
        "wins": 1,
        "losses": 1,
    }

    bootstrap = engine._bootstrap_runtime_state(
        state={},
        runtime_state={},
        reconciled=reconciled,
        persisted_cum=persisted,
        engine_pid=123,
        is_fresh_start=False,
        symbol="BTCUSDT",
        interval="15m",
        now=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
    )

    assert bootstrap.cumulative["sinceUtc"] == since
    assert bootstrap.changed_cumulative is False


def test_bootstrap_runtime_state_uses_now_for_missing_since_utc():
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)

    bootstrap = engine._bootstrap_runtime_state(
        state={},
        runtime_state={},
        reconciled={
            "paper": {"usdt": 1000.0, "btc": 0.0},
            "cumulative": {"trades": 0, "wins": 0, "losses": 0},
            "openCostBasisUsdt": 0.0,
            "anomalies": [],
        },
        persisted_cum={},
        engine_pid=123,
        is_fresh_start=False,
        symbol="BTCUSDT",
        interval="15m",
        now=now,
    )

    assert bootstrap.cumulative["sinceUtc"] == now.isoformat()
    assert bootstrap.changed_cumulative is True


def test_bootstrap_runtime_state_restores_grid_cost_basis():
    runtime_grid = engine.GridState(
        anchor=100.0,
        spacing_pct=0.01,
        levels=2,
        max_exposure_pct=0.35,
        reserved_usdt=50.0,
        reserved_btc=0.1,
        cost_basis_usdt=25.0,
        orders=[],
        active=True,
    )

    bootstrap = engine._bootstrap_runtime_state(
        state={},
        runtime_state={"grid": engine._serialize_grid(runtime_grid)},
        reconciled={
            "paper": {"usdt": 1000.0, "btc": 0.1},
            "cumulative": {"trades": 0, "wins": 0, "losses": 0},
            "openCostBasisUsdt": 40.0,
            "anomalies": [],
        },
        persisted_cum={},
        engine_pid=123,
        is_fresh_start=False,
        symbol="BTCUSDT",
        interval="15m",
        now=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
    )

    assert bootstrap.grid is not None
    assert bootstrap.grid.cost_basis_usdt == 40.0


def test_bootstrap_runtime_state_clears_grid_when_no_btc():
    runtime_grid = engine.GridState(
        anchor=100.0,
        spacing_pct=0.01,
        levels=2,
        max_exposure_pct=0.35,
        reserved_usdt=50.0,
        reserved_btc=0.1,
        cost_basis_usdt=25.0,
        orders=[engine.GridOrder(side="BUY", price=99.0, qty_btc=0.01)],
        active=True,
    )

    bootstrap = engine._bootstrap_runtime_state(
        state={},
        runtime_state={"grid": engine._serialize_grid(runtime_grid)},
        reconciled={
            "paper": {"usdt": 1000.0, "btc": 0.0},
            "cumulative": {"trades": 0, "wins": 0, "losses": 0},
            "openCostBasisUsdt": 25.0,
            "anomalies": [],
        },
        persisted_cum={},
        engine_pid=123,
        is_fresh_start=False,
        symbol="BTCUSDT",
        interval="15m",
        now=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
    )

    assert bootstrap.grid is not None
    assert bootstrap.grid.active is False
    assert bootstrap.grid.orders == []
    assert bootstrap.grid.reserved_btc == 0.0
    assert bootstrap.grid.cost_basis_usdt == 0.0
    assert bootstrap.has_open_position is False


def test_bootstrap_runtime_state_fresh_start_builds_engine_start_event():
    now = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)

    bootstrap = engine._bootstrap_runtime_state(
        state={"mode": "paper"},
        runtime_state={},
        reconciled={
            "paper": {"usdt": 1000.0, "btc": 0.0},
            "cumulative": {"trades": 0, "wins": 0, "losses": 0},
            "openCostBasisUsdt": 0.0,
            "anomalies": [],
        },
        persisted_cum={},
        engine_pid=123,
        is_fresh_start=True,
        symbol="BTCUSDT",
        interval="15m",
        now=now,
    )

    assert bootstrap.startup_event_name == "ENGINE_START"
    assert bootstrap.startup_event == {
        "tsUtc": now.isoformat(),
        "event": "ENGINE_START",
        "mode": "paper",
        "symbol": "BTCUSDT",
        "paper": True,
        "enginePid": 123,
        "hasOpenPosition": False,
    }
    assert bootstrap.startup_log == (
        "ENGINE_START mode=paper symbol=BTCUSDT interval=15m "
        "paper_equity_init_usdt=1000.0 paper_btc_init=0.0"
    )


def test_bootstrap_runtime_state_fresh_start_builds_engine_resume_event():
    bootstrap = engine._bootstrap_runtime_state(
        state={"mode": "paper"},
        runtime_state={},
        reconciled={
            "paper": {"usdt": 1000.0, "btc": 0.25},
            "cumulative": {"trades": 0, "wins": 0, "losses": 0},
            "openCostBasisUsdt": 500.0,
            "anomalies": [],
        },
        persisted_cum={},
        engine_pid=123,
        is_fresh_start=True,
        symbol="BTCUSDT",
        interval="15m",
        now=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
    )

    assert bootstrap.has_open_position is True
    assert bootstrap.startup_event_name == "ENGINE_RESUME"
    assert bootstrap.startup_event["event"] == "ENGINE_RESUME"
    assert bootstrap.startup_event["hasOpenPosition"] is True


def test_bootstrap_runtime_state_non_fresh_start_has_no_startup_event():
    bootstrap = engine._bootstrap_runtime_state(
        state={"mode": "paper"},
        runtime_state={},
        reconciled={
            "paper": {"usdt": 1000.0, "btc": 0.25},
            "cumulative": {"trades": 0, "wins": 0, "losses": 0},
            "openCostBasisUsdt": 500.0,
            "anomalies": [],
        },
        persisted_cum={},
        engine_pid=123,
        is_fresh_start=False,
        symbol="BTCUSDT",
        interval="15m",
        now=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
    )

    assert bootstrap.startup_event_name is None
    assert bootstrap.startup_event is None
    assert bootstrap.startup_log is None


def test_bootstrap_runtime_state_parses_cooldown_until():
    cooldown_until = "2026-05-06T12:30:00+00:00"

    bootstrap = engine._bootstrap_runtime_state(
        state={},
        runtime_state={"stats": {"cooldown_until": cooldown_until}},
        reconciled={
            "paper": {"usdt": 1000.0, "btc": 0.0},
            "cumulative": {"trades": 0, "wins": 0, "losses": 0},
            "openCostBasisUsdt": 0.0,
            "anomalies": [],
        },
        persisted_cum={},
        engine_pid=123,
        is_fresh_start=False,
        symbol="BTCUSDT",
        interval="15m",
        now=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
    )

    assert bootstrap.stats.cooldown_until == datetime(2026, 5, 6, 12, 30, tzinfo=timezone.utc)


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


def test_build_runtime_market_payload_preserves_full_candle_shape():
    kl = [[111, "100", "103", "97", "101", "5.5", 222, "6.5"]]

    payload = engine._build_runtime_market_payload(
        kl,
        [90.0, 101.0],
        price=101.0,
        candle_hi=103.0,
        candle_lo=97.0,
    )

    assert payload == {
        "price": 101.0,
        "candle": {
            "open": 100.0,
            "high": 103.0,
            "low": 97.0,
            "close": 101.0,
            "volumeBase": 5.5,
            "volumeUsdt": 6.5,
            "openTimeMs": 111,
            "closeTimeMs": 222,
        },
    }


def test_build_runtime_market_payload_falls_back_to_previous_close_without_kline_open():
    payload = engine._build_runtime_market_payload(
        [[111]],
        [90.0, 101.0],
        price=101.0,
        candle_hi=103.0,
        candle_lo=97.0,
    )

    assert payload["candle"]["open"] == 90.0
    assert payload["candle"]["volumeBase"] == 0.0
    assert payload["candle"]["volumeUsdt"] == 0.0
    assert payload["candle"]["openTimeMs"] == 111
    assert payload["candle"]["closeTimeMs"] is None


def test_build_runtime_market_payload_uses_price_when_no_kline_open_or_previous_close():
    payload = engine._build_runtime_market_payload(
        [[111]],
        [100.0],
        price=101.0,
        candle_hi=103.0,
        candle_lo=97.0,
    )

    assert payload["candle"]["open"] == 101.0
    assert payload["candle"]["volumeBase"] == 0.0
    assert payload["candle"]["volumeUsdt"] == 0.0
    assert payload["candle"]["openTimeMs"] == 111
    assert payload["candle"]["closeTimeMs"] is None


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


def test_run_engine_tick_normal_no_fill_updates_status_runtime_without_trade_side_effects():
    deps, calls = _fake_engine_tick_deps(
        klines=_engine_test_klines(price=100.0),
        ai_decision={"enabled": False, "source": "disabled"},
        monotonic_values=[0.5],
    )
    paper = engine.PaperAccount(usdt=1000.0, btc=0.1)
    stats = engine.Stats(day="2026-05-06", peak_equity=1010.0)
    grid = engine.GridState(
        anchor=100.0,
        spacing_pct=0.01,
        levels=2,
        max_exposure_pct=0.10,
        reserved_usdt=100.0,
        reserved_btc=0.1,
        cost_basis_usdt=10.0,
        orders=[
            engine.GridOrder(side="BUY", price=50.0, qty_btc=0.01),
            engine.GridOrder(side="SELL", price=150.0, qty_btc=0.01),
        ],
        active=True,
    )
    cum = _engine_test_cum()

    result = engine._run_engine_tick(
        state={
            "mode": "paper",
            "gridMode": "scalpy",
            "gridTrailActive": False,
            "maxDailyLossPct": 0.50,
        },
        paper=paper,
        stats=stats,
        grid=grid,
        cum=cum,
        symbol="BTCUSDT",
        interval="15m",
        runtime_snapshot_gate=engine._SnapshotChangeGate(10, 60, 5),
        engine_pid=123,
        last_heartbeat_log_monotonic=0.0,
        deps=deps,
    )

    assert result.last_event == "TICK"
    assert result.paper is paper
    assert result.stats is stats
    assert result.grid is grid
    assert result.cum is cum
    assert calls["fetch_klines"] == [("BTCUSDT", "15m", 210)]
    assert len(calls["status_writes"]) == 1
    assert calls["status_writes"][0]["lastEvent"] == "TICK"
    assert len(calls["maybe_runtime_calls"]) == 1
    assert calls["maybe_runtime_calls"][0][1]["enginePid"] == 123
    assert calls["trade_appends"] == []
    assert calls["cum_writes"] == []
    assert calls["state_writes"] == []
    assert calls["runtime_writes"] == []
    assert calls["sleeps"] == [1]


def test_run_engine_tick_daily_stop_pauses_state_before_ai_status_or_runtime():
    deps, calls = _fake_engine_tick_deps(klines=_engine_test_klines(price=100.0))
    paper = engine.PaperAccount(usdt=800.0, btc=0.0)
    stats = engine.Stats(day="2026-05-06", peak_equity=1000.0)
    cum = _engine_test_cum()

    result = engine._run_engine_tick(
        state={
            "mode": "paper",
            "gridMode": "scalpy",
            "maxDailyLossPct": 0.10,
        },
        paper=paper,
        stats=stats,
        grid=None,
        cum=cum,
        symbol="BTCUSDT",
        interval="15m",
        runtime_snapshot_gate=engine._SnapshotChangeGate(10, 60, 5),
        engine_pid=123,
        last_heartbeat_log_monotonic=None,
        deps=deps,
    )

    assert result.last_event == "DAILY_STOP"
    assert result.paper is paper
    assert result.stats is stats
    assert result.grid is None
    assert result.cum is cum
    assert calls["state_writes"] == [{"mode": "paper", "gridMode": "scalpy", "maxDailyLossPct": 0.10, "paused": True}]
    assert calls["ai_decision_calls"] == []
    assert calls["status_writes"] == []
    assert calls["runtime_writes"] == []
    assert calls["maybe_runtime_calls"] == []
    assert calls["trade_appends"] == []
    assert calls["cum_writes"] == []
    assert calls["sleeps"] == [1]


def test_run_engine_tick_invalid_grid_mode_writes_skip_status_without_runtime_or_trades():
    deps, calls = _fake_engine_tick_deps(
        klines=_engine_test_klines(price=100.0),
        ai_decision={"enabled": False, "source": "disabled"},
    )
    paper = engine.PaperAccount(usdt=1000.0, btc=0.1)
    stats = engine.Stats(day="2026-05-06", peak_equity=1010.0)
    grid = engine.GridState(
        anchor=100.0,
        spacing_pct=0.01,
        levels=1,
        max_exposure_pct=0.10,
        reserved_usdt=100.0,
        reserved_btc=0.1,
        cost_basis_usdt=10.0,
        orders=[engine.GridOrder(side="BUY", price=99.0, qty_btc=0.01)],
        active=True,
    )

    result = engine._run_engine_tick(
        state={
            "mode": "paper",
            "gridMode": "flexy",
            "maxDailyLossPct": 0.50,
        },
        paper=paper,
        stats=stats,
        grid=grid,
        cum=_engine_test_cum(),
        symbol="BTCUSDT",
        interval="15m",
        runtime_snapshot_gate=engine._SnapshotChangeGate(10, 60, 5),
        engine_pid=123,
        last_heartbeat_log_monotonic=None,
        deps=deps,
    )

    assert result.last_event == "GRID_CONFIG_INVALID"
    assert len(calls["status_writes"]) == 1
    status = calls["status_writes"][0]
    assert status["lastEvent"] == "GRID_CONFIG_INVALID"
    grid_status = status["stats"]["grid"]
    assert grid_status["skipped"] is True
    assert grid_status["skipReason"] == "unsupported_grid_mode"
    assert "flexy" in grid_status["error"]
    assert calls["state_writes"] == []
    assert calls["cum_writes"] == []
    assert calls["trade_appends"] == []
    assert calls["runtime_writes"] == []
    assert calls["maybe_runtime_calls"] == []
    assert calls["sleeps"] == [1]


@pytest.mark.parametrize(
    ("state_overrides", "cooldown_until", "expected_event", "expected_skip_reason"),
    [
        ({"paused": True}, None, "PAUSED", "paused"),
        (
            {"paused": False},
            datetime(2026, 5, 6, 12, 30, tzinfo=timezone.utc),
            "COOLDOWN",
            "cooldown_after_loss",
        ),
    ],
)
def test_run_engine_tick_inactive_paused_or_cooldown_writes_status_runtime_and_returns(
    state_overrides,
    cooldown_until,
    expected_event,
    expected_skip_reason,
):
    deps, calls = _fake_engine_tick_deps(
        klines=_engine_test_klines(price=100.0),
        ai_decision={"enabled": False, "source": "disabled"},
    )
    paper = engine.PaperAccount(usdt=1000.0, btc=0.1)
    stats = engine.Stats(day="2026-05-06", peak_equity=1010.0, cooldown_until=cooldown_until)
    grid = engine.GridState(
        anchor=100.0,
        spacing_pct=0.01,
        levels=1,
        max_exposure_pct=0.10,
        reserved_usdt=100.0,
        reserved_btc=0.1,
        cost_basis_usdt=10.0,
        orders=[engine.GridOrder(side="BUY", price=99.0, qty_btc=0.01)],
        active=True,
    )
    state = {
        "mode": "paper",
        "gridMode": "scalpy",
        "maxDailyLossPct": 0.50,
        **state_overrides,
    }

    result = engine._run_engine_tick(
        state=state,
        paper=paper,
        stats=stats,
        grid=grid,
        cum=_engine_test_cum(),
        symbol="BTCUSDT",
        interval="15m",
        runtime_snapshot_gate=engine._SnapshotChangeGate(10, 60, 5),
        engine_pid=123,
        last_heartbeat_log_monotonic=None,
        deps=deps,
    )

    assert result.last_event == expected_event
    assert len(calls["status_writes"]) == 1
    status = calls["status_writes"][0]
    assert status["lastEvent"] == expected_event
    assert "cooldownUntil" in status["stats"]
    grid_status = status["stats"]["grid"]
    assert grid_status["skipped"] is True
    assert grid_status["skipReason"] == expected_skip_reason
    assert len(calls["maybe_runtime_calls"]) == 1
    assert calls["runtime_writes"] == []
    assert calls["state_writes"] == []
    assert calls["trade_appends"] == []
    assert calls["cum_writes"] == []
    assert calls["monotonic_calls"] == []
    assert calls["sleeps"] == [1]
