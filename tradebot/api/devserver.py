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
from ..application.order_book import RestingBook, RestingOrder
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
from .views import InMemoryPortfolioView, money

# nosec B105 - not a credential: a fixed, published, loopback-only dev token so
# the operator can exercise the guarded mutation routes. Real deployments read
# TRADEBOT_API_TOKEN from the environment, and a non-loopback bind refuses to
# start without a strong one (see api/security.py::validate_startup).
DEV_TOKEN = "dev-local-token-not-a-secret-0123456789"  # nosec B105
N_CANDLES = 400
WINDOW = 150
FIVE_MIN_MS = 300_000  # 5-minute candle spacing, in milliseconds


def _candle(i: int, close: float, hi: float, lo: float, vol: float,
            open_ms: int | None = None) -> MarketSnapshot:
    # ``open_ms`` anchors the candle in real time. When omitted it falls back to
    # the legacy epoch-relative spacing (i * 5m), which is fine for unit tests
    # that only care about candle ordering, not wall-clock timestamps.
    open_ms = i * FIVE_MIN_MS if open_ms is None else open_ms
    close_ms = open_ms + FIVE_MIN_MS
    c = Decimal(f"{close:.2f}")
    return MarketSnapshot(
        snapshot_id=f"dev-c{i}", source="synthetic-dev", symbol="BTCUSDT",
        interval="5m", open_time_ms=open_ms, close_time_ms=close_ms,
        is_closed=True, open=c, high=c + Decimal(f"{hi:.2f}"),
        low=c - Decimal(f"{lo:.2f}"), close=c, volume=Decimal(f"{vol:.2f}"),
        retrieved_at_ms=close_ms, source_time_ms=close_ms,
    )


def build_market(seed: int = 7, *,
                 end_ms: int | None = None) -> tuple[MarketSnapshot, ...]:
    """Deterministic synthetic BTCUSDT walk.

    ``end_ms`` is the close time of the LAST candle. Pass ``now`` (aligned to a
    5-minute boundary) so the synthetic history reads with real recent
    timestamps offline, matching how live mode looks. Omit it for the legacy
    epoch-relative timeline used by tests.
    """

    # nosec B311 - deterministic REPRODUCIBILITY is the point here; this seeds a
    # synthetic demo market, never a security or trading decision.
    rng = random.Random(seed)  # nosec B311
    if end_ms is None:
        start_open_ms = 0
    else:
        # end_ms is the last candle's CLOSE; walk back N candles to candle 0's open.
        start_open_ms = end_ms - N_CANDLES * FIVE_MIN_MS
    px = 60_000.0
    out = []
    for i in range(N_CANDLES):
        px *= 1 + rng.uniform(-0.004, 0.0043)
        out.append(_candle(i, px, rng.uniform(5, 60), rng.uniform(5, 60),
                           rng.uniform(5, 30),
                           open_ms=start_open_ms + i * FIVE_MIN_MS))
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


# ---- per-wallet trade recording (drill-down data) --------------------------


def _iso(ms: int) -> str:
    return (dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
            .replace(tzinfo=None).isoformat() + "Z")


def _realized_from_txn(txn) -> Decimal:
    """Realized P&L booked by a fill (0 for buys). The sell posts a
    ``realized_pnl`` leg equal to ``-realized`` (credit convention)."""

    return -sum((p.amount for p in txn.postings if p.account == "realized_pnl"),
                start=Decimal("0"))


def _trade_record(snapshot: MarketSnapshot, intent: OrderIntent,
                  result, placed_ms: int | None = None) -> dict:
    """One row of a wallet's order history: what was asked, what happened.

    ``placed_ms`` is the open time of the candle the order was PLACED on — it
    differs from the fill candle for a resting limit order. When omitted the
    order was placed and resolved on the same candle (a market order).
    """

    ts = _iso(snapshot.close_time_ms)
    placed_at = _iso(placed_ms + FIVE_MIN_MS) if placed_ms is not None else ts
    filled = result.accepted
    txn = result.transaction
    price = result.fill_price
    filled_qty = txn.qty if txn is not None else Decimal("0")
    notional = (quote(price * filled_qty)
                if (price is not None and filled) else None)
    return {
        "order_id": intent.intent_id,
        "placed_at": placed_at,
        "filled_at": ts if filled else None,
        "side": intent.side.value,
        "order_type": intent.order_type.value,
        "requested_qty": money(intent.quantity),
        "filled_qty": money(filled_qty),
        "price": money(price) if price is not None else None,
        "notional": money(notional) if notional is not None else None,
        "fee": money(txn.fee) if txn is not None else None,
        "status": "filled" if filled else "rejected",
        "reason": (result.reason.value if result.reason is not None
                   else intent.reason_code),
        "strategy_version_id": intent.strategy_version_id,
        "realized_pnl": (money(_realized_from_txn(txn))
                         if txn is not None else None),
    }


