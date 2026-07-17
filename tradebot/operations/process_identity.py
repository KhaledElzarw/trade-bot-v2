"""Verified process identity for safe supervision (closes A15).

The legacy supervisor killed processes by PID file / `pgrep -f` / port owner —
all of which can target an unrelated process after a PID is recycled.

Here a PID file records a full identity record, and a process is only ever
signalled when EVERY field still matches the live process:

* pid
* OS-reported start timestamp (the field that actually defeats PID reuse)
* expected executable
* expected command
* service instance id
* PID-file nonce

Termination is graceful first (SIGTERM), then bounded escalation to SIGKILL —
and identity is RE-VERIFIED immediately before the escalation, because the
target could have exited and its PID been recycled during the grace window.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

IDENTITY_VERSION = "process-identity-v1"
DEFAULT_GRACE_SECONDS = 10.0


class IdentityMismatch(Exception):
    """The live process is not the one we recorded. Never signal it."""


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    version: str
    pid: int
    start_time: float          # OS-reported creation time
    executable: str
    command: str
    service: str
    instance_id: str
    nonce: str

    def matches(self, other: ProcessIdentity, *, tolerance: float = 1.0) -> bool:
        """Every field must agree. start_time compared with a small tolerance
        because platforms report it at differing resolutions."""

        return (
            self.pid == other.pid
            and abs(self.start_time - other.start_time) <= tolerance
            and self.executable == other.executable
            and self.command == other.command
            and self.service == other.service
            and self.instance_id == other.instance_id
            and self.nonce == other.nonce
        )


def new_instance_id() -> str:
    return secrets.token_hex(8)


def new_nonce() -> str:
    return secrets.token_hex(16)


def write_pid_file(path: Path, identity: ProcessIdentity) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(identity), indent=2), encoding="utf-8")


def read_pid_file(path: Path) -> ProcessIdentity | None:
    """Return the recorded identity, or None when absent/corrupt/stale-format."""

    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != IDENTITY_VERSION:
            return None
        return ProcessIdentity(**data)
    except (json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
        return None


# -- live process inspection (injected so tests never touch real processes) ---

ProbeFn = Callable[[int], "ProcessIdentity | None"]


def verify(recorded: ProcessIdentity, probe: ProbeFn) -> ProcessIdentity:
    """Confirm the live process still IS the recorded one, or raise."""

    live = probe(recorded.pid)
    if live is None:
        raise IdentityMismatch(f"pid {recorded.pid} is not running")
    if not recorded.matches(live):
        raise IdentityMismatch(
            f"pid {recorded.pid} does not match recorded identity "
            f"(likely PID reuse); refusing to signal"
        )
    return live


@dataclass(frozen=True, slots=True)
class StopOutcome:
    stopped: bool
    escalated: bool
    reason: str


def stop_process(
    recorded: ProcessIdentity,
    probe: ProbeFn,
    kill: Callable[[int, int], None],
    is_alive: Callable[[int], bool],
    sleep: Callable[[float], None],
    *,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
    poll_interval: float = 0.5,
) -> StopOutcome:
    """Graceful stop with identity-verified bounded escalation.

    Never signals a process whose identity does not match — a stale PID file or
    a recycled PID results in a refusal, not a kill.
    """

    try:
        verify(recorded, probe)
    except IdentityMismatch as exc:
        return StopOutcome(False, False, str(exc))

    kill(recorded.pid, signal.SIGTERM)

    waited = 0.0
    while waited < grace_seconds:
        if not is_alive(recorded.pid):
            return StopOutcome(True, False, "terminated gracefully")
        sleep(poll_interval)
        waited += poll_interval

    # Still alive: RE-VERIFY before escalating. During the grace window the
    # target may have exited and its PID been reassigned to something else.
    try:
        verify(recorded, probe)
    except IdentityMismatch as exc:
        return StopOutcome(True, False,
                           f"process exited during grace period ({exc})")

    kill(recorded.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    if is_alive(recorded.pid):
        return StopOutcome(False, True, "process survived escalation")
    return StopOutcome(True, True, "terminated after escalation")


def default_probe(pid: int) -> ProcessIdentity | None:  # pragma: no cover - real OS
    """Real probe. Requires psutil for a trustworthy OS start time.

    Absent psutil we return None rather than guessing — refusing to act is the
    safe failure mode.
    """

    try:
        import psutil
    except ImportError:
        return None
    try:
        proc = psutil.Process(pid)
        return ProcessIdentity(
            version=IDENTITY_VERSION,
            pid=pid,
            start_time=proc.create_time(),
            executable=proc.exe(),
            command=" ".join(proc.cmdline()),
            service="",
            instance_id="",
            nonce="",
        )
    except Exception:
        return None


def current_identity(service: str, instance_id: str, nonce: str,
                     start_time: float, executable: str | None = None,
                     command: str | None = None) -> ProcessIdentity:
    import sys

    return ProcessIdentity(
        version=IDENTITY_VERSION,
        pid=os.getpid(),
        start_time=start_time,
        executable=executable or sys.executable,
        command=command or " ".join(sys.argv),
        service=service,
        instance_id=instance_id,
        nonce=nonce,
    )
