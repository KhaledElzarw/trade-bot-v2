import os

import run_dashboard_detached
import run_engine_detached
import run_ai_sidecar_detached


def _assert_detached_popen_contract(module, calls, log_path, target):
    assert len(calls) == 1
    args, kwargs = calls[0]
    stdout = kwargs["stdout"]

    assert args == ["/custom/python", target]
    assert set(kwargs) == {
        "cwd",
        "stdin",
        "stdout",
        "stderr",
        "start_new_session",
        "close_fds",
        "env",
    }
    assert kwargs["cwd"] == str(module.BASE)
    assert kwargs["stdin"] == module.subprocess.DEVNULL
    assert stdout.name == str(log_path)
    assert kwargs["stderr"] == module.subprocess.STDOUT
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True
    assert isinstance(kwargs["env"], dict)
    assert kwargs["env"] is not os.environ
    assert kwargs["env"]["TRADEBOT_PYTHON"] == "/custom/python"
    assert kwargs["env"]["TRADEBOT_WRAPPER_TEST"] == "present"

    if not stdout.closed:
        stdout.close()


def _fail_popen(*args, **kwargs):
    raise AssertionError("Popen should not be called when a live PID is reused")


def test_wrapper_python_executable_defaults_to_venv(monkeypatch):
    monkeypatch.delenv("TRADEBOT_PYTHON", raising=False)

    assert run_engine_detached.get_python_executable() == run_engine_detached.PYTHON
    assert run_dashboard_detached.get_python_executable() == run_dashboard_detached.PYTHON
    assert run_ai_sidecar_detached.get_python_executable() == run_ai_sidecar_detached.PYTHON


def test_wrapper_python_executable_honors_override(monkeypatch):
    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")

    assert run_engine_detached.get_python_executable() == "/custom/python"
    assert run_dashboard_detached.get_python_executable() == "/custom/python"
    assert run_ai_sidecar_detached.get_python_executable() == "/custom/python"


def test_engine_start_uses_configurable_python(monkeypatch, tmp_path):
    calls = []

    class Proc:
        pid = 321

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    pid_path = tmp_path / "engine.pid"
    log_path = tmp_path / "engine.log"
    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")
    monkeypatch.setenv("TRADEBOT_WRAPPER_TEST", "present")
    monkeypatch.setattr(run_engine_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_engine_detached, "LOG_PATH", log_path)
    monkeypatch.setattr(run_engine_detached, "_all_engine_pids", lambda: [])
    monkeypatch.setattr(run_engine_detached, "_detached_engine_pids", lambda: [321])
    monkeypatch.setattr(run_engine_detached.subprocess, "Popen", fake_popen)

    assert run_engine_detached.start() == 321
    _assert_detached_popen_contract(
        run_engine_detached,
        calls,
        log_path,
        run_engine_detached.ENGINE,
    )
    assert pid_path.read_text(encoding="utf-8") == "321"


def test_dashboard_start_uses_configurable_python(monkeypatch, tmp_path):
    calls = []

    class Proc:
        pid = 654

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    pid_path = tmp_path / "dashboard.pid"
    log_path = tmp_path / "dashboard.log"
    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")
    monkeypatch.setenv("TRADEBOT_WRAPPER_TEST", "present")
    monkeypatch.setattr(run_dashboard_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_dashboard_detached, "LOG_PATH", log_path)
    monkeypatch.setattr(run_dashboard_detached, "_listening_pid_on_port", lambda: None)
    monkeypatch.setattr(run_dashboard_detached, "_live_dashboard_pids", lambda: [])
    monkeypatch.setattr(run_dashboard_detached.subprocess, "Popen", fake_popen)

    assert run_dashboard_detached.start() == 654
    _assert_detached_popen_contract(
        run_dashboard_detached,
        calls,
        log_path,
        run_dashboard_detached.DASHBOARD,
    )
    assert pid_path.read_text(encoding="utf-8") == "654"


def test_ai_sidecar_start_uses_configurable_python(monkeypatch, tmp_path):
    calls = []

    class Proc:
        pid = 987

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    pid_path = tmp_path / "ai_sidecar.pid"
    log_path = tmp_path / "ai_sidecar.log"
    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")
    monkeypatch.setenv("TRADEBOT_WRAPPER_TEST", "present")
    monkeypatch.setattr(run_ai_sidecar_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_ai_sidecar_detached, "LOG_PATH", log_path)
    monkeypatch.setattr(run_ai_sidecar_detached, "_live_pids", lambda: [])
    monkeypatch.setattr(run_ai_sidecar_detached.subprocess, "Popen", fake_popen)

    assert run_ai_sidecar_detached.start() == 987
    _assert_detached_popen_contract(
        run_ai_sidecar_detached,
        calls,
        log_path,
        run_ai_sidecar_detached.SIDECAR,
    )
    assert pid_path.read_text(encoding="utf-8") == "987"


def test_engine_start_reuses_live_pid_without_popen(monkeypatch, tmp_path):
    stopped = []
    pid_path = tmp_path / "engine.pid"

    monkeypatch.setattr(run_engine_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_engine_detached, "_all_engine_pids", lambda: [10, 20])
    monkeypatch.setattr(run_engine_detached, "_stop_pid", stopped.append)
    monkeypatch.setattr(run_engine_detached.subprocess, "Popen", _fail_popen)

    assert run_engine_detached.start() == 20
    assert stopped == [10]
    assert pid_path.read_text(encoding="utf-8") == "20"