def _expired_record(snapshot: MarketSnapshot, order) -> dict:
    """History row for a resting limit order that timed out unfilled."""

    return {
        "order_id": order.order_id,
        "placed_at": _iso(order.placed_open_ms + FIVE_MIN_MS),
        "filled_at": None,
        "side": order.side.value,
        "order_type": "LIMIT",
        "requested_qty": money(order.quantity),
        "filled_qty": "0.00000000",
        "price": money(order.limit_price),
        "notional": None,
        "fee": None,
        "status": "expired",
        "reason": order.reason_code,
        "strategy_version_id": order.strategy_version_id,
        "realized_pnl": None,
    }


def _strategy_description(strategy_obj) -> str:
    """A short human blurb from the strategy module's docstring."""

    import inspect

    module = inspect.getmodule(type(strategy_obj))
    doc = (module.__doc__ or "").strip() if module is not None else ""
    paras = [" ".join(p.split()) for p in doc.split("\n\n") if p.strip()]
    if not paras:
        return ""
    # Paragraph 0 is the "Strategy N — Title" line; paragraph 1 is the summary.
    return paras[1] if len(paras) > 1 else paras[0]


_PERMANENT_DESCRIPTIONS = {
    "dark-horse-v1": (
        "Permanent wallet trading the real five-domain committee (technical, "
        "liquidity, macro, fundamental, on-chain). It accumulates, reduces, or "
        "exits to cash on a 4-hour cadence and is never reset."),
    "dark-horse-daily-v1": (
        "Permanent wallet that re-tunes its committee parameters every 24 hours "
        "from every wallet's daily lessons, within engine guardrails. Like Dark "
        "Horse it is never reset — only its strategy version advances."),
}


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
    """Cadenced committee loop for one permanent wallet.

    ``entry_limit_bps`` / ``exit_limit_bps`` place accumulate/reduce as RESTING
    limit orders off the mark (0 = market). For Darkhorse - Daily these are read
    from its live params dict and re-tuned by the daily LLM adaptation loop; for
    Dark Horse they are fixed.
    """

    slot: WalletSlot
    cadence_seconds: int
    accumulate_fraction: Decimal
    reduce_fraction: Decimal
    entry_limit_bps: Decimal = Decimal("0")
    exit_limit_bps: Decimal = Decimal("0")
    last_eval_ms: int | None = None


def _permanent_committee_intent(
    runner: _PermanentRunner,
    snapshot: MarketSnapshot,
    window: tuple[MarketSnapshot, ...],
) -> tuple[Side, Decimal, str, Decimal | None] | None:
    """Evaluate the committee on cadence; map the decision to a spot order.

    Returns ``(side, quantity, reason, limit_price)`` — ``limit_price`` is
    ``None`` for a market order. Accumulate rests a bid below the mark and
    reduce rests an ask above it (both tunable); exit-to-cash is always a market
    order because a risk-off exit must not sit unfilled on the book.
    """

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
    limit_price: Decimal | None = None
    if decision.action is DarkHorseAction.ACCUMULATE:
        budget = quote(wallet.quote_cash * runner.accumulate_fraction)
        if budget < Decimal("10"):
            return None
        if runner.entry_limit_bps > 0:
            limit_price = quote(px * (Decimal(1) - runner.entry_limit_bps / Decimal(10_000)))
        qty = base_qty(budget / (limit_price or px))
        side = Side.BUY
    elif decision.action is DarkHorseAction.REDUCE:
        qty = base_qty(wallet.base_qty * runner.reduce_fraction)
        if runner.exit_limit_bps > 0:
            limit_price = quote(px * (Decimal(1) + runner.exit_limit_bps / Decimal(10_000)))
        side = Side.SELL
    elif decision.action is DarkHorseAction.EXIT_TO_CASH:
        qty = base_qty(wallet.base_qty)
        side = Side.SELL  # urgent risk-off -> market (limit_price stays None)
    else:
        return None
    if qty <= 0:
        return None
    return side, qty, decision.action.value, limit_price


