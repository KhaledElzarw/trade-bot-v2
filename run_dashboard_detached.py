import subprocess
import sys
from pathlib import Path

import wrapper_runner

BASE = Path(__file__).resolve().parent
PID_PATH = BASE / 'dashboard.pid'
LOG_PATH = BASE / 'dashboard.nohup.out'
PYTHON = str(BASE / '.venv' / 'bin' / 'python')
DASHBOARD = str(BASE / 'dashboard_server.py')
PORT = 8844


def get_python_executable() -> str:
    return wrapper_runner.get_python_executable(PYTHON)


def _live_dashboard_pids():
    try:
        out = subprocess.check_output(['pgrep', '-f', DASHBOARD], text=True)
        return [int(x.strip()) for x in out.splitlines() if x.strip()]
    except subprocess.CalledProcessError:
        return []


def _pid_is_alive(pid: int) -> bool:
    return wrapper_runner.pid_alive(pid)


def _stop_pid(pid: int, timeout: float = 5.0) -> None:
    wrapper_runner.stop_pid(pid, timeout=timeout)


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
    pid = wrapper_runner.start_detached(get_python_executable(), DASHBOARD, BASE, LOG_PATH)
    wrapper_runner.write_pid(PID_PATH, pid)
    return pid


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
        wrapper_runner.write_pid(PID_PATH, newest)
        return newest
    return _start_detached()


def stop() -> None:
    live = sorted(set(_live_dashboard_pids()))
    port_pid = _listening_pid_on_port()
    if port_pid is not None:
        live.append(port_pid)
    for pid in sorted(set(live)):
        _stop_pid(pid)
    wrapper_runner.unlink_pid(PID_PATH)


def restart() -> int:
    stop()
    return _start_detached()


def status() -> int:
    port_pid = _listening_pid_on_port()
    if port_pid:
        return port_pid
    live = _live_dashboard_pids()
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
    if cmd == 'log-reset':
        LOG_PATH.write_text('', encoding='utf-8')
        print('log-reset')
        return
    raise SystemExit(f'unknown command: {cmd}')


if __name__ == '__main__':
    main()
