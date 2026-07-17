"""Dark Horse tests: five-domain committee, degradation, continuity (Phase 10)."""

import datetime as dt
from decimal import Decimal

import pytest

from tradebot.application.dark_horse import (
    DarkHorseWallet,
    DomainSignal,
    assess_domain,
    due_for_evaluation,
    is_exempt_from_elimination,
    synthesize,
)
from tradebot.application.evolution import plan_replacements
from tradebot.domain.dark_horse import (
    LIQUIDITY,
    MACRO,
    ONCHAIN,
    REQUIRED_DOMAINS,
    TECHNICAL,
    DarkHorseAction,
    DomainReport,
    DomainStatus,
    EvidenceItem,
)
from tradebot.domain.evaluations import WalletEvaluation
from tradebot.domain.ledger import Wallet

NOW = dt.datetime(2026, 7, 17, 12, 0, 0)


def item(source="fred", metric="DFF", age_hours=1, snapshot="snap-1",
         confidence="0.8"):
    src_time = NOW - dt.timedelta(hours=age_hours)
    return EvidenceItem(
        source_id=source, metric=metric, value="5.25",
        interpretation="policy steady", confidence=Decimal(confidence),
        source_time=src_time, retrieved_at=NOW, data_snapshot_id=snapshot,
    )


def report(domain, status=DomainStatus.OK, n=1, age_hours=1):
    items = tuple(item(snapshot=f"{domain}-snap-{i}", age_hours=age_hours)
                  for i in range(n))
    return DomainReport(domain, status, items if status is DomainStatus.OK else ())


def all_reports(age_hours=1):
    return {d: report(d, age_hours=age_hours) for d in REQUIRED_DOMAINS}


def signals(bullish_domains, confidence="0.8"):
    return {
        d: DomainSignal(d, d in bullish_domains, Decimal(confidence))
        for d in REQUIRED_DOMAINS
    }


# ---- five required domains --------------------------------------------------

def test_five_required_domains():
    assert REQUIRED_DOMAINS == (MACRO, TECHNICAL, "bitcoin_fundamental",
                                ONCHAIN, LIQUIDITY)
    assert len(REQUIRED_DOMAINS) == 5


def test_decision_records_all_five_domain_states():
    decision = synthesize(all_reports(), signals(REQUIRED_DOMAINS), now=NOW,
                          strategy_version_id="dh-v1", holds_btc=False)
    assert len(decision.reports) == 5
    assert {r.domain for r in decision.reports} == set(REQUIRED_DOMAINS)
    assert decision.evidence_ids  # every conclusion links to evidence ids


# ---- decisions --------------------------------------------------------------

def test_accumulate_when_all_domains_usable_and_bullish():
    decision = synthesize(all_reports(), signals(REQUIRED_DOMAINS), now=NOW,
                          strategy_version_id="dh-v1", holds_btc=False)
    assert decision.action is DarkHorseAction.ACCUMULATE
    assert not decision.is_degraded


def test_exit_to_cash_when_broadly_bearish_and_holding():
    decision = synthesize(all_reports(), signals(set()), now=NOW,
                          strategy_version_id="dh-v1", holds_btc=True)
    assert decision.action is DarkHorseAction.EXIT_TO_CASH


def test_reduce_when_narrowly_bearish_and_holding():
    bullish = {MACRO, TECHNICAL}  # 2 bull vs 3 bear -> reduce, not full exit
    decision = synthesize(all_reports(), signals(bullish), now=NOW,
                          strategy_version_id="dh-v1", holds_btc=True)
    assert decision.action is DarkHorseAction.REDUCE


def test_hold_is_valid_no_forced_trading():
    """Split evidence -> HOLD. Holding cash is a legitimate outcome."""
    bullish = {MACRO, TECHNICAL}
    decision = synthesize(all_reports(), signals(bullish), now=NOW,
                          strategy_version_id="dh-v1", holds_btc=False)
    assert decision.action is DarkHorseAction.HOLD


def test_no_shorting_actions_exist():
    assert {a.value for a in DarkHorseAction} == {
        "accumulate", "hold", "reduce", "exit_to_cash"}


def test_low_confidence_bullish_does_not_accumulate():
    decision = synthesize(all_reports(), signals(REQUIRED_DOMAINS, "0.3"),
                          now=NOW, strategy_version_id="dh-v1", holds_btc=False)
    assert decision.action is DarkHorseAction.HOLD


# ---- degradation is explicit, never fabricated ------------------------------

def test_missing_domain_is_explicit_and_blocks_accumulation():
    reports = all_reports()
    del reports[ONCHAIN]
    decision = synthesize(reports, signals(REQUIRED_DOMAINS), now=NOW,
                          strategy_version_id="dh-v1", holds_btc=False)
    assert decision.is_degraded
    assert ONCHAIN in decision.degraded_domains
    assert decision.action is DarkHorseAction.HOLD  # uncertainty never buys
    missing = [r for r in decision.reports if r.domain == ONCHAIN][0]
    assert missing.status is DomainStatus.MISSING
    assert missing.items == ()  # nothing invented


def test_stale_evidence_downgrades_domain():
    reports = all_reports()
    reports[MACRO] = report(MACRO, age_hours=100)  # beyond freshness budget
    decision = synthesize(reports, signals(REQUIRED_DOMAINS), now=NOW,
                          strategy_version_id="dh-v1", holds_btc=False)
    assert MACRO in decision.degraded_domains
    assert decision.action is DarkHorseAction.HOLD


