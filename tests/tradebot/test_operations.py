"""Process identity, logging, metrics, and tradebotctl tests (Phase 12).

Closes A15: no process is ever signalled without full identity verification.
No real processes are started or signalled anywhere in this suite.
"""

import json
import logging
import signal
from pathlib import Path

import pytest

from tradebot.cli.tradebotctl import Runtime, main
from tradebot.operations.process_identity import (
    IDENTITY_VERSION,
    IdentityMismatch,
    ProcessIdentity,
    new_instance_id,
    new_nonce,
    read_pid_file,
    stop_process,
    verify,
    write_pid_file,
)
from tradebot.operations.structured_logging import (
    JsonFormatter,
    Metrics,
    configure,
    redact,
)


def identity(pid=4242, start=1_000_000.0, **over) -> ProcessIdentity:
    base = dict(
        version=IDENTITY_VERSION, pid=pid, start_time=start,
        executable="/usr/bin/python3", command="python -m tradebot.api",
        service="api", instance_id="inst-1", nonce="nonce-1",
    )
    base.update(over)
    return ProcessIdentity(**base)


# On Windows there is no SIGKILL, so the supervisor's escalation signal falls
# back to SIGTERM. Tests therefore identify the escalation by call ORDER rather
# than by signal number, which is portable across both platforms.
ESCALATION_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


class FakeOs:
    """A fake process table. Records every signal so tests can assert on them."""

    def __init__(self, table=None, dies_on_term=True):
        self.table = table or {}
        self.signals = []
        self.dies_on_term = dies_on_term

    def probe(self, pid):
        return self.table.get(pid)

    def kill(self, pid, sig):
        self.signals.append((pid, sig))
        first_signal = len(self.signals) == 1
        if first_signal and not self.dies_on_term:
            return  # ignores the graceful stop; only escalation ends it
        self.table.pop(pid, None)

    def is_alive(self, pid):
        return pid in self.table


# ---- identity matching ------------------------------------------------------

def test_identical_identities_match():
    assert identity().matches(identity())


@pytest.mark.parametrize("field,value", [
    ("pid", 9999),
    ("start_time", 2_000_000.0),
    ("executable", "/tmp/evil"),
    ("command", "python -m something.else"),
    ("service", "other"),
    ("instance_id", "inst-2"),
    ("nonce", "nonce-2"),
])
def test_any_field_difference_breaks_match(field, value):
    assert not identity().matches(identity(**{field: value}))


def test_start_time_tolerance():
    assert identity(start=1_000_000.0).matches(identity(start=1_000_000.4))
    assert not identity(start=1_000_000.0).matches(identity(start=1_000_003.0))


# ---- A15: never kill the wrong process -------------------------------------

def test_verify_raises_when_not_running():
    with pytest.raises(IdentityMismatch, match="not running"):
        verify(identity(), lambda pid: None)


def test_verify_raises_on_pid_reuse():
    """Same PID, different start time => a DIFFERENT process. Never signal it."""
    recorded = identity(pid=4242, start=1_000_000.0)
    recycled = identity(pid=4242, start=1_700_000.0, command="/usr/bin/postgres")
    with pytest.raises(IdentityMismatch, match="PID reuse"):
        verify(recorded, lambda pid: recycled)


def test_stop_refuses_to_kill_recycled_pid():
    recorded = identity(pid=4242, start=1_000_000.0)
    recycled = identity(pid=4242, start=1_700_000.0, command="/usr/bin/postgres",
                        service="postgres")
    os_fake = FakeOs({4242: recycled})
    outcome = stop_process(recorded, os_fake.probe, os_fake.kill,
                           os_fake.is_alive, lambda s: None)
    assert outcome.stopped is False
    assert "does not match" in outcome.reason
    assert os_fake.signals == []  # NOTHING was signalled


def test_stop_on_stale_pid_file_signals_nothing():
    os_fake = FakeOs({})  # process long gone
    outcome = stop_process(identity(), os_fake.probe, os_fake.kill,
                           os_fake.is_alive, lambda s: None)
    assert outcome.stopped is False
    assert "not running" in outcome.reason
    assert os_fake.signals == []


def test_graceful_stop_uses_sigterm_only():
    rec = identity()
    os_fake = FakeOs({rec.pid: rec}, dies_on_term=True)
    outcome = stop_process(rec, os_fake.probe, os_fake.kill, os_fake.is_alive,
                           lambda s: None)
    assert outcome.stopped is True
    assert outcome.escalated is False
    assert os_fake.signals == [(rec.pid, signal.SIGTERM)]


