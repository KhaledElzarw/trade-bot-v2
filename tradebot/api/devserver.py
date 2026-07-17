"""Local development server for manual dashboard/API testing.

Seeds an in-memory 25-wallet portfolio (12 active + 12 shadow + Dark Horse),
replays a deterministic synthetic market through the real ExecutionService so
the wallets hold genuine balances, then serves the API + dashboard.

This is a DEV harness, not a production entrypoint:

* the market is a seeded synthetic walk, not live data (no network is touched);
* state is in-memory and vanishes on exit — no runtime database is created or
  modified;
* it binds loopback only.

Run:  python -m tradebot.api.devserver --port 5555
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
from decimal import Decimal

from ..application.execution import ExecutionService, OrderIntent, OrderType
from ..application.portfolio import seed_portfolio
from ..domain.market import MarketSnapshot
from ..domain.strategies import StrategyContext, WalletView
from ..strategies.builtin import BUILTIN_STRATEGIES
from .app import create_app
from .security import ApiSettings
from .views import InMemoryPortfolioView

DEV_TOKEN = "dev-local-token-not-a-secret-0123456789"
N_CANDLES = 400
WINDOW = 150


def _candle(i: int, close: float, hi: float, lo: float, vol: float) -> MarketSnapshot:
    c = Decimal(f"{close:.2f}")
    return MarketSnapshot(
        snapshot_id=f"dev-c{i}", source="synthetic-dev", symbol="BTCUSDT",
        interval="5m", open_time_ms=i * 300_000, close_time_ms=(i + 1) * 300_000,
        is_closed=True, open=c, high=c + Decimal(f"{hi:.2f}"),
        low=c - Decimal(f"{lo:.2f}"), close=c, volume=Decimal(f"{vol:.2f}"),
        retrieved_at_ms=(i + 1) * 300_000, source_time_ms=(i + 1) * 300_000,
    )


def build_market(seed: int = 7) -> tuple[MarketSnapshot, ...]:
    rng = random.Random(seed)
    px = 60_000.0
    out = []
    for i in range(N_CANDLES):
        px *= 1 + rng.uniform(-0.004, 0.0043)
        out.append(_candle(i, px, rng.uniform(5, 60), rng.uniform(5, 60),
                           rng.uniform(5, 30)))
    return tuple(out)


def build_view(now: dt.datetime) -> InMemoryPortfolioView:
    market = build_market()
    names = [c().metadata().name for c in BUILTIN_STRATEGIES]
    # Assignments are backdated so the display-name day counter is non-zero.
    portfolio = seed_portfolio(names, now=now - dt.timedelta(days=3),
                               id_factory=lambda h: f"w-{h}")
    by_name = {c().metadata().name: c for c in BUILTIN_STRATEGIES}
    runners = []
    for slot in portfolio.active + portfolio.shadow:
        strategy = by_name[slot.strategy_name]()
        runners.append((slot, strategy, strategy.initialize()))

    execution = ExecutionService()
    fills = 0
    seq = 0
    for tick in range(1, N_CANDLES + 1):
        snapshot = market[tick - 1]
        window = market[max(0, tick - WINDOW):tick]
        batch = []
        for idx, (slot, strategy, state) in enumerate(runners):
            w = slot.wallet
            ctx = StrategyContext(
                snapshot=snapshot,
                wallet=WalletView(w.quote_cash, w.base_qty, w.avg_cost),
                candles=window,
            )
            decision = strategy.on_market_snapshot(ctx, state)
            runners[idx] = (slot, strategy, decision.state)
            for spec in decision.intents:
                seq += 1
                batch.append((w, OrderIntent(
                    intent_id=f"dev-i{seq}", wallet_id=w.wallet_id,
                    strategy_version_id=slot.strategy_version_id,
                    side=spec.side, order_type=OrderType(spec.order_type),
                    quantity=spec.quantity, limit_price=spec.limit_price,
                    reason_code=spec.reason_code,
                )))
        for result in execution.process_tick(snapshot, batch):
            fills += result.accepted

    mark = market[-1].mark_price
    print(f"[devserver] replayed {N_CANDLES} candles across "
          f"{len(portfolio.active) + len(portfolio.shadow)} wallets -> {fills} fills")
    print(f"[devserver] mark price {mark}  active equity "
          f"{portfolio.active_equity(mark)}  shadow {portfolio.shadow_equity(mark)}")

    return InMemoryPortfolioView(
        portfolio=portfolio, mark_price=mark, now=now,
        archived_lifetime_pnl=Decimal("0.00"),
        llm_healthy=False,  # honest: no local model is running in this harness
        llm_model_id="unavailable",
        source_status=[
            {"source_id": "binance_public", "status": "not_contacted",
             "note": "dev harness uses a synthetic market; no network calls"},
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradebot-devserver")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)

    import uvicorn

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    view = build_view(now)
    settings = ApiSettings(host=args.host, port=args.port, auth_token=DEV_TOKEN)
    app = create_app(view, settings)

    print(f"[devserver] dashboard  http://{args.host}:{args.port}/")
    print(f"[devserver] api        http://{args.host}:{args.port}/api/v2/portfolio/summary")
    print(f"[devserver] mutation token: {DEV_TOKEN}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(main())