def test_empty_items_treated_as_missing_not_ok():
    empty = DomainReport(MACRO, DomainStatus.OK, ())
    assessed = assess_domain(empty, NOW)
    assert assessed.status is DomainStatus.MISSING


def test_error_domain_preserved():
    errored = DomainReport(LIQUIDITY, DomainStatus.ERROR, (), "broker timeout")
    assert assess_domain(errored, NOW).status is DomainStatus.ERROR


def test_degraded_still_allows_defensive_reduce():
    """Caution stays available even on incomplete evidence."""
    reports = all_reports()
    del reports[ONCHAIN]
    decision = synthesize(reports, signals(set()), now=NOW,
                          strategy_version_id="dh-v1", holds_btc=True)
    assert decision.action is DarkHorseAction.REDUCE
    assert decision.is_degraded


def test_evidence_freshness_and_confidence_are_reported():
    r = report(MACRO, n=3)
    assert r.mean_confidence() == Decimal("0.8")
    assert r.items[0].freshness_seconds(NOW) == 3600.0
    assert not r.items[0].is_stale(NOW, 7200)
    assert r.items[0].is_stale(NOW, 600)
    assert DomainReport(MACRO, DomainStatus.MISSING).mean_confidence() == Decimal("0")


# ---- cadence ----------------------------------------------------------------

def test_long_horizon_cadence():
    assert due_for_evaluation(None, NOW) is True
    assert due_for_evaluation(NOW - dt.timedelta(hours=1), NOW) is False
    assert due_for_evaluation(NOW - dt.timedelta(hours=5), NOW) is True


# ---- exemptions -------------------------------------------------------------

def test_dark_horse_exempt_from_elimination_helper():
    assert is_exempt_from_elimination("dark_horse") is True
    assert is_exempt_from_elimination("active") is False


def _eval(wallet_id, kind, profit, fills):
    return WalletEvaluation(
        wallet_id=wallet_id, strategy_version_id=f"{wallet_id}-v1",
        code_hash=f"{wallet_id}-hash", structural_fingerprint=f"{wallet_id}-fp",
        kind=kind, evaluation_start_equity=Decimal("10000.00"),
        pre_liquidation_equity=Decimal("10000.00") + profit,
        liquidation_adjusted_equity=Decimal("10000.00") + profit,
        fill_count=fills, completed_round_trip_count=0,
    )


def test_dark_horse_never_eliminated_despite_loss_and_no_trades():
    """A losing, zero-fill Dark Horse survives; a losing active does not."""
    evaluations = [_eval(f"a{i}", "active", Decimal("100"), 5) for i in range(12)]
    evaluations.append(_eval("dh", "dark_horse", Decimal("-5000"), 0))
    plan = plan_replacements(evaluations)
    eliminated = {e.wallet_id for e in plan.eliminations}
    assert "dh" not in eliminated
    assert plan.replacement_count == 6  # bottom-six rule among actives only


# ---- permanent wallet continuity -------------------------------------------

def test_upgrade_preserves_wallet_and_records_history():
    wallet = Wallet("dh-1", quote_cash=Decimal("14237.55"),
                    base_qty=Decimal("0.35"), avg_cost=Decimal("58000"),
                    realized_pnl=Decimal("4237.55"))
    dh = DarkHorseWallet(wallet=wallet, active_version_id="dh-v1")
    dh.upgrade("dh-v2", NOW)

    assert dh.active_version_id == "dh-v2"
    assert dh.version_history == [("dh-v1", NOW)]
    # Wallet identity and every balance survive the strategy-code change.
    assert dh.wallet is wallet
    assert dh.wallet.quote_cash == Decimal("14237.55")
    assert dh.wallet.base_qty == Decimal("0.35")
    assert dh.wallet.realized_pnl == Decimal("4237.55")


def test_rollback_restores_last_valid_version_without_resetting_wallet():
    wallet = Wallet("dh-1", quote_cash=Decimal("9000.00"),
                    base_qty=Decimal("0.2"), realized_pnl=Decimal("-1000.00"))
    dh = DarkHorseWallet(wallet=wallet, active_version_id="dh-v1")
    dh.upgrade("dh-v2", NOW)
    restored = dh.rollback(NOW + dt.timedelta(minutes=5))

    assert restored == "dh-v1"
    assert dh.active_version_id == "dh-v1"
    # Defective version rolled back; wallet balances untouched (no reset).
    assert dh.wallet.quote_cash == Decimal("9000.00")
    assert dh.wallet.base_qty == Decimal("0.2")
    assert dh.wallet.realized_pnl == Decimal("-1000.00")


def test_upgrade_rejects_reactivating_current_version():
    dh = DarkHorseWallet(wallet=Wallet("dh-1"), active_version_id="dh-v1")
    with pytest.raises(ValueError, match="already active"):
        dh.upgrade("dh-v1", NOW)


def test_balance_never_reset_across_many_upgrades():
    wallet = Wallet("dh-1", quote_cash=Decimal("25000.00"))
    dh = DarkHorseWallet(wallet=wallet, active_version_id="dh-v1")
    for v in range(2, 8):
        dh.upgrade(f"dh-v{v}", NOW + dt.timedelta(days=v))
    assert dh.wallet.quote_cash == Decimal("25000.00")  # never 10,000 again
    assert len(dh.version_history) == 6
