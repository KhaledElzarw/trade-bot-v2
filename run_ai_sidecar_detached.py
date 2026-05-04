import os
import subprocess
from pathlib import Path

import wrapper_runner

BASE = Path(__file__).resolve().parent
PID_PATH = BASE / 'ai_sidecar.pid'
LOG_PATH = BASE / 'ai_sidecar.nohup.out'
PYTHON = str(BASE / '.venv' / 'bin' / 'python')
SIDECAR = str(BASE / 'ai_sidecar.py')


def get_python_executable() -> str:
    return wrapper_runner.get_python_executable(PYTHON)


def _live_pids():
    try:
        out = subprocess.check_output(['pgrep', '-f', SIDECAR], text=True)
        current = os.getpid()
        return [int(x.strip()) for x in out.splitlines() if x.strip() and int(x.strip()) != current]
    except subprocess.CalledProcessError:
        return []


def _stop_pid(pid: int) -> None:
    wrapper_runner.stop_pid(pid, kill_after_timeout=False)


def start() -> int:
    live = _live_pids()
    if live:
        newest = max(live)
        for pid in live:
            if pid != newest:
                _stop_pid(pid)
        wrapper_runner.write_pid(PID_PATH, newest)
        return newest
    pid = wrapper_runner.start_detached(get_python_executable(), SIDECAR, BASE, LOG_PATH)
    wrapper_runner.write_pid(PID_PATH, pid)
    return pid


def stop() -> None:
    for pid in _live_pids():
        _stop_pid(pid)
    wrapper_runner.unlink_pid(PID_PATH)


def restart() -> int:
    stop()
    return start()


def status() -> int:
    live = _live_pids()
    if live:
        return max(live)
    return 0


if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'start'
    if cmd == 'start':
        print(start())
    elif cmd == 'stop':
        stop()
        print('stopped')
    elif cmd == 'restart':
        print(restart())
    elif cmd == 'status':
        print(status())
    else:
        raise SystemExit(f'unknown command: {cmd}')
