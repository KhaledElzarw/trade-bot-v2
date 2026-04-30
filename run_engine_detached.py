import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
PID_PATH = BASE / 'engine.pid'
LOG_PATH = BASE / 'engine.nohup.out'
PYTHON = str(BASE / '.venv' / 'bin' / 'python')
ENGINE = str(BASE / 'engine.py')


def _live_engine_pids():
    try:
        out = subprocess.check_output(['pgrep', '-f', ENGINE], text=True)
        current = os.getpid()
        return [int(x.strip()) for x in out.splitlines() if x.strip() and int(x.strip()) != current]
    except subprocess.CalledProcessError:
        return []


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _pid_ppid(pid: int) -> int | None:
    try:
        with open(f'/proc/{pid}/stat', 'r', encoding='utf-8') as f:
            parts = f.read().split()
        return int(parts[3])
    except Exception:
        return None


def _detached_engine_pids() -> list[int]:
    return [pid for pid in _live_engine_pids() if _pid_ppid(pid) == 1 and _pid_alive(pid)]


def _stop_pid(pid: int, timeout: float = 5.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _start_fresh_detached() -> int:
    log = open(LOG_PATH, 'ab', buffering=0)
    proc = subprocess.Popen(
        [PYTHON, ENGINE],
        cwd=str(BASE),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
        env=os.environ.copy(),
    )
    deadline = time.time() + 5.0
    while time.time() < deadline:
        detached = _detached_engine_pids()
        if detached:
            pid = max(detached)
            PID_PATH.write_text(str(pid))
            return pid
        time.sleep(0.1)
    PID_PATH.write_text(str(proc.pid))
    return proc.pid


def start() -> int:
    detached = _detached_engine_pids()
    if detached:
        pid = max(detached)
        for other in _live_engine_pids():
            if other != pid:
                _stop_pid(other)
        PID_PATH.write_text(str(pid))
        return pid

    # No canonical detached engine exists, so kill everything matching and create one clean worker.
    for pid in _live_engine_pids():
        _stop_pid(pid)
    return _start_fresh_detached()


def stop() -> None:
    for pid in _live_engine_pids():
        _stop_pid(pid)
    if PID_PATH.exists():
        PID_PATH.unlink()


def restart() -> int:
    stop()
    return _start_fresh_detached()


def status() -> int:
    detached = _detached_engine_pids()
    if detached:
        pid = max(detached)
        for other in _live_engine_pids():
            if other != pid:
                _stop_pid(other)
        PID_PATH.write_text(str(pid))
        return pid
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'start'
    if cmd == 'start':
        print(start())
        return
    if cmd == 'stop':
        stop()
        print('stopped')
        return
    if cmd == 'restart':
        print(restart())
        return
    if cmd == 'status':
        print(status())
        return
    raise SystemExit(f'unknown command: {cmd}')


if __name__ == '__main__':
    main()