def test_dashboard_start_reuses_live_pid_without_popen(monkeypatch, tmp_path):
    stopped = []
    pid_path = tmp_path / "dashboard.pid"

    monkeypatch.setattr(run_dashboard_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_dashboard_detached, "_listening_pid_on_port", lambda: None)
    monkeypatch.setattr(run_dashboard_detached, "_live_dashboard_pids", lambda: [10, 20])
    monkeypatch.setattr(run_dashboard_detached, "_stop_pid", stopped.append)
    monkeypatch.setattr(run_dashboard_detached.subprocess, "Popen", _fail_popen)

    assert run_dashboard_detached.start() == 20
    assert stopped == [10]
    assert pid_path.read_text(encoding="utf-8") == "20"


def test_ai_sidecar_start_reuses_live_pid_without_popen(monkeypatch, tmp_path):
    stopped = []
    pid_path = tmp_path / "ai_sidecar.pid"

    monkeypatch.setattr(run_ai_sidecar_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_ai_sidecar_detached, "_live_pids", lambda: [10, 20])
    monkeypatch.setattr(run_ai_sidecar_detached, "_stop_pid", stopped.append)
    monkeypatch.setattr(run_ai_sidecar_detached.subprocess, "Popen", _fail_popen)

    assert run_ai_sidecar_detached.start() == 20
    assert stopped == [10]
    assert pid_path.read_text(encoding="utf-8") == "20"


def test_engine_status_is_observational(monkeypatch, tmp_path):
    pid_path = tmp_path / "engine.pid"
    pid_path.write_text("1", encoding="utf-8")
    monkeypatch.setattr(run_engine_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_engine_detached, "_all_engine_pids", lambda: [10, 20])

    assert run_engine_detached.status() == 20
    assert pid_path.read_text(encoding="utf-8") == "1"


def test_dashboard_status_is_observational(monkeypatch, tmp_path):
    pid_path = tmp_path / "dashboard.pid"
    pid_path.write_text("1", encoding="utf-8")
    monkeypatch.setattr(run_dashboard_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_dashboard_detached, "_listening_pid_on_port", lambda: 99)
    monkeypatch.setattr(run_dashboard_detached, "_live_dashboard_pids", lambda: [10, 20])

    assert run_dashboard_detached.status() == 99
    assert pid_path.read_text(encoding="utf-8") == "1"


def test_ai_sidecar_status_is_observational(monkeypatch, tmp_path):
    pid_path = tmp_path / "ai_sidecar.pid"
    pid_path.write_text("1", encoding="utf-8")
    monkeypatch.setattr(run_ai_sidecar_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_ai_sidecar_detached, "_live_pids", lambda: [30, 40])

    assert run_ai_sidecar_detached.status() == 40
    assert pid_path.read_text(encoding="utf-8") == "1"


def test_wrapper_status_returns_zero_when_no_live_pid(monkeypatch):
    monkeypatch.setattr(run_engine_detached, "_all_engine_pids", lambda: [])
    monkeypatch.setattr(run_dashboard_detached, "_listening_pid_on_port", lambda: None)
    monkeypatch.setattr(run_dashboard_detached, "_live_dashboard_pids", lambda: [])
    monkeypatch.setattr(run_ai_sidecar_detached, "_live_pids", lambda: [])

    assert run_engine_detached.status() == 0
    assert run_dashboard_detached.status() == 0
    assert run_ai_sidecar_detached.status() == 0


def test_engine_stop_removes_pid_file_and_calls_stop_helper(monkeypatch, tmp_path):
    stopped = []
    pid_path = tmp_path / "engine.pid"
    pid_path.write_text("20", encoding="utf-8")

    monkeypatch.setattr(run_engine_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_engine_detached, "_all_engine_pids", lambda: [10, 20])
    monkeypatch.setattr(run_engine_detached, "_stop_pid", stopped.append)

    run_engine_detached.stop()

    assert stopped == [10, 20]
    assert not pid_path.exists()


def test_dashboard_stop_removes_pid_file_and_calls_stop_helper(monkeypatch, tmp_path):
    stopped = []
    pid_path = tmp_path / "dashboard.pid"
    pid_path.write_text("30", encoding="utf-8")

    monkeypatch.setattr(run_dashboard_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_dashboard_detached, "_live_dashboard_pids", lambda: [10, 20])
    monkeypatch.setattr(run_dashboard_detached, "_listening_pid_on_port", lambda: 30)
    monkeypatch.setattr(run_dashboard_detached, "_stop_pid", stopped.append)

    run_dashboard_detached.stop()

    assert stopped == [10, 20, 30]
    assert not pid_path.exists()


def test_ai_sidecar_stop_removes_pid_file_and_calls_stop_helper(monkeypatch, tmp_path):
    stopped = []
    pid_path = tmp_path / "ai_sidecar.pid"
    pid_path.write_text("40", encoding="utf-8")

    monkeypatch.setattr(run_ai_sidecar_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_ai_sidecar_detached, "_live_pids", lambda: [30, 40])
    monkeypatch.setattr(run_ai_sidecar_detached, "_stop_pid", stopped.append)

    run_ai_sidecar_detached.stop()

    assert stopped == [30, 40]
    assert not pid_path.exists()