def _permanent_runners(portfolio,
                       daily_params: dict | None = None) -> list[_PermanentRunner]:
    """Dark Horse on its 4h cadence; Darkhorse - Daily on its tuned cadence.

    ``daily_params`` overrides Darkhorse - Daily's tunables (fractions, cadence,
    limit offsets); when omitted it uses ``default_params()``. The harness passes
    freshly-adapted params here as the simulated days advance (Part E).

    Dark Horse trades MARKET: a high-conviction 4h committee decision should act
    now, not sit on the book (resting limits are natural for the mean-reversion
    builtins and the adaptive daily wallet, not for a conviction accumulator).
    """

    runners = []
    if portfolio.dark_horse is not None:
        runners.append(_PermanentRunner(
            slot=portfolio.dark_horse,
            cadence_seconds=DEFAULT_CADENCE_SECONDS,
            accumulate_fraction=Decimal("0.25"),
            reduce_fraction=Decimal("0.50"),
        ))
    if portfolio.dark_horse_daily is not None:
        params = daily_params or default_params()
        runners.append(_PermanentRunner(
            slot=portfolio.dark_horse_daily,
            cadence_seconds=int(params["signal_cadence_hours"] * 3600),
            accumulate_fraction=params["accumulate_fraction"],
            reduce_fraction=params["reduce_fraction"],
            entry_limit_bps=params.get("entry_limit_bps", Decimal("0")),
            exit_limit_bps=params.get("exit_limit_bps", Decimal("0")),
        ))
    return runners


