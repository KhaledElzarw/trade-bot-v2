"""Dev-harness tests: the permanent wallets must be visible AND trading.

Regression for the report that Dark Horse sat at 10,000 with zero trades and
Darkhorse - Daily was missing from the dashboard: the devserver replay now
runs both permanent wallets through the real five-domain committee.
"""

import datetime as dt
from decimal import Decimal

import socket

from tradebot.api.devserver import (
    FIVE_MIN_MS,
    N_CANDLES,
    _candle,
    _committee_evidence,
    _permanent_committee_intent,
    _permanent_runners,
    _port_in_use,
    build_market,
    build_view,
)
from tradebot.application.portfolio import WalletSlot, seed_portfolio
from tradebot.domain.dark_horse import REQUIRED_DOMAINS, DomainStatus
from tradebot.domain.ledger import Side, Wallet
from tradebot.strategies.builtin import BUILTIN_STRATEGIES

NOW = dt.datetime(2026, 7, 20, 12, 0, 0)


def _window(*, rising: bool, n: int = 60):
    step = 80.0 if rising else -80.0
    return tuple(_candle(i, 60_000.0 + i * step, 20, 20, 10) for i in range(n))


def _runner(wallet: Wallet):
    slot = WalletSlot(wallet=wallet, kind="dark_horse", strategy_name="DarkHorse",
                      strategy_version_id="dh-v1", activated_at=NOW)
    from tradebot.api.devserver import _PermanentRunner
    return _PermanentRunner(slot=slot, cadence_seconds=4 * 3600,
                            accumulate_fraction=Decimal("0.25"),
                            reduce_fraction=Decimal("0.50"))


def test_committee_evidence_covers_all_five_domains_with_honest_labels():
    reports, signals, _now = _committee_evidence(_window(rising=True))
    assert set(reports) == set(REQUIRED_DOMAINS) == set(signals)
    for domain, report in reports.items():
        assert report.status is DomainStatus.OK
        source = report.items[0].source_id
        if domain in ("technical", "liquidity_derivatives"):
            assert source == "dev-market"
        else:
            assert source == "dev-harness-demo"  # placeholders never masquerade


def test_uptrend_produces_a_buy_and_downtrend_exits_holdings():
    buy = _permanent_committee_intent(
        _runner(Wallet("dh", quote_cash=Decimal("10000.00"))),
        _window(rising=True)[-1], _window(rising=True))
    assert buy is not None and buy[0] is Side.BUY

    sell = _permanent_committee_intent(
        _runner(Wallet("dh", quote_cash=Decimal("1000.00"),
                       base_qty=Decimal("0.15"))),
        _window(rising=False)[-1], _window(rising=False))
    assert sell is not None and sell[0] is Side.SELL


def test_cadence_gate_blocks_back_to_back_evaluations():
    runner = _runner(Wallet("dh", quote_cash=Decimal("10000.00")))
    window = _window(rising=True)
    assert _permanent_committee_intent(runner, window[-1], window) is not None
    # Same candle again: inside the 4h cadence -> no re-evaluation.
    assert _permanent_committee_intent(runner, window[-1], window) is None


def test_permanent_runners_cover_both_wallets_with_daily_tuned_cadence():
    names = [c().metadata().name for c in BUILTIN_STRATEGIES]
    portfolio = seed_portfolio(names, now=NOW, id_factory=lambda h: f"w-{h}")
    runners = _permanent_runners(portfolio)
    kinds = {r.slot.kind: r for r in runners}
    assert set(kinds) == {"dark_horse", "dark_horse_daily"}
    assert kinds["dark_horse"].cadence_seconds == 4 * 3600
    # Daily runs on its tunable signal cadence (default 1h), not the 4h one.
    assert kinds["dark_horse_daily"].cadence_seconds == 3600


def test_build_view_replay_trades_both_permanent_wallets():
    view = build_view(NOW)
    summary = view.portfolio_summary()
    assert summary["dark_horse"] is not None
    assert summary["dark_horse_daily"] is not None
    assert summary["dark_horse_daily"]["display_name"] == "Darkhorse - Daily"
    # Deterministic seeded replay: both permanent wallets actually traded.
    assert view.portfolio.dark_horse.wallet.base_qty > 0
    assert view.portfolio.dark_horse_daily.wallet.base_qty > 0


def test_build_market_anchors_last_candle_close_to_end_ms():
    end_ms = 1_800_000_000_000  # arbitrary wall-clock anchor
    market = build_market(end_ms=end_ms)
    assert len(market) == N_CANDLES
    # Last candle closes exactly at the anchor; candle 0 opens N candles earlier.
    assert market[-1].close_time_ms == end_ms
    assert market[0].open_time_ms == end_ms - N_CANDLES * FIVE_MIN_MS
    # Legacy epoch-relative timeline is preserved when no anchor is given.
    assert build_market()[0].open_time_ms == 0


def test_replay_produces_resting_open_orders():
    view = build_view(NOW)  # synthetic, deterministic seed
    total = sum(len(view.wallet_open_orders(w["wallet_id"]))
                for w in view.wallets())
    assert total >= 1, "expected at least one resting limit order at the snapshot"
    for w in view.wallets():
        for o in view.wallet_open_orders(w["wallet_id"]):
            assert o["order_type"] == "LIMIT" and o["status"] == "open"
            assert Decimal(o["limit_price"]) > 0


def test_synthetic_history_timestamps_are_recent_not_1970():
    view = build_view(NOW)  # synthetic (offline) mode
    orders = view.wallet_orders(view.portfolio.dark_horse.wallet.wallet_id)
    assert orders, "expected recorded trades"
    # Newest trade lands on the harness 'now' day, not the 1970 epoch.
    assert orders[0]["placed_at"].startswith("2026-07-")


def test_port_in_use_detects_a_bound_socket():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert _port_in_use("127.0.0.1", port) is True
    # Once released, the same port is free again.
    assert _port_in_use("127.0.0.1", port) is False
