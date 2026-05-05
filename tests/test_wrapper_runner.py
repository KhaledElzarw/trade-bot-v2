import os
import signal
import subprocess

import wrapper_runner


def test_get_python_executable_returns_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("TRADEBOT_PYTHON", raising=False)

    assert wrapper_runner.get_python_executable("/default/python") == "/default/python"


def test_get_python_executable_uses_tradebot_python_when_set(monkeypatch):
    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")

    assert wrapper_runner.get_python_executable("/default/python") == "/custom/python"


def test_start_detached_calls_popen_contract_and_returns_pid(monkeypatch, tmp_path):
    calls = []

    class Proc:
        pid = 4321

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    base_dir = tmp_path / "base"
    base_dir.mkdir()
    log_path = tmp_path / "wrapper.log"
    monkeypatch.setenv("TRADEBOT_WRAPPER_TEST", "present")
    monkeypatch.setattr(wrapper_runner.subprocess, "Popen", fake_popen)

    pid = wrapper_runner.start_detached(
        "/custom/python",
        "target.py",
        base_dir,
        log_path,
    )

    assert pid == 4321
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ["/custom/python", "target.py"]
    assert set(kwargs) == {
        "cwd",
        "stdin",
        "stdout",
        "stderr",
        "start_new_session",
        "close_fds",
        "env",
    }
    assert kwargs["cwd"] == str(base_dir)
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"].name == str(log_path)
    assert kwargs["stdout"].closed
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True
    assert isinstance(kwargs["env"], dict)
    assert kwargs["env"] is not os.environ
    assert kwargs["env"]["TRADEBOT_WRAPPER_TEST"] == "present"
    assert log_path.exists()


def test_write_pid_writes_string_pid_to_path(tmp_path):
    pid_path = tmp_path / "wrapper.pid"

    wrapper_runner.write_pid(pid_path, 12345)

    assert pid_path.read_text(encoding="utf-8") == "12345"


def test_unlink_pid_removes_existing_pid_path(tmp_path):
    pid_path = tmp_path / "wrapper.pid"
    pid_path.write_text("12345", encoding="utf-8")

    wrapper_runner.unlink_pid(pid_path)

    assert not pid_path.exists()


def test_unlink_pid_is_safe_when_file_is_missing(tmp_path):
    pid_path = tmp_path / "missing.pid"

    wrapper_runner.unlink_pid(pid_path)

    assert not pid_path.exists()


def test_pid_alive_returns_true_when_kill_zero_succeeds(monkeypatch):
    calls = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))

    monkeypatch.setattr(wrapper_runner.os, "kill", fake_kill)

    assert wrapper_runner.pid_alive(12345) is True
    assert calls == [(12345, 0)]


def test_pid_alive_returns_false_when_kill_zero_raises(monkeypatch):
    def fake_kill(pid, sig):
        raise OSError("missing")

    monkeypatch.setattr(wrapper_runner.os, "kill", fake_kill)

    assert wrapper_runner.pid_alive(12345) is False


def test_stop_pid_sends_sigterm_and_returns_when_process_exits(monkeypatch):
    calls = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))

    monkeypatch.setattr(wrapper_runner.os, "kill", fake_kill)
    monkeypatch.setattr(wrapper_runner, "pid_alive", lambda pid: False)
    monkeypatch.setattr(wrapper_runner.time, "time", lambda: 0.0)
    monkeypatch.setattr(
        wrapper_runner.time,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(
            AssertionError("sleep should not be called")
        ),
    )

    wrapper_runner.stop_pid(12345)

    assert calls == [(12345, signal.SIGTERM)]


def test_stop_pid_sends_sigkill_after_timeout_when_process_stays_alive(
    monkeypatch,
):
    calls = []
    sleeps = []
    times = iter([0.0, 0.0, 0.2])

    def fake_kill(pid, sig):
        calls.append((pid, sig))

    monkeypatch.setattr(wrapper_runner.os, "kill", fake_kill)
    monkeypatch.setattr(wrapper_runner, "pid_alive", lambda pid: True)
    monkeypatch.setattr(wrapper_runner.time, "time", lambda: next(times))
    monkeypatch.setattr(wrapper_runner.time, "sleep", sleeps.append)

    wrapper_runner.stop_pid(12345, timeout=0.1)

    assert calls == [(12345, signal.SIGTERM), (12345, signal.SIGKILL)]
    assert sleeps == [0.1]


def test_stop_pid_swallows_sigkill_exception_after_timeout(monkeypatch):
    calls = []
    sleeps = []
    times = iter([0.0, 0.0, 0.2])

    def fake_kill(pid, sig):
        calls.append((pid, sig))
        if sig == signal.SIGKILL:
            raise OSError("denied")

    monkeypatch.setattr(wrapper_runner.os, "kill", fake_kill)
    monkeypatch.setattr(wrapper_runner, "pid_alive", lambda pid: True)
    monkeypatch.setattr(wrapper_runner.time, "time", lambda: next(times))
    monkeypatch.setattr(wrapper_runner.time, "sleep", sleeps.append)

    wrapper_runner.stop_pid(12345, timeout=0.1)

    assert calls == [(12345, signal.SIGTERM), (12345, signal.SIGKILL)]
    assert sleeps == [0.1]


def test_stop_pid_without_kill_after_timeout_sends_only_sigterm(monkeypatch):
    calls = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))

    monkeypatch.setattr(wrapper_runner.os, "kill", fake_kill)
    monkeypatch.setattr(
        wrapper_runner,
        "pid_alive",
        lambda pid: (_ for _ in ()).throw(
            AssertionError("pid_alive should not be called")
        ),
    )

    wrapper_runner.stop_pid(12345, kill_after_timeout=False)

    assert calls == [(12345, signal.SIGTERM)]


def test_stop_pid_returns_cleanly_on_process_lookup_error(monkeypatch):
    def fake_kill(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(wrapper_runner.os, "kill", fake_kill)

    wrapper_runner.stop_pid(12345)


def test_stop_pid_returns_cleanly_on_generic_kill_exception(monkeypatch):
    def fake_kill(pid, sig):
        raise PermissionError("denied")

    monkeypatch.setattr(wrapper_runner.os, "kill", fake_kill)

    wrapper_runner.stop_pid(12345)