def build_view(now: dt.datetime, live: bool = False,
               interval: str = "5m",
               llm_adapt: bool = False) -> InMemoryPortfolioView:
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
        # Anchor the synthetic history so its timestamps end at ``now`` (aligned
        # to a 5-minute boundary), so the order history reads with real recent
        # times offline instead of 1970.
        now_ms = int(now.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        end_ms = now_ms - (now_ms % FIVE_MIN_MS)
        market = build_market(end_ms=end_ms)

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
    book = RestingBook()
    _perm_slots = [p for p in (portfolio.dark_horse, portfolio.dark_horse_daily)
                   if p is not None]
    all_slots = portfolio.active + portfolio.shadow + _perm_slots
    wallet_by_id = {s.wallet.wallet_id: s.wallet for s in all_slots}
    version_by_id = {s.wallet.wallet_id: s.strategy_version_id for s in all_slots}

    # Opt-in: the daily LLM re-tune loop. None => no adaptation (current
    # behaviour, and the graceful fallback when the local model is down).
    retuner = _build_retuner(portfolio) if llm_adapt else None
    current_day: str | None = None
    day_fills: dict[str, list[dict]] = {}

    trades: dict[str, list[dict]] = {}
    fills = 0
    seq = 0
    prev = None
    n = len(market)
    for tick in range(1, n + 1):
        snapshot = market[tick - 1]
        window = market[max(0, tick - WINDOW):tick]

        # Day boundary: close out the completed day's lessons, adapt Darkhorse -
        # Daily, and apply the new params before this day's candles replay.
        day = _iso(snapshot.close_time_ms)[:10]
        if retuner is not None and day != current_day:
            if current_day is not None and prev is not None:
                new_params = retuner.end_day(current_day, wallet_by_id,
                                             prev.mark_price, version_by_id,
                                             day_fills)
                _apply_daily_params(permanents, new_params)
            retuner.begin_day(wallet_by_id, snapshot.mark_price)
            day_fills = {}
            current_day = day

        book.observe_spacing(snapshot, prev)
        prev = snapshot
        batch: list[tuple] = []
        placed_ms_by_id: dict[str, int] = {}

        # (1) Expire resting orders that have sat unfilled past their TTL.
        for order in book.expire(snapshot):
            trades.setdefault(order.wallet_id, []).append(
                _expired_record(snapshot, order))
        # (2) Resting orders the candle traded through become fills this tick.
        for order in book.due_fills(snapshot):
            seq += 1
            iid = f"dev-i{seq}"
            placed_ms_by_id[iid] = order.placed_open_ms
            batch.append((wallet_by_id[order.wallet_id], OrderIntent(
                intent_id=iid, wallet_id=order.wallet_id,
                strategy_version_id=order.strategy_version_id,
                side=order.side, order_type=OrderType.LIMIT,
                quantity=order.quantity, limit_price=order.limit_price,
                reason_code=order.reason_code,
            )))

        def place(wallet, version_id, side, order_type, qty, limit_price, reason):
            """Market -> execute this tick; not-yet-marketable limit -> rest."""
            nonlocal seq
            seq += 1
            iid = f"dev-i{seq}"
            if order_type is OrderType.LIMIT and limit_price is not None:
                # A limit placed at this close cannot fill on its own candle (the
                # high/low already happened) — it rests and is checked next tick.
                book.rest(RestingOrder(
                    order_id=iid, wallet_id=wallet.wallet_id,
                    strategy_version_id=version_id, side=side,
                    limit_price=limit_price, quantity=qty, reason_code=reason,
                    placed_open_ms=snapshot.open_time_ms))
            else:
                batch.append((wallet, OrderIntent(
                    intent_id=iid, wallet_id=wallet.wallet_id,
                    strategy_version_id=version_id, side=side,
                    order_type=order_type, quantity=qty, limit_price=limit_price,
                    reason_code=reason)))

        # (3) Strategy wallets.
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
                place(w, slot.strategy_version_id, spec.side,
                      OrderType(spec.order_type), spec.quantity,
                      spec.limit_price, spec.reason_code)

        # (4) Permanent wallets: the real five-domain committee on their cadences.
        for pr in permanents:
            order = _permanent_committee_intent(pr, snapshot, window)
            if order is None:
                continue
            side, qty, reason, limit_price = order
            place(pr.slot.wallet, pr.slot.strategy_version_id, side,
                  OrderType.LIMIT if limit_price is not None else OrderType.MARKET,
                  qty, limit_price, f"committee_{reason}")

        # (5) Execute everything submitted this tick.
        intents_by_id = {intent.intent_id: intent for _, intent in batch}
        for result in execution.process_tick(snapshot, batch):
            fills += result.accepted
            intent = intents_by_id[result.intent_id]
            record = _trade_record(snapshot, intent, result,
                                   placed_ms=placed_ms_by_id.get(result.intent_id))
            trades.setdefault(result.wallet_id, []).append(record)
            if retuner is not None and result.accepted:
                day_fills.setdefault(result.wallet_id, []).append(record)

    if retuner is not None:
        _print_adaptation_summary(retuner)
    open_orders = book.snapshot_open()
    mark = market[-1].mark_price
    resting = sum(len(v) for v in open_orders.values())
    print(f"[devserver] replayed {n} candles across "
          f"{len(portfolio.active) + len(portfolio.shadow) + len(permanents)} "
          f"wallets -> {fills} fills, {resting} orders still resting")
    print(f"[devserver] mark price {mark}  active equity "
          f"{portfolio.active_equity(mark)}  shadow {portfolio.shadow_equity(mark)}")
    for pr in permanents:
        w = pr.slot.wallet
        print(f"[devserver] {pr.slot.strategy_name}: equity {w.equity(mark)}  "
              f"btc {w.base_qty}  usdt {w.quote_cash}")

    llm_healthy, llm_model = probe_local_llm()

    descriptions = {c().metadata().strategy_version_id: _strategy_description(c())
                    for c in BUILTIN_STRATEGIES}
    descriptions.update(_PERMANENT_DESCRIPTIONS)

    return InMemoryPortfolioView(
        portfolio=portfolio, mark_price=mark, now=now,
        candles=tuple(market),
        archived_lifetime_pnl=Decimal("0.00"),
        trades_by_wallet=trades,
        open_orders_by_wallet=open_orders,
        strategy_descriptions=descriptions,
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


def _build_llm_client():
    """A LlamaCppClient over the allowlist-guarded httpx transport, or None.

    Shared by the health probe and the daily re-tune loop. Returns
    ``(client, model_id)`` on success or ``(None, "unavailable")`` when the model
    is unreachable — callers then degrade rather than pretend.
    """

    import httpx

    from ..infrastructure.data_broker.policy import PolicyViolation, validate_request
    from ..infrastructure.llm.llama_cpp_client import LlamaCppClient, LlmConfig

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

    client = LlamaCppClient(HttpxTransport(), LlmConfig())
    try:
        if not client.health():
            return None, "unavailable"
        return client, client.discover_model()
    except (httpx.HTTPError, PolicyViolation, Exception):
        return None, "unavailable"


def probe_local_llm() -> tuple[bool, str]:
    """Live health + model discovery against the configured llama.cpp host."""

    client, model_id = _build_llm_client()
    if client is None:
        print("[devserver] local model unreachable -> degraded")
        return False, "unavailable"
    print(f"[devserver] local model OK: served id '{model_id}'")
    return True, model_id


def _apply_daily_params(permanents, params: dict) -> None:
    """Push freshly-adapted tunables onto the live Darkhorse - Daily runner."""

    for pr in permanents:
        if pr.slot.kind == "dark_horse_daily":
            pr.accumulate_fraction = params["accumulate_fraction"]
            pr.reduce_fraction = params["reduce_fraction"]
            pr.cadence_seconds = int(params["signal_cadence_hours"] * 3600)
            pr.entry_limit_bps = params["entry_limit_bps"]
            pr.exit_limit_bps = params["exit_limit_bps"]


def _build_retuner(portfolio):
    """Assemble the daily LLM re-tuner, or None if the model is unavailable."""

    from .harness_adaptation import DailyReTuner, LlmAnalyst, LlmProposer

    if portfolio.dark_horse_daily is None:
        return None
    client, model_id = _build_llm_client()
    if client is None:
        print("[devserver] --llm-adapt requested but local model is down "
              "-> daily wallet runs on default params (no adaptation)")
        return None
    print(f"[devserver] --llm-adapt ON: Darkhorse - Daily re-tunes each "
          f"simulated day via '{model_id}'")
    slot = portfolio.dark_horse_daily
    return DailyReTuner(
        daily_wallet_id=slot.wallet.wallet_id,
        daily_version_id=slot.strategy_version_id,
        params=default_params(),
        analyst=LlmAnalyst(client),
        proposer=LlmProposer(client),
    )


def _print_adaptation_summary(retuner) -> None:
    changed = [a for a in retuner.history if not a.degraded and a.changed]
    print(f"[devserver] daily adaptation: {len(retuner.history)} cycles, "
          f"{len(changed)} changed the strategy")
    for a in changed:
        moves = ", ".join(f"{adj.parameter} {adj.previous_value}->{adj.new_value}"
                          for adj in a.adjustments)
        print(f"[devserver]   {a.date}: {moves}")
    daily = retuner.params
    print(f"[devserver] final daily limits: entry_bps={daily['entry_limit_bps']} "
          f"exit_bps={daily['exit_limit_bps']}")


def _port_in_use(host: str, port: int) -> bool:
    """True if something is already listening on host:port.

    Guards against the confusing failure mode where a stale devserver keeps
    serving OLD in-memory code on the port while a fresh start silently loses
    the bind race — the exact trap behind the earlier 'Dark Horse frozen' report.
    """

    import socket

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False  # unresolvable host: let uvicorn surface the real error
    for family, socktype, proto, _canon, sockaddr in infos:
        with socket.socket(family, socktype, proto) as probe:
            try:
                probe.bind(sockaddr)
            except OSError:
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradebot-devserver")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--live", action="store_true",
                        help="fetch real BTCUSDT candles from Binance public "
                             "(no credentials required)")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--llm-adapt", action="store_true",
                        help="re-tune Darkhorse - Daily's parameters (including "
                             "resting-limit offsets) each simulated day via the "
                             "local model's daily lessons; degrades to no-op if "
                             "the model is down. Off by default (deterministic).")
    args = parser.parse_args(argv)

    # Fail fast (before the expensive market replay) if the port is taken, so a
    # stale process can't keep serving old code under a "started fine" illusion.
    if _port_in_use(args.host, args.port):
        print(f"[devserver] ERROR: {args.host}:{args.port} is already in use.")
        print("[devserver] A stale devserver may still be serving OLD code there.")
        print("[devserver] Stop that process (or pass a different --port), then retry.")
        return 1

    import uvicorn

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    view = build_view(now, live=args.live, interval=args.interval,
                      llm_adapt=args.llm_adapt)
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