def test_bounded_escalation_reverifies_before_sigkill():
    rec = identity()
    os_fake = FakeOs({rec.pid: rec}, dies_on_term=False)  # ignores SIGTERM
    outcome = stop_process(rec, os_fake.probe, os_fake.kill, os_fake.is_alive,
                           lambda s: None, grace_seconds=1.0, poll_interval=0.5)
    assert outcome.stopped is True
    assert outcome.escalated is True
    sigs = [s for _, s in os_fake.signals]
    assert len(sigs) == 2, "graceful attempt then exactly one escalation"
    assert sigs[0] == signal.SIGTERM
    assert sigs[1] == ESCALATION_SIGNAL


def test_escalation_aborts_if_pid_recycled_during_grace_window():
    """The target exits during grace and its PID is reused -> no SIGKILL."""
    rec = identity(pid=4242, start=1_000_000.0)
    os_fake = FakeOs({4242: rec}, dies_on_term=False)

    def probe(pid):
        # After the SIGTERM was sent, the PID now belongs to something else.
        if os_fake.signals:
            return identity(pid=4242, start=1_900_000.0, service="postgres")
        return os_fake.table.get(pid)

    outcome = stop_process(rec, probe, os_fake.kill, os_fake.is_alive,
                           lambda s: None, grace_seconds=1.0, poll_interval=0.5)
    assert outcome.stopped is True
    assert outcome.escalated is False
    assert [s for _, s in os_fake.signals] == [signal.SIGTERM]  # no SIGKILL


def test_survives_escalation_reported_honestly():
    rec = identity()

    class Immortal(FakeOs):
        def kill(self, pid, sig):
            self.signals.append((pid, sig))  # never dies

    os_fake = Immortal({rec.pid: rec})
    outcome = stop_process(rec, os_fake.probe, os_fake.kill, os_fake.is_alive,
                           lambda s: None, grace_seconds=1.0, poll_interval=0.5)
    assert outcome.stopped is False
    assert outcome.escalated is True
    assert "survived" in outcome.reason


# ---- pid file ---------------------------------------------------------------

def test_pid_file_round_trip(tmp_path):
    path = tmp_path / "api.pid.json"
    rec = identity()
    write_pid_file(path, rec)
    assert read_pid_file(path) == rec


def test_pid_file_missing_corrupt_or_wrong_version(tmp_path):
    assert read_pid_file(tmp_path / "absent.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert read_pid_file(bad) is None
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"version": "v0", "pid": 1}), encoding="utf-8")
    assert read_pid_file(old) is None
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({"version": IDENTITY_VERSION, "pid": 1}),
                       encoding="utf-8")
    assert read_pid_file(partial) is None


def test_ids_are_unique():
    assert new_instance_id() != new_instance_id()
    assert new_nonce() != new_nonce()


# ---- structured logging -----------------------------------------------------

def test_json_formatter_includes_context_fields():
    record = logging.LogRecord("tradebot", logging.INFO, __file__, 1,
                               "tick complete", None, None)
    record.correlation_id = "abc123"
    record.wallet_id = "w-1"
    record.strategy_version_id = "sv-1"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["severity"] == "INFO"
    assert payload["correlation_id"] == "abc123"
    assert payload["wallet_id"] == "w-1"
    assert payload["schema"] == "log-v1"


def test_secrets_are_redacted():
    assert redact("hunter2", "api_key") == "***redacted***"
    assert redact("hunter2", "Authorization") == "***redacted***"
    nested = redact({"token": "abc", "wallet_id": "w1",
                     "inner": {"password": "p", "ok": "v"}})
    assert nested["token"] == "***redacted***"
    assert nested["wallet_id"] == "w1"
    assert nested["inner"]["password"] == "***redacted***"
    assert nested["inner"]["ok"] == "v"
    assert redact(["x", {"secret": "s"}])[1]["secret"] == "***redacted***"


def test_long_external_content_truncated():
    out = redact("A" * 5000)
    assert len(out) < 600
    assert "(+4488)" in out


