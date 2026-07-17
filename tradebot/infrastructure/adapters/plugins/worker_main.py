"""Entry point executed INSIDE the isolated strategy subprocess.

Reads one JSON request from stdin, loads the (already statically validated)
bundle's ``strategy.py``, invokes the strategy, and writes one JSON response
to stdout. This module is the only place generated code is ever imported, and
it runs under ``python -I`` with a sanitized environment and a temporary
working directory (see worker.py).

On POSIX, CPU-time and address-space rlimits are applied. On Windows the
parent's wall-clock timeout is the enforcement mechanism (documented
operational warning: the strongest sandbox layer is unavailable there).
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

# Under ``python -I`` PYTHONPATH is ignored, so the worker bootstraps the SDK
# from its own known location inside the repository/package tree. Only this
# one path is added — nothing from the parent environment leaks in.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _apply_rlimits(cpu_seconds: int, memory_bytes: int) -> None:
    try:
        # POSIX-only; on Windows the parent's wall-clock timeout is the backstop.
        import resource  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        return
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))


def _load_strategy(bundle_dir: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "strategy_bundle.strategy", bundle_dir / "strategy.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load strategy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "create_strategy"):
        raise AttributeError("strategy.py must define create_strategy()")
    return module.create_strategy()


def _run(request: dict) -> dict:
    from tradebot.domain.ledger import Side
    from tradebot.domain.market import MarketSnapshot
    from tradebot.domain.strategies import StrategyContext, WalletView

    bundle_dir = Path(request["bundle_dir"])
    strategy = _load_strategy(bundle_dir)

    snap_data = request["snapshot"]
    snapshot = MarketSnapshot(
        snapshot_id=snap_data["snapshot_id"],
        source=snap_data["source"],
        symbol=snap_data["symbol"],
        interval=snap_data["interval"],
        open_time_ms=snap_data["open_time_ms"],
        close_time_ms=snap_data["close_time_ms"],
        is_closed=snap_data["is_closed"],
        open=Decimal(snap_data["open"]),
        high=Decimal(snap_data["high"]),
        low=Decimal(snap_data["low"]),
        close=Decimal(snap_data["close"]),
        volume=Decimal(snap_data["volume"]),
        retrieved_at_ms=snap_data["retrieved_at_ms"],
        source_time_ms=snap_data["source_time_ms"],
    )
    wallet = WalletView(
        quote_cash=Decimal(request["wallet"]["quote_cash"]),
        base_qty=Decimal(request["wallet"]["base_qty"]),
        avg_cost=Decimal(request["wallet"]["avg_cost"]),
    )
    context = StrategyContext(snapshot=snapshot, wallet=wallet)
    state = request.get("state") or strategy.initialize()
    decision = strategy.on_market_snapshot(context, state)

    return {
        "ok": True,
        "intents": [
            {
                "side": i.side.value if isinstance(i.side, Side) else str(i.side),
                "order_type": i.order_type,
                "quantity": str(i.quantity),
                "limit_price": str(i.limit_price) if i.limit_price is not None else None,
                "reason_code": i.reason_code,
            }
            for i in decision.intents
        ],
        "state": decision.state,
    }


def main() -> int:
    _apply_rlimits(cpu_seconds=10, memory_bytes=512 * 1024 * 1024)
    try:
        request = json.loads(sys.stdin.read())
        response = _run(request)
    except Exception as exc:  # deliberate catch-all: report, never crash silently
        response = {"ok": False, "error_category": type(exc).__name__, "error": str(exc)[:500]}
    sys.stdout.write(json.dumps(response))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - subprocess entry
    raise SystemExit(main())
