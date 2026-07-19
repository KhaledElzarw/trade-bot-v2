import os
import runpy
import sys

import dashboard_orchestrator


def test_orchestrator_services_map_contains_only_runtime_wrappers():
    services = dashboard_orchestrator.SERVICES

    assert set(services) == {"engine", "dashboard", "ai"}
    assert {name: path.name for name, path in services.items()} == {
        "engine": "run_engine_detached.py",
        "dashboard": "run_dashboard_detached.py",
        "ai": "run_ai_sidecar_detached.py",
    }

    removed_or_deprecated = (
        "advisor",
        "engine_trend",
        "grid_engine_honest",
        "grid_engine_honest_v2",
    )
    service_text = " ".join(f"{name} {path.name}" for name, path in services.items())

    assert not any(marker in service_text for marker in removed_or_deprecated)


def test_orchestrator_python_executable_defaults_to_venv(monkeypatch):
    monkeypatch.delenv("TRADEBOT_PYTHON", raising=False)

    assert dashboard_orchestrator.get_python_executable() == dashboard_orchestrator.PYTHON


def test_orchestrator_python_executable_honors_override(monkeypatch):
    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")

    assert dashboard_orchestrator.get_python_executable() == "/custom/python"


def test_orchestrator_run_uses_configurable_python(monkeypatch):
    calls = []

    class Result:
        stdout = "123\n"

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return Result()

    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")
    monkeypatch.setattr(dashboard_orchestrator.subprocess, "run", fake_run)

    assert dashboard_orchestrator._run("engine", "status") == "123"
    assert calls == [
        (
            [
                "/custom/python",
                str(dashboard_orchestrator.SERVICES["engine"]),
                "status",
            ],
            {
                "cwd": str(dashboard_orchestrator.BASE_DIR),
                "text": True,
                "capture_output": True,
                "check": True,
            },
        )
    ]


def test_orchestrator_start_starts_all_runtime_services_in_order(monkeypatch):
    calls = []

    def fake_run(service, command):
        calls.append((service, command))
        return f"{service}-{command}"

    monkeypatch.setattr(dashboard_orchestrator, "_run", fake_run)

    assert dashboard_orchestrator.start() == {
        "engine": "engine-start",
        "dashboard": "dashboard-start",
        "ai": "ai-start",
    }
    assert calls == [("engine", "start"), ("dashboard", "start"), ("ai", "start")]


def test_orchestrator_stop_stops_all_runtime_services_in_order(monkeypatch):
    calls = []

    def fake_run(service, command):
        calls.append((service, command))
        return f"{service}-{command}"

    monkeypatch.setattr(dashboard_orchestrator, "_run", fake_run)

    assert dashboard_orchestrator.stop() == {
        "ai": "ai-stop",
        "engine": "engine-stop",
        "dashboard": "dashboard-stop",
    }
    assert calls == [("ai", "stop"), ("engine", "stop"), ("dashboard", "stop")]


def test_orchestrator_status_queries_all_runtime_services(monkeypatch):
    calls = []

    def fake_run(service, command):
        calls.append((service, command))
        return f"{service}-pid"

    monkeypatch.setattr(dashboard_orchestrator, "_run", fake_run)

    assert dashboard_orchestrator.status() == {
        "engine": "engine-pid",
        "dashboard": "dashboard-pid",
        "ai": "ai-pid",
    }
    assert calls == [("engine", "status"), ("dashboard", "status"), ("ai", "status")]


def test_orchestrator_restart_stops_before_starting(monkeypatch):
    calls = []

    def fake_run(service, command):
        calls.append((service, command))
        return f"{service}-{command}"

    monkeypatch.setattr(dashboard_orchestrator, "_run", fake_run)

    assert dashboard_orchestrator.restart() == {
        "engine": "engine-start",
        "dashboard": "dashboard-start",
        "ai": "ai-start",
    }
    assert calls == [
        ("ai", "stop"),
        ("engine", "stop"),
        ("dashboard", "stop"),
        ("engine", "start"),
        ("dashboard", "start"),
        ("ai", "start"),
    ]


def test_orchestrator_entrypoint_prints_status_for_all_services(monkeypatch, capsys):
    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(args, **kwargs):
        assert args[0] == dashboard_orchestrator.PYTHON
        assert args[2] == "status"
        assert kwargs == {
            "cwd": str(dashboard_orchestrator.BASE_DIR),
            "text": True,
            "capture_output": True,
            "check": True,
        }
        return Result(f"{os.path.basename(args[1])}-pid\n")

    monkeypatch.setattr(dashboard_orchestrator.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["dashboard_orchestrator.py", "status"])

    runpy.run_path(dashboard_orchestrator.__file__, run_name="__main__")

    assert capsys.readouterr().out == (
        "engine: run_engine_detached.py-pid\n"
        "dashboard: run_dashboard_detached.py-pid\n"
        "ai: run_ai_sidecar_detached.py-pid\n"
    )
