"""Parent-side runner for isolated strategy subprocess workers.

Generated plugins NEVER load into the caller's process. Each invocation:

* launches ``python -I`` (isolated mode: no user site, no env PYTHONPATH),
* with a sanitized environment (only a controlled PYTHONPATH to reach the SDK),
* a temporary working directory,
* a hard wall-clock timeout (kill on expiry),
* typed JSON-over-stdio IPC (single request, single response).

Malformed output, timeout, or nonzero exit produce a structured failure the
caller can quarantine on — the parent never raises into the trading loop.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - launching the sandbox IS this module's purpose
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ....domain.ledger import Wallet
from ....domain.market import MarketSnapshot

_WORKER_MAIN = Path(__file__).with_name("worker_main.py")

DEFAULT_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class WorkerResult:
    ok: bool
    intents: tuple[dict, ...] = ()
    state: dict | None = None
    error_category: str | None = None
    error: str | None = None


def _snapshot_payload(s: MarketSnapshot) -> dict:
    return {
        "snapshot_id": s.snapshot_id,
        "source": s.source,
        "symbol": s.symbol,
        "interval": s.interval,
        "open_time_ms": s.open_time_ms,
        "close_time_ms": s.close_time_ms,
        "is_closed": s.is_closed,
        "open": str(s.open),
        "high": str(s.high),
        "low": str(s.low),
        "close": str(s.close),
        "volume": str(s.volume),
        "retrieved_at_ms": s.retrieved_at_ms,
        "source_time_ms": s.source_time_ms,
    }


def run_strategy_in_worker(
    bundle_dir: Path,
    snapshot: MarketSnapshot,
    wallet: Wallet,
    state: dict | None = None,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    python_executable: str | None = None,
) -> WorkerResult:
    """Execute one strategy tick in an isolated subprocess."""

    request = json.dumps(
        {
            "bundle_dir": str(bundle_dir.resolve()),
            "snapshot": _snapshot_payload(snapshot),
            "wallet": {
                "quote_cash": str(wallet.quote_cash),
                "base_qty": str(wallet.base_qty),
                "avg_cost": str(wallet.avg_cost),
            },
            "state": state,
        }
    )

    exe = python_executable or sys.executable
    with tempfile.TemporaryDirectory(prefix="strategy-worker-") as tmp:
        # Sanitized environment: only the minimal OS variables CPython needs to
        # start (no user env, no credentials leak). SDK path is bootstrapped by
        # worker_main itself because ``-I`` ignores PYTHONPATH.
        env: dict[str, str] = {}
        for os_var in ("SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP"):
            value = os.environ.get(os_var)
            if value is not None:
                env[os_var] = value
        try:
            # nosec B603 - argv is a fixed list (no shell, no untrusted input):
            # `exe` is our own interpreter and `_WORKER_MAIN` is a constant path
            # inside this package. The untrusted bundle is passed as DATA on
            # stdin, never as an argument, and has already passed AST validation.
            proc = subprocess.run(  # nosec B603
                [exe, "-I", "-E", str(_WORKER_MAIN)],
                input=request,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=tmp,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return WorkerResult(ok=False, error_category="Timeout",
                                error=f"worker exceeded {timeout_seconds}s")

    if proc.returncode != 0:
        return WorkerResult(ok=False, error_category="WorkerCrash",
                            error=(proc.stderr or "")[:500])
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return WorkerResult(ok=False, error_category="MalformedOutput",
                            error=proc.stdout[:200])
    if not isinstance(payload, dict) or "ok" not in payload:
        return WorkerResult(ok=False, error_category="MalformedOutput",
                            error="missing ok field")
    if not payload["ok"]:
        return WorkerResult(
            ok=False,
            error_category=str(payload.get("error_category", "Unknown")),
            error=str(payload.get("error", ""))[:500],
        )
    intents = payload.get("intents", [])
    if not isinstance(intents, list):
        return WorkerResult(ok=False, error_category="MalformedOutput",
                            error="intents not a list")
    return WorkerResult(ok=True, intents=tuple(intents),
                        state=payload.get("state") or {})
