import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
PID_PATH = BASE / 'dashboard.pid'
LOG_PATH = BASE / 'dashboard.nohup.out'
PYTHON = str(BASE / '.venv' / 'bin' / 'python')
DASHBOARD = str(BASE / 'dashboard_server.py')
PORT = 8844


def _live_dashboard_pids():
    try:
        out = subprocess.check_output(['pgrep', '-f', DASHBOARD], text=True)
        return [int(x.strip()) for x in out.splitlines() if x.strip()]
    except subprocess.CalledProcessError:
        return []


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _stop_pid(pid: int, timeout: float = 5.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_is_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _listening_pid_on_port() -> int | None:
    try:
        out = subprocess.check_output(['ss', '-ltnp', f'( sport = :{PORT} )'], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    for line in out.splitlines():
        marker = 'pid='
        if marker in line:
            tail = line.split(marker, 1)[1]
            pid_str = ''
            for ch in tail:
                if ch.isdigit():
                    pid_str += ch
                else:
                    break
            if pid_str:
                return int(pid_str)
    return None


def _start_detached() -> int:
    with open(LOG_PATH, 'ab', buffering=0) as log:
        proc = subprocess.Popen(
            [PYTHON, DASHBOARD],
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


def start() -> int:
    port_pid = _listening_pid_on_port()
    live = _live_dashboard_pids()
    if port_pid and port_pid not in live:
        live.append(port_pid)
    if live:
        newest = max(live)
        for pid in sorted(set(live)):
            if pid != newest:
                _stop_pid(pid)
        PID_PATH.write_text(str(newest))
        return newest
    return _start_detached()


def stop() -> None:
    live = sorted(set(_live_dashboard_pids()))
    port_pid = _listening_pid_on_port()
    if port_pid is not None:
        live.append(port_pid)
    for pid in sorted(set(live)):
        _stop_pid(pid)
    if PID_PATH.exists():
        PID_PATH.unlink()


def restart() -> int:
    stop()
    return _start_detached()


def status() -> int:
    port_pid = _listening_pid_on_port()
    if port_pid:
        PID_PATH.write_text(str(port_pid))
        return port_pid
    live = _live_dashboard_pids()
    if live:
        newest = max(live)
        PID_PATH.write_text(str(newest))
        return newest
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
    if cmd == 'log-reset':
        LOG_PATH.write_text('', encoding='utf-8')
        print('log-reset')
        return
    raise SystemExit(f'unknown command: {cmd}')


if __name__ == '__main__':
    main()
