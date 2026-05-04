import ai_sidecar
import pytest


def test_safe_number_helpers_use_defaults():
    assert ai_sidecar._safe_float("1.25") == 1.25
    assert ai_sidecar._safe_float("bad", default=9.5) == 9.5
    assert ai_sidecar._safe_int("7.9") == 7
    assert ai_sidecar._safe_int(None, default=4) == 4


def test_model_config_prefers_state_and_clamps_timeout(monkeypatch):
    monkeypatch.setenv("TRADEBOT_AI_PROVIDER", "env-provider")
    monkeypatch.setenv("TRADEBOT_AI_BASE_URL", "http://env-host/v1")
    monkeypatch.setenv("TRADEBOT_AI_MODEL", "env-model")

    cfg = ai_sidecar._model_config(
        {
            "aiProvider": "state-provider",
            "aiBaseUrl": "http://state-host/v1/",
            "aiModel": "primary-model",
            "aiQuickModel": "quick-model",
            "aiFallbackModel": "fallback-model",
            "aiTimeoutSeconds": "0.1",
        }
    )

    assert cfg == {
        "provider": "state-provider",
        "host": "http://state-host/v1",
        "model": "fallback-model",
        "quick_model": "quick-model",
        "deep_model": "fallback-model",
        "fallback_model": "fallback-model",
        "timeout_s": 2.0,
    }


def test_load_template_reads_file_and_falls_back(monkeypatch, tmp_path):
    template = tmp_path / "custom.txt"
    template.write_text("hello {role}", encoding="utf-8")
    monkeypatch.setattr(ai_sidecar, "TEMPLATE_DIR", tmp_path)

    assert ai_sidecar._load_template("custom") == "hello {role}"

    fallback = ai_sidecar._load_template("missing")
    assert "Return strict JSON only" in fallback
    assert "{payload_json}" in fallback


