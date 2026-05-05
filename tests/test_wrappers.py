import builtins
import io
import os
import subprocess
import sys

import pytest

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


def test_engine_live_pids_parse_pgrep_and_exclude_current_pid(monkeypatch):
    def fake_check_output(args, text):
        assert args == ["pgrep", "-f", run_engine_detached.ENGINE]
        assert text is True
        return "100\n200\n\n300\n"

    monkeypatch.setattr(run_engine_detached.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(run_engine_detached.os, "getpid", lambda: 200)

    assert run_engine_detached._live_engine_pids() == [100, 300]


def test_dashboard_live_pids_parse_pgrep(monkeypatch):
    def fake_check_output(args, text):
        assert args == ["pgrep", "-f", run_dashboard_detached.DASHBOARD]
        assert text is True
        return "100\n200\n\n"

    monkeypatch.setattr(run_dashboard_detached.subprocess, "check_output", fake_check_output)

    assert run_dashboard_detached._live_dashboard_pids() == [100, 200]


def test_ai_sidecar_live_pids_parse_pgrep_and_exclude_current_pid(monkeypatch):
    def fake_check_output(args, text):
        assert args == ["pgrep", "-f", run_ai_sidecar_detached.SIDECAR]
        assert text is True
        return "100\n200\n\n300\n"

    monkeypatch.setattr(run_ai_sidecar_detached.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(run_ai_sidecar_detached.os, "getpid", lambda: 200)

    assert run_ai_sidecar_detached._live_pids() == [100, 300]


@pytest.mark.parametrize(
    ("module", "function_name"),
    [
        (run_engine_detached, "_live_engine_pids"),
        (run_dashboard_detached, "_live_dashboard_pids"),
        (run_ai_sidecar_detached, "_live_pids"),
    ],
)
def test_live_pid_discovery_returns_empty_on_pgrep_failure(monkeypatch, module, function_name):
    def fake_check_output(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(module.subprocess, "check_output", fake_check_output)

    assert getattr(module, function_name)() == []


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ("123 (python) S 1 2 3\n", 1),
        ("bad\n", None),
    ],
)
def test_engine_pid_ppid_reads_proc_stat(monkeypatch, contents, expected):
    opened = []

    def fake_open(path, mode="r", encoding=None):
        opened.append((path, mode, encoding))
        return io.StringIO(contents)

    monkeypatch.setattr(builtins, "open", fake_open)

    assert run_engine_detached._pid_ppid(123) == expected
    assert opened == [("/proc/123/stat", "r", "utf-8")]


def test_engine_pid_ppid_returns_none_when_proc_read_fails(monkeypatch):
    def fake_open(*args, **kwargs):
        raise OSError("missing")

    monkeypatch.setattr(builtins, "open", fake_open)

    assert run_engine_detached._pid_ppid(123) is None


def test_engine_detached_pids_require_ppid_one_and_alive(monkeypatch):
    monkeypatch.setattr(run_engine_detached, "_live_engine_pids", lambda: [10, 20, 30])
    monkeypatch.setattr(run_engine_detached, "_pid_ppid", lambda pid: {10: 1, 20: 999, 30: 1}[pid])
    monkeypatch.setattr(run_engine_detached, "_pid_alive", lambda pid: pid != 30)

    assert run_engine_detached._detached_engine_pids() == [10]


def test_engine_all_pids_deduplicates_and_filters_alive(monkeypatch):
    monkeypatch.setattr(run_engine_detached, "_live_engine_pids", lambda: [10, 20, 10, 30])
    monkeypatch.setattr(run_engine_detached, "_pid_alive", lambda pid: pid != 20)

    assert run_engine_detached._all_engine_pids() == [10, 30]


def test_wrapper_stop_pid_helpers_delegate_to_runner(monkeypatch):
    calls = []

    def fake_stop_pid(pid, timeout=5.0, kill_after_timeout=True):
        calls.append((pid, timeout, kill_after_timeout))

    monkeypatch.setattr(run_engine_detached.wrapper_runner, "stop_pid", fake_stop_pid)

    run_engine_detached._stop_pid(10, timeout=1.0)
    run_dashboard_detached._stop_pid(20, timeout=2.0)
    run_ai_sidecar_detached._stop_pid(30)

    assert calls == [(10, 1.0, True), (20, 2.0, True), (30, 5.0, False)]


def test_dashboard_listening_pid_on_port_parses_ss_output(monkeypatch):
    def fake_check_output(args, text, stderr):
        assert args == ["ss", "-ltnp", f"( sport = :{run_dashboard_detached.PORT} )"]
        assert text is True
        assert stderr == run_dashboard_detached.subprocess.DEVNULL
        return 'LISTEN 0 4096 *:8844 *:* users:(("python",pid=4321,fd=7))\n'

    monkeypatch.setattr(run_dashboard_detached.subprocess, "check_output", fake_check_output)

    assert run_dashboard_detached._listening_pid_on_port() == 4321


@pytest.mark.parametrize("output", ["", 'LISTEN users:(("python",fd=7))\n'])
def test_dashboard_listening_pid_on_port_returns_none_without_pid(monkeypatch, output):
    monkeypatch.setattr(run_dashboard_detached.subprocess, "check_output", lambda *args, **kwargs: output)

    assert run_dashboard_detached._listening_pid_on_port() is None


def test_dashboard_listening_pid_on_port_returns_none_on_failure(monkeypatch):
    def fake_check_output(*args, **kwargs):
        raise OSError("ss missing")

    monkeypatch.setattr(run_dashboard_detached.subprocess, "check_output", fake_check_output)

    assert run_dashboard_detached._listening_pid_on_port() is None


def test_dashboard_start_merges_port_pid_and_reuses_newest(monkeypatch, tmp_path):
    stopped = []
    pid_path = tmp_path / "dashboard.pid"

    monkeypatch.setattr(run_dashboard_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_dashboard_detached, "_listening_pid_on_port", lambda: 30)
    monkeypatch.setattr(run_dashboard_detached, "_live_dashboard_pids", lambda: [10, 20])
    monkeypatch.setattr(run_dashboard_detached, "_stop_pid", stopped.append)
    monkeypatch.setattr(run_dashboard_detached.subprocess, "Popen", _fail_popen)

    assert run_dashboard_detached.start() == 30
    assert stopped == [10, 20]
    assert pid_path.read_text(encoding="utf-8") == "30"


def test_dashboard_status_falls_back_to_live_pgrep_pid(monkeypatch):
    monkeypatch.setattr(run_dashboard_detached, "_listening_pid_on_port", lambda: None)
    monkeypatch.setattr(run_dashboard_detached, "_live_dashboard_pids", lambda: [12, 34])

    assert run_dashboard_detached.status() == 34


def test_engine_start_fresh_uses_proc_pid_when_detached_pid_never_appears(monkeypatch, tmp_path):
    calls = []
    sleeps = []
    times = iter([0.0, 0.0, 5.1])
    pid_path = tmp_path / "engine.pid"
    log_path = tmp_path / "engine.log"

    def fake_start_detached(python, target, base, log):
        calls.append(("start", python, target, base, log))
        return 777

    monkeypatch.setattr(run_engine_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_engine_detached, "LOG_PATH", log_path)
    monkeypatch.setattr(run_engine_detached, "_detached_engine_pids", lambda: [])
    monkeypatch.setattr(run_engine_detached.wrapper_runner, "start_detached", fake_start_detached)
    monkeypatch.setattr(run_engine_detached.time, "time", lambda: next(times))
    monkeypatch.setattr(run_engine_detached.time, "sleep", sleeps.append)

    assert run_engine_detached._start_fresh_detached() == 777
    assert calls == [
        (
            "start",
            run_engine_detached.PYTHON,
            run_engine_detached.ENGINE,
            run_engine_detached.BASE,
            log_path,
        )
    ]
    assert sleeps == [0.1]
    assert pid_path.read_text(encoding="utf-8") == "777"


@pytest.mark.parametrize(
    ("module", "start_name"),
    [
        (run_engine_detached, "_start_fresh_detached"),
        (run_dashboard_detached, "_start_detached"),
        (run_ai_sidecar_detached, "start"),
    ],
)
def test_wrapper_restart_stops_before_starting(monkeypatch, module, start_name):
    calls = []

    def fake_stop():
        calls.append("stop")

    def fake_start():
        calls.append("start")
        return 42

    monkeypatch.setattr(module, "stop", fake_stop)
    monkeypatch.setattr(module, start_name, fake_start)

    assert module.restart() == 42
    assert calls == ["stop", "start"]


@pytest.mark.parametrize(
    ("module", "command", "target_name", "return_value", "expected"),
    [
        (run_engine_detached, "start", "start", 11, "11\n"),
        (run_engine_detached, "stop", "stop", None, "stopped\n"),
        (run_engine_detached, "restart", "restart", 12, "12\n"),
        (run_engine_detached, "status", "status", 13, "13\n"),
        (run_dashboard_detached, "start", "start", 21, "21\n"),
        (run_dashboard_detached, "stop", "stop", None, "stopped\n"),
        (run_dashboard_detached, "restart", "restart", 22, "22\n"),
        (run_dashboard_detached, "status", "status", 23, "23\n"),
    ],
)
def test_engine_and_dashboard_main_route_commands(
    monkeypatch,
    capsys,
    module,
    command,
    target_name,
    return_value,
    expected,
):
    calls = []

    def fake_command():
        calls.append(target_name)
        return return_value

    monkeypatch.setattr(module, target_name, fake_command)
    monkeypatch.setattr(sys, "argv", [module.__file__, command])

    module.main()

    assert calls == [target_name]
    assert capsys.readouterr().out == expected


@pytest.mark.parametrize("module", [run_engine_detached, run_dashboard_detached])
def test_engine_and_dashboard_main_reject_unknown_command(monkeypatch, module):
    monkeypatch.setattr(sys, "argv", [module.__file__, "bogus"])

    with pytest.raises(SystemExit, match="unknown command: bogus"):
        module.main()


def test_dashboard_main_log_reset_only_truncates_patched_log_path(monkeypatch, tmp_path, capsys):
    log_path = tmp_path / "dashboard.nohup.out"
    log_path.write_text("old", encoding="utf-8")
    monkeypatch.setattr(run_dashboard_detached, "LOG_PATH", log_path)
    monkeypatch.setattr(sys, "argv", [run_dashboard_detached.__file__, "log-reset"])

    run_dashboard_detached.main()

    assert log_path.read_text(encoding="utf-8") == ""
    assert capsys.readouterr().out == "log-reset\n"


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
