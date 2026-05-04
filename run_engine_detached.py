import os
import subprocess
import sys
import time
from pathlib import Path

import wrapper_runner

BASE = Path(__file__).resolve().parent
PID_PATH = BASE / 'engine.pid'
LOG_PATH = BASE / 'engine.nohup.out'
PYTHON = str(BASE / '.venv' / 'bin' / 'python')
ENGINE = str(BASE / 'engine.py')


def get_python_executable() -> str:
    return wrapper_runner.get_python_executable(PYTHON)


def _live_engine_pids():
    try:
        out = subprocess.check_output(['pgrep', '-f', ENGINE], text=True)
        current = os.getpid()
        return [int(x.strip()) for x in out.splitlines() if x.strip() and int(x.strip()) != current]
    except subprocess.CalledProcessError:
        return []


def _pid_alive(pid: int) -> bool:
    return wrapper_runner.pid_alive(pid)


def _pid_ppid(pid: int) -> int | None:
    try:
        with open(f'/proc/{pid}/stat', 'r', encoding='utf-8') as f:
            parts = f.read().split()
        return int(parts[3])
    except Exception:
        return None


def _detached_engine_pids() -> list[int]:
    return [pid for pid in _live_engine_pids() if _pid_ppid(pid) == 1 and _pid_alive(pid)]


def _all_engine_pids() -> list[int]:
    seen = []
    for pid in _live_engine_pids():
        if pid not in seen and _pid_alive(pid):
            seen.append(pid)
    return seen


def _stop_pid(pid: int, timeout: float = 5.0) -> None:
    wrapper_runner.stop_pid(pid, timeout=timeout)


def _start_fresh_detached() -> int:
    proc_pid = wrapper_runner.start_detached(get_python_executable(), ENGINE, BASE, LOG_PATH)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        detached = _detached_engine_pids()
        if detached:
            pid = max(detached)
            wrapper_runner.write_pid(PID_PATH, pid)
            return pid
        time.sleep(0.1)
    wrapper_runner.write_pid(PID_PATH, proc_pid)
    return proc_pid


def start() -> int:
    live = _all_engine_pids()
    if live:
        pid = max(live)
        for other in live:
            if other != pid:
                _stop_pid(other)
        wrapper_runner.write_pid(PID_PATH, pid)
        return pid

    return _start_fresh_detached()


def stop() -> None:
    for pid in _all_engine_pids():
        _stop_pid(pid)
    wrapper_runner.unlink_pid(PID_PATH)


def restart() -> int:
    stop()
    return _start_fresh_detached()


def status() -> int:
    live = _all_engine_pids()
    if live:
        return max(live)
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
