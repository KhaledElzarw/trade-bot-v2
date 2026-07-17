"""tradebotctl — operator CLI.

Commands: start, stop, restart, status, doctor, migrate, seed,
run-daily-review, run-weekly-review, validate-strategy, replay-strategy.

Every side-effecting command is dependency-injected (`Runtime`) so tests drive
it without touching real processes, databases, daemons, or the network.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from ..operations.process_identity import (
    ProcessIdentity,
    read_pid_file,
    stop_process,
)

SERVICES = ("portfolio-engine", "api", "evolution-worker")
EXIT_OK = 0
EXIT_ERROR = 1


@dataclass(slots=True)
class Runtime:
    """Injected side-effect boundary (fakes in tests, real adapters in prod)."""

    pid_dir: Path
    start_service: Callable[[str], ProcessIdentity] | None = None
    probe: Callable[[int], ProcessIdentity | None] | None = None
    kill: Callable[[int, int], None] | None = None
    is_alive: Callable[[int], bool] | None = None
    sleep: Callable[[float], None] = lambda _s: None
    migrate: Callable[[bool], dict] | None = None
    seed: Callable[[bool], dict] | None = None
    daily_review: Callable[[str, bool], dict] | None = None
    weekly_review: Callable[[str, bool], dict] | None = None
    validate_strategy: Callable[[Path], dict] | None = None
    replay_strategy: Callable[[Path, int], dict] | None = None
    checks: list[Callable[[], tuple[str, bool, str]]] = field(default_factory=list)
    out: Callable[[str], None] = print

    def pid_file(self, service: str) -> Path:
        return self.pid_dir / f"{service}.pid.json"


def _emit(rt: Runtime, payload: dict) -> None:
    rt.out(json.dumps(payload, indent=2, default=str, sort_keys=True))


# -- commands -----------------------------------------------------------------

def cmd_status(rt: Runtime, _args) -> int:
    services: dict[str, dict[str, object]] = {}
    for service in SERVICES:
        recorded = read_pid_file(rt.pid_file(service))
        if recorded is None:
            services[service] = {"state": "stopped", "reason": "no pid file"}
            continue
        live = rt.probe(recorded.pid) if rt.probe else None
        if live is None:
            services[service] = {"state": "stopped", "pid": recorded.pid,
                                 "reason": "process not running (stale pid file)"}
        elif not recorded.matches(live):
            # Never claim a mismatched process is ours.
            services[service] = {"state": "unknown", "pid": recorded.pid,
                                 "reason": "pid reused by another process"}
        else:
            services[service] = {"state": "running", "pid": recorded.pid,
                                 "instance_id": recorded.instance_id}
    _emit(rt, {"services": services})
    return EXIT_OK


def cmd_start(rt: Runtime, args) -> int:
    targets = [args.service] if args.service else list(SERVICES)
    started: dict[str, dict[str, object]] = {}
    for service in targets:
        recorded = read_pid_file(rt.pid_file(service))
        live = rt.probe(recorded.pid) if (recorded and rt.probe) else None
        if recorded is not None and live is not None:
            if recorded.matches(live):
                started[service] = {"state": "already running",
                                    "pid": recorded.pid}
                continue
        if rt.start_service is None:
            started[service] = {"state": "error", "reason": "no start adapter"}
            continue
        identity = rt.start_service(service)
        started[service] = {"state": "started", "pid": identity.pid,
                            "instance_id": identity.instance_id}
    _emit(rt, {"started": started})
    return EXIT_OK


def cmd_stop(rt: Runtime, args) -> int:
    targets = [args.service] if args.service else list(SERVICES)
    results: dict[str, dict[str, object]] = {}
    exit_code = EXIT_OK
    for service in targets:
        recorded = read_pid_file(rt.pid_file(service))
        if recorded is None:
            results[service] = {"stopped": False, "reason": "no pid file"}
            continue
        if rt.probe is None or rt.kill is None or rt.is_alive is None:
            # Fail closed: without a way to VERIFY identity we must never
            # signal anything (A15).
            results[service] = {"stopped": False,
                                "reason": "no process adapters; refusing to signal"}
            exit_code = EXIT_ERROR
            continue
        outcome = stop_process(
            recorded, rt.probe, rt.kill, rt.is_alive, rt.sleep,
            grace_seconds=args.grace,
        )
        results[service] = {"stopped": outcome.stopped,
                            "escalated": outcome.escalated,
                            "reason": outcome.reason}
        if outcome.stopped:
            rt.pid_file(service).unlink(missing_ok=True)
        elif "does not match" in outcome.reason:
            exit_code = EXIT_ERROR  # refused to kill a mismatched process
    _emit(rt, {"stopped": results})
    return exit_code


def cmd_restart(rt: Runtime, args) -> int:
    if cmd_stop(rt, args) != EXIT_OK:
        return EXIT_ERROR
    return cmd_start(rt, args)


def cmd_doctor(rt: Runtime, _args) -> int:
    results = []
    healthy = True
    for check in rt.checks:
        name, ok, detail = check()
        results.append({"check": name, "ok": ok, "detail": detail})
        healthy = healthy and ok
    _emit(rt, {"healthy": healthy, "checks": results})
    return EXIT_OK if healthy else EXIT_ERROR


def cmd_migrate(rt: Runtime, args) -> int:
    if rt.migrate is None:
        _emit(rt, {"error": "no migrate adapter"})
        return EXIT_ERROR
    _emit(rt, rt.migrate(args.dry_run))
    return EXIT_OK


def cmd_seed(rt: Runtime, args) -> int:
    if rt.seed is None:
        _emit(rt, {"error": "no seed adapter"})
        return EXIT_ERROR
    _emit(rt, rt.seed(args.dry_run))
    return EXIT_OK


def cmd_daily(rt: Runtime, args) -> int:
    if rt.daily_review is None:
        _emit(rt, {"error": "no daily adapter"})
        return EXIT_ERROR
    _emit(rt, rt.daily_review(args.date, args.force))
    return EXIT_OK


def cmd_weekly(rt: Runtime, args) -> int:
    if rt.weekly_review is None:
        _emit(rt, {"error": "no weekly adapter"})
        return EXIT_ERROR
    _emit(rt, rt.weekly_review(args.window, args.force))
    return EXIT_OK


def cmd_validate_strategy(rt: Runtime, args) -> int:
    if rt.validate_strategy is None:
        _emit(rt, {"error": "no validate adapter"})
        return EXIT_ERROR
    result = rt.validate_strategy(Path(args.bundle))
    _emit(rt, result)
    return EXIT_OK if result.get("ok") else EXIT_ERROR


def cmd_replay_strategy(rt: Runtime, args) -> int:
    if rt.replay_strategy is None:
        _emit(rt, {"error": "no replay adapter"})
        return EXIT_ERROR
    result = rt.replay_strategy(Path(args.bundle), args.candles)
    _emit(rt, result)
    return EXIT_OK if result.get("ok") else EXIT_ERROR


COMMANDS: dict[str, Callable[[Runtime, object], int]] = {
    "start": cmd_start,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "status": cmd_status,
    "doctor": cmd_doctor,
    "migrate": cmd_migrate,
    "seed": cmd_seed,
    "run-daily-review": cmd_daily,
    "run-weekly-review": cmd_weekly,
    "validate-strategy": cmd_validate_strategy,
    "replay-strategy": cmd_replay_strategy,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tradebotctl")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("start", "stop", "restart"):
        p = sub.add_parser(name)
        p.add_argument("--service", choices=SERVICES)
        p.add_argument("--grace", type=float, default=10.0)

    sub.add_parser("status")
    sub.add_parser("doctor")

    for name in ("migrate", "seed"):
        p = sub.add_parser(name)
        p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("run-daily-review")
    p.add_argument("--date", required=True)
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("run-weekly-review")
    p.add_argument("--window", required=True)
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("validate-strategy")
    p.add_argument("--bundle", required=True)

    p = sub.add_parser("replay-strategy")
    p.add_argument("--bundle", required=True)
    p.add_argument("--candles", type=int, default=500)

    return parser


def main(argv: Sequence[str] | None = None, runtime: Runtime | None = None) -> int:
    args = build_parser().parse_args(argv)
    rt = runtime or Runtime(pid_dir=Path("runtime/pids"))
    return COMMANDS[args.command](rt, args)


if __name__ == "__main__":  # pragma: no cover - console entry
    sys.exit(main())
