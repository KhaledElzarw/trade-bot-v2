"""Local development server for manual dashboard/API testing.

Seeds an in-memory 26-wallet portfolio (12 active + 12 shadow + Dark Horse +
Darkhorse - Daily), replays a deterministic synthetic market through the real
ExecutionService so the wallets hold genuine balances, then serves the API +
dashboard. The two permanent wallets trade through the REAL five-domain
committee (`tradebot.application.dark_horse.synthesize`); the technical and
liquidity domains are derived from the actual candle window, while the
macro/fundamental/onchain feeds do not exist in the dev harness and carry
clearly-labelled synthetic placeholder evidence instead.

Market data is either:

* ``--live``  — real BTCUSDT candles from Binance's public endpoint. **No API
  key**: public market data needs none, and requiring exchange credentials for a
  paper platform was audit finding A10. Real exchange filters (tick/lot/notional)
  are fetched too, and the in-progress candle is excluded. If live data cannot be
  obtained the server **fails loudly** rather than silently serving fake prices.
* default — a seeded synthetic walk, touching no network.

This is a DEV harness, not a production entrypoint:

* state is in-memory and vanishes on exit — no runtime database is created or
  modified;
* it binds loopback only;
* it backfills history once at startup and then re-marks equity every 15s from
  the newest closed candle; it does not re-run strategy decisions on new bars.

Run:  python -m tradebot.api.devserver --port 5555 --live
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import threading
import time
from decimal import Decimal

from dataclasses import dataclass

from ..application.dark_horse import (
    DEFAULT_CADENCE_SECONDS,
    DomainSignal,
    synthesize,
)
from ..application.execution import (
    ExecutionModel,
    ExecutionService,
    OrderIntent,
    OrderType,
)
from ..application.portfolio import WalletSlot, seed_portfolio
from ..domain.dark_horse import (
    LIQUIDITY,
    REQUIRED_DOMAINS,
    TECHNICAL,
    DarkHorseAction,
    DomainReport,
    DomainStatus,
    EvidenceItem,
)
from ..domain.dark_horse_daily import default_params
from ..domain.ledger import Side
from ..domain.market import MarketSnapshot
from ..domain.money import base as base_qty
from ..domain.money import quote
from ..domain.strategies import StrategyContext, WalletView
from ..strategies.builtin import BUILTIN_STRATEGIES
from .app import create_app
from .security import ApiSettings
from .views import InMemoryPortfolioView

# nosec B105 - not a credential: a fixed, published, loopback-only dev token so
# the operator can exercise the guarded mutation routes. Real deployments read
# TRADEBOT_API_TOKEN from the environment, and a non-loopback bind refuses to
# start without a strong one (see api/security.py::validate_startup).
DEV_TOKEN = "dev-local-token-not-a-secret-0123456789"  # nosec B105
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
    # nosec B311 - deterministic REPRODUCIBILITY is the point here; this seeds a
    # synthetic demo market, never a security or trading decision.
    rng = random.Random(seed)  # nosec B311
    px = 60_000.0
    out = []
    for i in range(N_CANDLES):
        px *= 1 + rng.uniform(-0.004, 0.0043)
        out.append(_candle(i, px, rng.uniform(5, 60), rng.uniform(5, 60),
                           rng.uniform(5, 30)))
    return tuple(out)


def build_live_market(interval: str = "5m", limit: int = 1000):
    """Real BTCUSDT candles from Binance's public endpoint (no credentials).

    Returns (closed_snapshots, filters, source_note). Raises on failure so the
    caller can decide whether to fall back — we never silently pretend live data
    was obtained.
    """

    from ..infrastructure.market_data.binance_public import (
        closed_only,
        fetch_exchange_filters,
        fetch_klines,
    )

    filters = fetch_exchange_filters()
    snapshots = fetch_klines(interval=interval, limit=limit)
    closed = closed_only(snapshots)
    if not closed:
        raise MarketDataUnavailable("no closed candles returned")
    dropped = len(snapshots) - len(closed)
    note = (f"binance public {interval}, {len(closed)} closed candles "
            f"({dropped} in-progress excluded)")
    return closed, filters, note


class MarketDataUnavailable(RuntimeError):
    pass


# ---- permanent-wallet committee (Dark Horse + Darkhorse - Daily) -----------

_FLAT_EPSILON = Decimal("0.001")  # <0.1% drift = no directional call


def _committee_evidence(
    window: tuple[MarketSnapshot, ...],
) -> tuple[dict[str, DomainReport], dict[str, DomainSignal], dt.datetime]:
    """Five-domain evidence for the dev harness.

    ``technical`` and ``liquidity_derivatives`` are genuinely derived from the
    candle window (short-horizon drift). The macro/fundamental/onchain feeds do
    not exist in the dev harness, so those domains carry synthetic placeholder
    evidence following the long-window drift, with ``source_id`` labelling them
    as such — the REAL committee logic is exercised, but nothing pretends the
    dev harness has production data feeds.
    """

    last = window[-1]
    now = dt.datetime.fromtimestamp(last.close_time_ms / 1000,
                                    dt.timezone.utc).replace(tzinfo=None)
    closes = [c.close for c in window]

    def drift(n: int) -> Decimal:
        seg = closes[-min(n, len(closes)):]
        return (seg[-1] - seg[0]) / seg[0]

    # Each domain reads its own horizon so the evidence is not one number
    # repeated five times: technical/liquidity are short-horizon reads of the
    # real candles; the placeholder domains follow progressively longer drifts.
    horizons = {TECHNICAL: 24, LIQUIDITY: 36}
    placeholder_horizons = {"macro": 288, "bitcoin_fundamental": 144,
                            "onchain": 72}
    market_derived = {d: drift(n) for d, n in horizons.items()}
    moves = {d: market_derived.get(d,
                                   drift(placeholder_horizons.get(d, len(closes))))
             for d in REQUIRED_DOMAINS}

    reports: dict[str, DomainReport] = {}
    signals: dict[str, DomainSignal] = {}
    for domain, move in moves.items():
        confidence = min(Decimal("0.85"),
                         Decimal("0.50") + min(abs(move) * 25, Decimal("0.35")))
        derived = domain in market_derived
        reports[domain] = DomainReport(domain, DomainStatus.OK, (EvidenceItem(
            source_id="dev-market" if derived else "dev-harness-demo",
            metric=f"{domain}_drift",
            value=f"{move:.6f}",
            interpretation=("candle-window drift" if derived
                            else "synthetic dev placeholder"),
            confidence=confidence,
            source_time=now,
            retrieved_at=now,
            data_snapshot_id=last.snapshot_id,
        ),))
        bullish = None if abs(move) < _FLAT_EPSILON else move > 0
        signals[domain] = DomainSignal(domain, bullish, confidence)
    return reports, signals, now


@dataclass
class _PermanentRunner:
    """Cadenced committee loop for one permanent wallet."""

    slot: WalletSlot
    cadence_seconds: int
    accumulate_fraction: Decimal
    reduce_fraction: Decimal
    last_eval_ms: int | None = None


def _permanent_committee_intent(
    runner: _PermanentRunner,
    snapshot: MarketSnapshot,
    window: tuple[MarketSnapshot, ...],
) -> tuple[Side, Decimal, str] | None:
    """Evaluate the committee on cadence; map the decision to a spot order."""

    if (runner.last_eval_ms is not None
            and snapshot.close_time_ms - runner.last_eval_ms
            < runner.cadence_seconds * 1000):
        return None
    runner.last_eval_ms = snapshot.close_time_ms

    wallet = runner.slot.wallet
    reports, signals, now = _committee_evidence(window)
    decision = synthesize(
        reports, signals, now=now,
        strategy_version_id=runner.slot.strategy_version_id,
        holds_btc=wallet.base_qty > 0,
    )
    px = snapshot.mark_price
    if decision.action is DarkHorseAction.ACCUMULATE:
        budget = quote(wallet.quote_cash * runner.accumulate_fraction)
        if budget < Decimal("10"):
            return None
        qty = base_qty(budget / px)
        side = Side.BUY
    elif decision.action is DarkHorseAction.REDUCE:
        qty = base_qty(wallet.base_qty * runner.reduce_fraction)
        side = Side.SELL
    elif decision.action is DarkHorseAction.EXIT_TO_CASH:
        qty = base_qty(wallet.base_qty)
        side = Side.SELL
    else:
        return None
    if qty <= 0:
        return None
    return side, qty, decision.action.value


def _permanent_runners(portfolio) -> list[_PermanentRunner]:
    """Dark Horse on its 4h cadence; Darkhorse - Daily on its tuned cadence."""

    runners = []
    if portfolio.dark_horse is not None:
        runners.append(_PermanentRunner(
            slot=portfolio.dark_horse,
            cadence_seconds=DEFAULT_CADENCE_SECONDS,
            accumulate_fraction=Decimal("0.25"),
            reduce_fraction=Decimal("0.50"),
        ))
    if portfolio.dark_horse_daily is not None:
        params = default_params()
        runners.append(_PermanentRunner(
            slot=portfolio.dark_horse_daily,
            cadence_seconds=int(params["signal_cadence_hours"] * 3600),
            accumulate_fraction=params["accumulate_fraction"],
            reduce_fraction=params["reduce_fraction"],
        ))
    return runners


def build_view(now: dt.datetime, live: bool = False,
               interval: str = "5m") -> InMemoryPortfolioView:
    filters = None
    market_note = "synthetic seeded walk (no network)"
    market_status = "synthetic"

    if live:
        try:
            market, filters, market_note = build_live_market(interval=interval)
            market_status = "ok"
            print(f"[devserver] LIVE market data: {market_note}")
            print(f"[devserver] real exchange filters: tick={filters.tick_size} "
                  f"step={filters.step_size} minNotional={filters.min_notional}")
        except Exception as exc:
            # Be loud and honest rather than silently serving fake prices.
            print(f"[devserver] LIVE market data FAILED ({type(exc).__name__}: "
                  f"{exc}) -> refusing to fall back silently")
            raise
    else:
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
    permanents = _permanent_runners(portfolio)

    # Live mode uses the REAL exchange filters (Binance's actual LOT_SIZE step is
    # 0.00001, not the 1-satoshi default), so fills obey the true venue rules.
    execution = (ExecutionService(model=ExecutionModel(filters=filters))
                 if filters else ExecutionService())
    fills = 0
    seq = 0
    n = len(market)
    for tick in range(1, n + 1):
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
        # Permanent wallets trade against the SAME snapshot, via the real
        # five-domain committee on their own cadences.
        for pr in permanents:
            order = _permanent_committee_intent(pr, snapshot, window)
            if order is None:
                continue
            side, qty, reason = order
            seq += 1
            batch.append((pr.slot.wallet, OrderIntent(
                intent_id=f"dev-i{seq}", wallet_id=pr.slot.wallet.wallet_id,
                strategy_version_id=pr.slot.strategy_version_id,
                side=side, order_type=OrderType.MARKET,
                quantity=qty, limit_price=None,
                reason_code=f"committee_{reason}",
            )))
        for result in execution.process_tick(snapshot, batch):
            fills += result.accepted

    mark = market[-1].mark_price
    print(f"[devserver] replayed {n} candles across "
          f"{len(portfolio.active) + len(portfolio.shadow) + len(permanents)} "
          f"wallets -> {fills} fills")
    print(f"[devserver] mark price {mark}  active equity "
          f"{portfolio.active_equity(mark)}  shadow {portfolio.shadow_equity(mark)}")
    for pr in permanents:
        w = pr.slot.wallet
        print(f"[devserver] {pr.slot.strategy_name}: equity {w.equity(mark)}  "
              f"btc {w.base_qty}  usdt {w.quote_cash}")

    llm_healthy, llm_model = probe_local_llm()

    return InMemoryPortfolioView(
        portfolio=portfolio, mark_price=mark, now=now,
        archived_lifetime_pnl=Decimal("0.00"),
        llm_healthy=llm_healthy,
        llm_model_id=llm_model,
        source_status=[
            {"source_id": "binance_public", "status": market_status,
             "note": market_note},
            {"source_id": "llama_cpp", "status": "ok" if llm_healthy else "degraded",
             "note": f"local model: {llm_model}"},
        ],
    )


def start_market_refresher(view: InMemoryPortfolioView, interval: str = "5m",
                           period_seconds: float = 15.0) -> threading.Thread:
    """Keep the mark price current from live Binance data.

    Without this the mark is frozen at whatever the startup backfill saw, so the
    dashboard would show live-sourced but stale prices. Re-marks every wallet's
    equity against the newest CLOSED candle (the in-progress bar is still never
    used for marking, matching the evaluation rules).

    Failures leave the last good mark in place and flag the source as degraded —
    we never invent a price.
    """

    from ..infrastructure.market_data.binance_public import closed_only, fetch_klines

    def _loop() -> None:
        while True:
            try:
                closed = closed_only(fetch_klines(interval=interval, limit=2))
                if closed:
                    newest = closed[-1]
                    view.mark_price = newest.close
                    view.now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                    _set_source(view, "binance_public", "ok",
                                f"live {interval}; marked at closed candle "
                                f"{newest.close_time_ms}")
            except Exception as exc:  # keep serving the last good mark
                _set_source(view, "binance_public", "degraded",
                            f"refresh failed: {type(exc).__name__}")
            time.sleep(period_seconds)

    thread = threading.Thread(target=_loop, name="market-refresher", daemon=True)
    thread.start()
    return thread


def _set_source(view: InMemoryPortfolioView, source_id: str, status: str,
                note: str) -> None:
    for entry in view.source_status:
        if entry.get("source_id") == source_id:
            entry["status"] = status
            entry["note"] = note
            return


def probe_local_llm() -> tuple[bool, str]:
    """Live health + model discovery against the configured llama.cpp host.

    Goes through the real LlamaCppClient over an httpx transport that is
    constrained to the DataBroker's single permitted private destination. If the
    model is down we report it as degraded rather than pretending.
    """

    import httpx

    from ..infrastructure.data_broker.policy import PolicyViolation, validate_request
    from ..infrastructure.llm.llama_cpp_client import LlamaCppClient, LlmConfig

    config = LlmConfig()

    class HttpxTransport:
        """Every URL is revalidated against the allowlist before it is sent."""

        def get(self, url: str) -> tuple[int, dict]:
            validate_request(url, "GET", resolver=lambda h: [h])
            r = httpx.get(url, timeout=5.0)
            return r.status_code, (r.json() if r.content else {})

        def post(self, url: str, payload: dict) -> tuple[int, dict]:
            validate_request(url, "POST", resolver=lambda h: [h])
            r = httpx.post(url, json=payload, timeout=60.0)
            return r.status_code, (r.json() if r.content else {})

    client = LlamaCppClient(HttpxTransport(), config)
    try:
        if not client.health():
            print("[devserver] local model: health check failed -> degraded")
            return False, "unavailable"
        model_id = client.discover_model()
    except (httpx.HTTPError, PolicyViolation, Exception) as exc:
        print(f"[devserver] local model unreachable ({type(exc).__name__}) -> degraded")
        return False, "unavailable"

    print(f"[devserver] local model OK: served id '{model_id}' "
          f"(artifact {config.expected_model_artifact})")
    return True, model_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradebot-devserver")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--live", action="store_true",
                        help="fetch real BTCUSDT candles from Binance public "
                             "(no credentials required)")
    parser.add_argument("--interval", default="5m")
    args = parser.parse_args(argv)

    import uvicorn

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    view = build_view(now, live=args.live, interval=args.interval)
    if args.live:
        start_market_refresher(view, interval=args.interval)
        print(f"[devserver] market refresher running (every 15s, {args.interval} "
              f"closed candles)")

    settings = ApiSettings(host=args.host, port=args.port, auth_token=DEV_TOKEN)
    app = create_app(view, settings)

    print(f"[devserver] dashboard  http://{args.host}:{args.port}/")
    print(f"[devserver] api        http://{args.host}:{args.port}/api/v2/portfolio/summary")
    print(f"[devserver] mutation token: {DEV_TOKEN}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(main())