def test_exception_logs_category_not_traceback_text():
    try:
        raise ValueError("db at /srv/secret")
    except ValueError:
        import sys
        record = logging.LogRecord("tradebot", logging.ERROR, __file__, 1,
                                   "failed", None, sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert payload["error_category"] == "ValueError"


def test_configure_installs_json_handler():
    handler = configure()
    assert isinstance(handler.formatter, JsonFormatter)
    assert logging.getLogger("tradebot").propagate is False


# ---- metrics ----------------------------------------------------------------

def test_metrics_counters_and_gauges():
    m = Metrics()
    m.increment("quarantines_total")
    m.increment("quarantines_total", 2)
    m.observe("active_wallet_count", 12)
    m.observe("shadow_wallet_count", 12)
    assert m.counter("quarantines_total") == 3.0
    assert m.gauge("active_wallet_count") == 12
    assert m.gauge("llm_health") is None
    assert len(m.snapshot()) == 3


def test_metrics_rejects_unknown_name():
    with pytest.raises(ValueError, match="unknown metric"):
        Metrics().increment("made_up_metric")


def test_rejected_intents_metric_supports_reason_labels():
    m = Metrics()
    m.increment("rejected_intents_total", label="min_notional")
    m.increment("rejected_intents_total", label="duplicate_candle")
    assert m.counter("rejected_intents_total", "min_notional") == 1.0
    assert m.counter("rejected_intents_total", "duplicate_candle") == 1.0


# ---- tradebotctl ------------------------------------------------------------

def runtime(tmp_path: Path, **kw) -> Runtime:
    return Runtime(pid_dir=tmp_path, **kw)


def capture():
    lines = []
    return lines, lines.append


def test_status_reports_stopped_running_and_pid_reuse(tmp_path):
    rec = identity(service="api")
    write_pid_file(tmp_path / "api.pid.json", rec)
    write_pid_file(tmp_path / "portfolio-engine.pid.json",
                   identity(pid=7, service="portfolio-engine"))

    def probe(pid):
        if pid == rec.pid:
            return rec
        if pid == 7:
            return identity(pid=7, start=9_000_000.0, service="postgres")
        return None

    lines, out = capture()
    assert main(["status"], runtime(tmp_path, probe=probe, out=out)) == 0
    services = json.loads(lines[0])["services"]
    assert services["api"]["state"] == "running"
    assert services["portfolio-engine"]["state"] == "unknown"  # never "running"
    assert "reused" in services["portfolio-engine"]["reason"]
    assert services["evolution-worker"]["state"] == "stopped"


def test_stop_refuses_mismatched_process_and_exits_nonzero(tmp_path):
    rec = identity(service="api")
    write_pid_file(tmp_path / "api.pid.json", rec)
    other = identity(pid=rec.pid, start=9_000_000.0, service="postgres")
    os_fake = FakeOs({rec.pid: other})
    lines, out = capture()
    code = main(["stop", "--service", "api"],
                runtime(tmp_path, probe=os_fake.probe, kill=os_fake.kill,
                        is_alive=os_fake.is_alive, out=out))
    assert code == 1
    assert os_fake.signals == []
    assert (tmp_path / "api.pid.json").exists()  # pid file preserved


def test_stop_removes_pid_file_on_success(tmp_path):
    rec = identity(service="api")
    write_pid_file(tmp_path / "api.pid.json", rec)
    os_fake = FakeOs({rec.pid: rec})
    lines, out = capture()
    assert main(["stop", "--service", "api"],
                runtime(tmp_path, probe=os_fake.probe, kill=os_fake.kill,
                        is_alive=os_fake.is_alive, out=out)) == 0
    assert not (tmp_path / "api.pid.json").exists()


def test_stop_without_pid_file(tmp_path):
    lines, out = capture()
    assert main(["stop", "--service", "api"], runtime(tmp_path, out=out)) == 0
    assert json.loads(lines[0])["stopped"]["api"]["reason"] == "no pid file"


def test_start_is_idempotent_for_running_service(tmp_path):
    rec = identity(service="api")
    write_pid_file(tmp_path / "api.pid.json", rec)
    started = []
    lines, out = capture()
    main(["start", "--service", "api"],
         runtime(tmp_path, probe=lambda pid: rec,
                 start_service=lambda s: started.append(s), out=out))
    assert started == []  # not restarted
    assert json.loads(lines[0])["started"]["api"]["state"] == "already running"


def test_start_launches_stopped_service(tmp_path):
    lines, out = capture()
    new = identity(pid=555, service="api", instance_id="inst-new")
    main(["start", "--service", "api"],
         runtime(tmp_path, probe=lambda pid: None,
                 start_service=lambda s: new, out=out))
    body = json.loads(lines[0])["started"]["api"]
    assert body["state"] == "started" and body["pid"] == 555


def test_start_without_adapter_reports_error(tmp_path):
    lines, out = capture()
    main(["start", "--service", "api"],
         runtime(tmp_path, probe=lambda pid: None, out=out))
    assert json.loads(lines[0])["started"]["api"]["state"] == "error"


def test_restart_stops_then_starts(tmp_path):
    rec = identity(service="api")
    write_pid_file(tmp_path / "api.pid.json", rec)
    os_fake = FakeOs({rec.pid: rec})
    lines, out = capture()
    code = main(["restart", "--service", "api"],
                runtime(tmp_path, probe=os_fake.probe, kill=os_fake.kill,
                        is_alive=os_fake.is_alive,
                        start_service=lambda s: identity(pid=999), out=out))
    assert code == 0
    assert json.loads(lines[1])["started"]["api"]["state"] == "started"


def test_restart_aborts_when_stop_refuses(tmp_path):
    """A mismatched process must block restart, not get killed."""
    rec = identity(service="api")
    write_pid_file(tmp_path / "api.pid.json", rec)
    other = identity(pid=rec.pid, start=9_000_000.0)
    os_fake = FakeOs({rec.pid: other})
    started = []
    lines, out = capture()
    code = main(["restart", "--service", "api"],
                runtime(tmp_path, probe=os_fake.probe, kill=os_fake.kill,
                        is_alive=os_fake.is_alive,
                        start_service=lambda s: started.append(s), out=out))
    assert code == 1
    assert started == []
    assert os_fake.signals == []


def test_doctor_healthy_and_unhealthy(tmp_path):
    lines, out = capture()
    ok_checks = [lambda: ("database", True, "ok"),
                 lambda: ("market_data", True, "fresh")]
    assert main(["doctor"], runtime(tmp_path, checks=ok_checks, out=out)) == 0
    assert json.loads(lines[0])["healthy"] is True

    lines2, out2 = capture()
    bad = ok_checks + [lambda: ("local_model", False, "unreachable")]
    assert main(["doctor"], runtime(tmp_path, checks=bad, out=out2)) == 1
    body = json.loads(lines2[0])
    assert body["healthy"] is False
    assert body["checks"][2]["ok"] is False


def test_migrate_and_seed_support_dry_run(tmp_path):
    calls = []
    lines, out = capture()
    rt = runtime(tmp_path, out=out,
                 migrate=lambda dry: calls.append(("migrate", dry)) or {"ok": True},
                 seed=lambda dry: calls.append(("seed", dry)) or {"ok": True})
    assert main(["migrate", "--dry-run"], rt) == 0
    assert main(["seed"], rt) == 0
    assert calls == [("migrate", True), ("seed", False)]


def test_migrate_and_seed_without_adapters_error(tmp_path):
    lines, out = capture()
    assert main(["migrate"], runtime(tmp_path, out=out)) == 1
    assert main(["seed"], runtime(tmp_path, out=out)) == 1


def test_reviews_pass_window_and_force(tmp_path):
    calls = []
    lines, out = capture()
    rt = runtime(tmp_path, out=out,
                 daily_review=lambda d, f: calls.append((d, f)) or {"ok": True},
                 weekly_review=lambda w, f: calls.append((w, f)) or {"ok": True})
    assert main(["run-daily-review", "--date", "2026-07-16"], rt) == 0
    assert main(["run-weekly-review", "--window", "2026-W29", "--force"], rt) == 0
    assert calls == [("2026-07-16", False), ("2026-W29", True)]


def test_reviews_without_adapters_error(tmp_path):
    lines, out = capture()
    assert main(["run-daily-review", "--date", "x"], runtime(tmp_path, out=out)) == 1
    assert main(["run-weekly-review", "--window", "x"],
                runtime(tmp_path, out=out)) == 1


def test_validate_and_replay_exit_codes(tmp_path):
    lines, out = capture()
    ok_rt = runtime(tmp_path, out=out,
                    validate_strategy=lambda p: {"ok": True},
                    replay_strategy=lambda p, n: {"ok": True, "fills": 3})
    assert main(["validate-strategy", "--bundle", "b"], ok_rt) == 0
    assert main(["replay-strategy", "--bundle", "b", "--candles", "10"], ok_rt) == 0

    bad_rt = runtime(tmp_path, out=out,
                     validate_strategy=lambda p: {"ok": False, "errors": ["x"]},
                     replay_strategy=lambda p, n: {"ok": False})
    assert main(["validate-strategy", "--bundle", "b"], bad_rt) == 1
    assert main(["replay-strategy", "--bundle", "b"], bad_rt) == 1


def test_validate_and_replay_without_adapters_error(tmp_path):
    lines, out = capture()
    assert main(["validate-strategy", "--bundle", "b"],
                runtime(tmp_path, out=out)) == 1
    assert main(["replay-strategy", "--bundle", "b"],
                runtime(tmp_path, out=out)) == 1


def test_all_required_commands_exist():
    from tradebot.cli.tradebotctl import COMMANDS
    for name in ("start", "stop", "restart", "status", "doctor", "migrate",
                 "seed", "run-daily-review", "run-weekly-review",
                 "validate-strategy", "replay-strategy"):
        assert name in COMMANDS