def test_build_payload_includes_grid_and_risk_context():
    payload = ai_sidecar._build_payload(
        {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "feeBps": 10,
            "gridMaxExposurePct": 0.35,
            "maxDailyLossPct": 0.1,
        },
        {
            "market": {
                "price": 100.0,
                "candle": {"open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0},
            },
            "paper": {"usdt": 50.0, "btc": 0.5},
            "grid": {
                "active": True,
                "spacing_pct": 0.01,
                "levels": 12,
                "max_exposure_pct": 0.35,
                "cost_basis_usdt": 45.0,
                "orders": [{"side": "BUY"}, {"side": "SELL"}],
            },
            "stats": {"trades": 2, "max_drawdown_pct": 0.03},
        },
    )

    assert payload["equityUsdt"] == 100.0
    assert payload["openBuyOrders"] == 1
    assert payload["openSellOrders"] == 1
    assert payload["riskLimits"]["maxDailyLossPct"] == 0.1


def test_build_payload_falls_back_to_status_without_runtime_market(monkeypatch):
    def fake_read_json(path):
        assert path == str(ai_sidecar.STATUS_PATH)
        return {
            "price": "120.0",
            "usdt": "80.0",
            "btc": "0.25",
            "position": {"entryPrice": "100.0"},
            "stats": {"closedTrades": 3, "maxDrawdownPct": 0.04},
        }

    monkeypatch.setattr(ai_sidecar, "_read_json", fake_read_json)

    payload = ai_sidecar._build_payload(
        {"symbol": "ETHUSDT", "maxTradesPerDay": "12.9"},
        {"market": {}, "grid": {"orders": [{"side": "BUY"}]}},
    )

    assert payload["symbol"] == "ETHUSDT"
    assert payload["price"] == 120.0
    assert payload["equityUsdt"] == 110.0
    assert payload["avgCost"] == 100.0
    assert payload["unrealizedPnlPct"] == pytest.approx(0.2)
    assert payload["closedTrades"] == 3
    assert payload["maxDrawdownPct"] == 0.04
    assert payload["openBuyOrders"] == 1
    assert payload["riskLimits"]["maxTradesPerDay"] == 12


def test_build_payload_returns_none_when_no_price(monkeypatch):
    monkeypatch.setattr(ai_sidecar, "_read_json", lambda path: {})

    assert ai_sidecar._build_payload({}, {"market": {}}) is None


def test_fallback_decision_is_not_stale_and_has_source():
    payload = {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "price": 100.0,
        "atrPct": 0.001,
        "trendStrength": 0.001,
    }
    cfg = {"provider": "local", "model": "m", "quick_model": "q", "deep_model": "d"}

    decision = ai_sidecar._fallback_decision(
        state={"gridMaxExposurePct": 0.35},
        payload=payload,
        cfg=cfg,
        started=0.0,
        error=RuntimeError("down"),
        persist=False,
    )

    assert decision["source"] == "deterministic_fallback"
    assert decision["enabled"] is True
    assert "stale" not in decision


def test_fallback_decision_persist_false_does_not_append(monkeypatch):
    appended = []
    monkeypatch.setattr(
        ai_sidecar,
        "append_decision",
        lambda *args, **kwargs: appended.append((args, kwargs)),
    )

    decision = ai_sidecar._fallback_decision(
        state={"gridMaxExposurePct": 0.35},
        payload={
            "symbol": "BTCUSDT",
            "interval": "1m",
            "price": 100.0,
            "atrPct": 0.001,
            "trendStrength": 0.01,
        },
        cfg={"provider": "local", "model": "m", "quick_model": "q", "deep_model": "d"},
        started=0.0,
        error=RuntimeError("model offline"),
        persist=False,
    )

    assert appended == []
    assert decision["source"] == "deterministic_fallback"
    assert decision["error"] == "model offline"
    assert decision["keyRisks"] == ["model offline"]


def test_synthetic_case_report_is_deterministic_for_bull_and_bear_cases():
    payload = {
        "trendStrength": 0.02,
        "atrPct": 0.01,
        "hasOpenPosition": True,
        "openOrders": 5,
    }

    bull = ai_sidecar._synthetic_case_report(
        "bull_case",
        payload,
        {"grid_risk": {}, "market_regime": {}},
    )
    bear = ai_sidecar._synthetic_case_report("bear_case", payload, {"bull_case": {}})

    assert bull["role"] == "bull_case"
    assert bull["recommendation"] == "reduce_exposure"
    assert bull["risk_score"] == pytest.approx(0.45)
    assert bull["raw"]["reportsSeen"] == ["grid_risk", "market_regime"]
    assert "hasOpenPosition=True" in bull["evidence"]

    assert bear["role"] == "bear_case"
    assert bear["recommendation"] == "sells_only"
    assert bear["risk_score"] == 1.0
    assert bear["raw"]["reportsSeen"] == ["bull_case"]
    assert "openOrders=5" in bear["evidence"]


def test_ensure_ai_enabled_raises_when_state_disables_ai(monkeypatch):
    monkeypatch.setattr(ai_sidecar, "_read_json", lambda path: {"aiEnabled": False})

    with pytest.raises(ai_sidecar.AiDisabled, match="AI assist disabled"):
        ai_sidecar._ensure_ai_enabled()


def test_ensure_ai_enabled_allows_enabled_state(monkeypatch):
    monkeypatch.setattr(ai_sidecar, "_read_json", lambda path: {"aiEnabled": True})

    ai_sidecar._ensure_ai_enabled()


def test_run_once_uses_persist_flag_and_write_signal_boundary(monkeypatch):
    state = {"symbol": "BTCUSDT"}
    runtime = {
        "market": {
            "price": 100.0,
            "candle": {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
            },
        }
    }
    calls = {}
    written = []

    def fake_read_json(path):
        if path == ai_sidecar.STATE_PATH:
            return state
        if path == ai_sidecar.RUNTIME_PATH:
            return runtime
        raise AssertionError(f"unexpected read: {path}")

    def fake_run_multi_agent_decision(read_state, payload, *, persist):
        calls["state"] = read_state
        calls["payload"] = payload
        calls["persist"] = persist
        return {"decisionId": "d1", "riskAction": "allow_grid"}

    monkeypatch.setattr(ai_sidecar, "_read_json", fake_read_json)
    monkeypatch.setattr(
        ai_sidecar,
        "_run_multi_agent_decision",
        fake_run_multi_agent_decision,
    )
    monkeypatch.setattr(
        ai_sidecar,
        "_write_ai_signal",
        lambda decision: written.append(decision),
    )

    decision = ai_sidecar._run_once(persist=False, write_signal=True)

    assert decision == {"decisionId": "d1", "riskAction": "allow_grid"}
    assert calls["state"] is state
    assert calls["persist"] is False
    assert calls["payload"]["price"] == 100.0
    assert written == [decision]
