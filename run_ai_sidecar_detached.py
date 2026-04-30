import os
import signal
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
PID_PATH = BASE / 'ai_sidecar.pid'
LOG_PATH = BASE / 'ai_sidecar.nohup.out'
PYTHON = str(BASE / '.venv' / 'bin' / 'python')
SIDECAR = str(BASE / 'ai_sidecar.py')


def _live_pids():
    try:
        out = subprocess.check_output(['pgrep', '-f', SIDECAR], text=True)
        current = os.getpid()
        return [int(x.strip()) for x in out.splitlines() if x.strip() and int(x.strip()) != current]
    except subprocess.CalledProcessError:
        return []


def _stop_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def start() -> int:
    live = _live_pids()
    if live:
        newest = max(live)
        for pid in live:
            if pid != newest:
                _stop_pid(pid)
        PID_PATH.write_text(str(newest))
        return newest
    log = open(LOG_PATH, 'ab', buffering=0)
    proc = subprocess.Popen(
        [PYTHON, SIDECAR],
        cwd=str(BASE),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
        env=os.environ.copy(),
    )
    PID_PATH.write_text(str(proc.pid))
    return proc.pid


def stop() -> None:
    for pid in _live_pids():
        _stop_pid(pid)
    if PID_PATH.exists():
        PID_PATH.unlink()


def restart() -> int:
    stop()
    return start()


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
    else:
        raise SystemExit(f'unknown command: {cmd}')
