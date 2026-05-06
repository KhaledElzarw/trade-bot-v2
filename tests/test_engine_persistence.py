import json
from datetime import datetime, timezone

import pytest

import engine


def test_atomic_json_write_creates_parent_and_replaces_tmp(tmp_path):
    target = tmp_path / "nested" / "state.json"

    engine._atomic_json_write(str(target), {"b": 2, "a": 1})

    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": 2}
    assert not target.with_name("state.json.tmp").exists()


def test_atomic_json_write_raises_when_tmp_disappears(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    real_exists = engine.os.path.exists

    def fake_exists(path):
        if path == str(target) + ".tmp":
            return False
        return real_exists(path)

    monkeypatch.setattr(engine.os.path, "exists", fake_exists)

    with pytest.raises(FileNotFoundError, match="temporary write path missing"):
        engine._atomic_json_write(str(target), {"ok": True})


def test_read_json_prefers_sqlite_snapshot(monkeypatch, tmp_path):
    mirror = tmp_path / "state.json"
    mirror.write_text(json.dumps({"source": "file"}), encoding="utf-8")

    monkeypatch.setattr(engine.sqlite_store, "snapshot_key_for_path", lambda path: "state")
    monkeypatch.setattr(engine.sqlite_store, "has_snapshot", lambda key: key == "state")
    monkeypatch.setattr(engine.sqlite_store, "read_snapshot", lambda key: {"source": key})

    assert engine._read_json(str(mirror)) == {"source": "state"}


def test_read_json_falls_back_to_file(monkeypatch, tmp_path):
    mirror = tmp_path / "custom.json"
    mirror.write_text(json.dumps({"source": "file"}), encoding="utf-8")
    monkeypatch.setattr(engine.sqlite_store, "snapshot_key_for_path", lambda path: None)

    assert engine._read_json(str(mirror)) == {"source": "file"}


def test_write_json_writes_sqlite_snapshot_and_file_mirror(monkeypatch, tmp_path):
    mirror = tmp_path / "state.json"
    writes = []

    monkeypatch.setattr(engine.sqlite_store, "snapshot_key_for_path", lambda path: "state")
    monkeypatch.setattr(engine.sqlite_store, "write_snapshot", lambda key, payload: writes.append((key, payload)))

    engine._write_json(str(mirror), {"paused": False, "count": 2})

    assert writes == [("state", {"paused": False, "count": 2})]
    assert json.loads(mirror.read_text(encoding="utf-8")) == {"count": 2, "paused": False}


def test_safe_read_json_returns_empty_on_failure(monkeypatch):
    monkeypatch.setattr(engine, "_read_json", lambda path: (_ for _ in ()).throw(ValueError("bad json")))

    assert engine._safe_read_json("missing.json") == {}


def test_pid_alive_handles_invalid_alive_and_dead_paths(monkeypatch):
    kill_calls = []

    def fake_kill(pid, signal):
        kill_calls.append((pid, signal))
        if pid == 456:
            raise ProcessLookupError("missing")

    monkeypatch.setattr(engine.os, "kill", fake_kill)

    assert engine._pid_alive(None) is False
    assert engine._pid_alive(0) is False
    assert engine._pid_alive(-1) is False
    assert engine._pid_alive(123) is True
    assert engine._pid_alive(456) is False
    assert kill_calls == [(123, 0), (456, 0)]


def test_acquire_engine_lock_writes_fresh_runtime_state(monkeypatch):
    fixed_now = datetime(2026, 5, 5, 12, 30, tzinfo=timezone.utc)
    writes = []

    def fail_pid_alive(pid):
        raise AssertionError(f"unexpected pid check for {pid}")

    monkeypatch.setattr(engine.os, "getpid", lambda: 222)
    monkeypatch.setattr(engine, "_safe_read_json", lambda path: {})
    monkeypatch.setattr(engine, "_pid_alive", fail_pid_alive)
    monkeypatch.setattr(engine, "_utc_now", lambda: fixed_now)
    monkeypatch.setattr(engine, "_write_runtime_state", lambda payload: writes.append(dict(payload)))

    assert engine._acquire_engine_lock() == (222, True)
    assert writes == [
        {
            "enginePid": 222,
            "engineStartedAt": "2026-05-05T12:30:00+00:00",
            "savedAt": "2026-05-05T12:30:00+00:00",
        }
    ]


def test_acquire_engine_lock_preserves_existing_start_for_same_pid(monkeypatch):
    fixed_now = datetime(2026, 5, 5, 12, 30, tzinfo=timezone.utc)
    writes = []

    monkeypatch.setattr(engine.os, "getpid", lambda: 222)
    monkeypatch.setattr(
        engine,
        "_safe_read_json",
        lambda path: {"enginePid": 222, "engineStartedAt": "already-started"},
    )
    monkeypatch.setattr(engine, "_utc_now", lambda: fixed_now)
    monkeypatch.setattr(engine, "_write_runtime_state", lambda payload: writes.append(dict(payload)))

    assert engine._acquire_engine_lock() == (222, False)
    assert writes == [
        {
            "enginePid": 222,
            "engineStartedAt": "already-started",
            "savedAt": "2026-05-05T12:30:00+00:00",
        }
    ]


def test_acquire_engine_lock_takes_over_stale_owner(monkeypatch):
    fixed_now = datetime(2026, 5, 5, 12, 30, tzinfo=timezone.utc)
    writes = []

    monkeypatch.setattr(engine.os, "getpid", lambda: 222)
    monkeypatch.setattr(engine, "_safe_read_json", lambda path: {"enginePid": 111})
    monkeypatch.setattr(engine, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(engine, "_utc_now", lambda: fixed_now)
    monkeypatch.setattr(engine, "_write_runtime_state", lambda payload: writes.append(dict(payload)))

    assert engine._acquire_engine_lock() == (222, True)
    assert writes == [
        {
            "enginePid": 222,
            "engineStartedAt": "2026-05-05T12:30:00+00:00",
            "savedAt": "2026-05-05T12:30:00+00:00",
        }
    ]


def test_acquire_engine_lock_raises_for_different_live_owner(monkeypatch):
    writes = []

    monkeypatch.setattr(engine.os, "getpid", lambda: 222)
    monkeypatch.setattr(engine, "_safe_read_json", lambda path: {"enginePid": 111})
    monkeypatch.setattr(engine, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(engine, "_write_runtime_state", lambda payload: writes.append(dict(payload)))

    with pytest.raises(RuntimeError, match="Engine already running with pid 111"):
        engine._acquire_engine_lock()

    assert writes == []


def test_log_writes_timestamped_line_to_patched_path(tmp_path, monkeypatch, capsys):
    log_path = tmp_path / "engine.log"
    monkeypatch.setattr(engine, "LOG_PATH", str(log_path))
    monkeypatch.setattr(engine, "_utc_now", lambda: datetime(2026, 5, 5, 12, 30, tzinfo=timezone.utc))

    engine._log("RUNTIME_STATE_WRITE reason=forced")

    expected = "[2026-05-05 12:30:00 UTC] RUNTIME_STATE_WRITE reason=forced"
    assert capsys.readouterr().out.strip() == expected
    assert log_path.read_text(encoding="utf-8") == expected + "\n"


def test_write_status_writes_snapshot_and_patched_status_file(monkeypatch, tmp_path):
    status_path = tmp_path / "engine_status.json"
    writes = []

    monkeypatch.setattr(engine, "STATUS_PATH", str(status_path))
    monkeypatch.setattr(engine.sqlite_store, "write_snapshot", lambda key, payload: writes.append((key, payload)))

    engine._write_status({"lastEvent": "TICK", "price": 100.0})

    assert writes == [("engine_status", {"lastEvent": "TICK", "price": 100.0})]
    assert json.loads(status_path.read_text(encoding="utf-8")) == {"lastEvent": "TICK", "price": 100.0}


def test_append_trade_writes_sqlite_only_for_default_path_and_jsonl_for_both(monkeypatch, tmp_path):
    default_path = tmp_path / "default_trades.jsonl"
    custom_path = tmp_path / "custom_trades.jsonl"
    appended = []

    monkeypatch.setattr(engine.sqlite_store, "append_event", lambda event: appended.append(dict(event)))
    monkeypatch.setattr(engine, "DEFAULT_TRADES_PATH", str(default_path))
    monkeypatch.setattr(engine, "TRADES_PATH", str(default_path))

    engine._append_trade({"event": "ENTER", "qtyBtc": 0.1})

    monkeypatch.setattr(engine, "TRADES_PATH", str(custom_path))
    engine._append_trade({"event": "EXIT", "qtyBtc": 0.05})

    assert appended == [{"event": "ENTER", "qtyBtc": 0.1}]
    assert default_path.read_text(encoding="utf-8") == '{"event": "ENTER", "qtyBtc": 0.1}\n'
    assert custom_path.read_text(encoding="utf-8") == '{"event": "EXIT", "qtyBtc": 0.05}\n'


def test_read_cum_prefers_sqlite_snapshot_and_normalizes(monkeypatch):
    monkeypatch.setattr(engine.sqlite_store, "has_snapshot", lambda key: key == "cumulative")
    monkeypatch.setattr(
        engine.sqlite_store,
        "read_snapshot",
        lambda key: {
            "grossRealizedPnlUsdt": "10.5",
            "feesPaidUsdt": "0.5",
            "trades": "2",
            "wins": "1",
            "losses": None,
        },
    )

    assert engine._read_cum() == {
        "grossRealizedPnlUsdt": 10.5,
        "feesPaidUsdt": 0.5,
        "realizedPnlUsdt": 10.0,
        "trades": 2,
        "wins": 1,
        "losses": 0,
    }


def test_read_cum_returns_default_for_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(engine.sqlite_store, "has_snapshot", lambda key: False)
    monkeypatch.setattr(engine, "CUM_PATH", str(tmp_path / "missing_cumulative.json"))

    assert engine._read_cum() == {
        "sinceUtc": None,
        "realizedPnlUsdt": 0.0,
        "grossRealizedPnlUsdt": 0.0,
        "feesPaidUsdt": 0.0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
    }


def test_read_cum_derives_legacy_gross_realized_from_file(monkeypatch, tmp_path):
    cum_path = tmp_path / "cumulative.json"
    cum_path.write_text(
        json.dumps({
            "realizedPnlUsdt": 9.0,
            "feesPaidUsdt": 1.0,
            "trades": 3,
            "wins": 2,
            "losses": 1,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(engine.sqlite_store, "has_snapshot", lambda key: False)
    monkeypatch.setattr(engine, "CUM_PATH", str(cum_path))

    assert engine._read_cum() == {
        "realizedPnlUsdt": 9.0,
        "feesPaidUsdt": 1.0,
        "trades": 3,
        "wins": 2,
        "losses": 1,
        "grossRealizedPnlUsdt": 10.0,
    }


def test_write_cum_normalizes_sqlite_snapshot_and_file(monkeypatch, tmp_path):
    cum_path = tmp_path / "cumulative.json"
    writes = []

    monkeypatch.setattr(engine, "CUM_PATH", str(cum_path))
    monkeypatch.setattr(engine.sqlite_store, "write_snapshot", lambda key, payload: writes.append((key, dict(payload))))

    engine._write_cum({"grossRealizedPnlUsdt": "7.5", "feesPaidUsdt": "0.5", "trades": "1"})

    expected = {
        "grossRealizedPnlUsdt": 7.5,
        "feesPaidUsdt": 0.5,
        "trades": 1,
        "realizedPnlUsdt": 7.0,
        "wins": 0,
        "losses": 0,
    }
    assert writes == [("cumulative", expected)]
    assert json.loads(cum_path.read_text(encoding="utf-8")) == expected


def test_runtime_state_read_write_uses_sqlite_and_tmp_file_branches(monkeypatch, tmp_path):
    runtime_path = tmp_path / "runtime_state.json"
    snapshot_writes = []

    monkeypatch.setattr(engine, "RUNTIME_PATH", str(runtime_path))
    monkeypatch.setattr(engine.sqlite_store, "has_snapshot", lambda key: key == "runtime_state")
    monkeypatch.setattr(engine.sqlite_store, "read_snapshot", lambda key: {"enginePid": 123})
    monkeypatch.setattr(
        engine.sqlite_store,
        "write_snapshot",
        lambda key, payload: snapshot_writes.append((key, dict(payload))),
    )

    assert engine._read_runtime_state() == {"enginePid": 123}

    monkeypatch.setattr(engine.sqlite_store, "has_snapshot", lambda key: False)
    assert engine._read_runtime_state() == {}

    runtime_path.write_text(json.dumps({"enginePid": 456}), encoding="utf-8")
    assert engine._read_runtime_state() == {"enginePid": 456}

    engine._write_runtime_state({"enginePid": 789, "savedAt": "now"})

    assert snapshot_writes == [("runtime_state", {"enginePid": 789, "savedAt": "now"})]
    assert json.loads(runtime_path.read_text(encoding="utf-8")) == {"enginePid": 789, "savedAt": "now"}


def test_ai_signal_read_write_covers_sqlite_missing_invalid_valid_and_write(monkeypatch, tmp_path):
    signal_path = tmp_path / "ai_signal.json"
    snapshot_writes = []

    monkeypatch.setattr(engine, "AI_SIGNAL_PATH", str(signal_path))
    monkeypatch.setattr(engine.sqlite_store, "has_snapshot", lambda key: key == "ai_signal")
    monkeypatch.setattr(engine.sqlite_store, "read_snapshot", lambda key: {"enabled": True, "source": "sqlite"})
    monkeypatch.setattr(
        engine.sqlite_store,
        "write_snapshot",
        lambda key, payload: snapshot_writes.append((key, dict(payload))),
    )

    assert engine._read_ai_signal() == {"enabled": True, "source": "sqlite"}

    monkeypatch.setattr(engine.sqlite_store, "has_snapshot", lambda key: False)
    assert engine._read_ai_signal() == {}

    signal_path.write_text("{", encoding="utf-8")
    assert engine._read_ai_signal() == {}

    signal_path.write_text(json.dumps({"enabled": True, "confidence": 0.75}), encoding="utf-8")
    assert engine._read_ai_signal() == {"enabled": True, "confidence": 0.75}

    engine._write_ai_signal({"enabled": False, "source": "test"})

    assert snapshot_writes == [("ai_signal", {"enabled": False, "source": "test"})]
    assert json.loads(signal_path.read_text(encoding="utf-8")) == {"enabled": False, "source": "test"}


def test_load_trade_events_prefers_sqlite_rows_and_handles_missing_or_bad_jsonl(monkeypatch, tmp_path):
    trades_path = tmp_path / "trades.jsonl"

    monkeypatch.setattr(engine, "DEFAULT_TRADES_PATH", str(trades_path))
    monkeypatch.setattr(engine, "TRADES_PATH", str(trades_path))
    monkeypatch.setattr(engine.sqlite_store, "list_events", lambda: [{"event": "SQLITE"}])

    assert engine._load_trade_events() == [{"event": "SQLITE"}]

    monkeypatch.setattr(engine.sqlite_store, "list_events", lambda: [])
    assert engine._load_trade_events() == []

    trades_path.write_text(
        "\n".join([
            json.dumps({"event": "ENTER", "qtyBtc": 0.1}),
            "",
            "{bad-json",
            json.dumps({"event": "EXIT", "qtyBtc": 0.05}),
        ])
        + "\n",
        encoding="utf-8",
    )

    assert engine._load_trade_events() == [
        {"event": "ENTER", "qtyBtc": 0.1},
        {"event": "EXIT", "qtyBtc": 0.05},
    ]


def test_maybe_write_runtime_state_forced_persists_and_throttled_skips(monkeypatch):
    writes = []
    logs = []
    times = iter([1.0, 2.0])
    runtime_payload = {
        "enginePid": 123,
        "paper": {"usdt": 100.0, "btc": 0.0},
        "stats": {"trades": 0},
        "market": {"price": 100.0, "candle": {"openTimeMs": 1, "closeTimeMs": 2}},
        "ai": {"model": "test-model"},
        "savedAt": "2026-05-05T12:30:00+00:00",
    }
    gate = engine._SnapshotChangeGate(min_interval_seconds=10, max_interval_seconds=60, market_change_bps=5)

    monkeypatch.setattr(engine.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(engine, "_write_runtime_state", lambda payload: writes.append(dict(payload)))
    monkeypatch.setattr(engine, "_log", logs.append)

    assert engine._maybe_write_runtime_state(gate, runtime_payload, force=True) == (True, "forced")
    assert writes == [runtime_payload]
    assert logs == [
        "RUNTIME_STATE_WRITE reason=forced savedAt=2026-05-05T12:30:00+00:00 "
        "ai_model=test-model has_ai=True"
    ]

    assert engine._maybe_write_runtime_state(gate, runtime_payload) == (False, "throttled")
    assert writes == [runtime_payload]
    assert len(logs) == 1
