"""Darkhorse - Daily tests: 24h learn-from-lessons adaptation with guardrails."""

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tradebot.application.dark_horse import DarkHorseWallet, is_exempt_from_elimination
from tradebot.application.dark_horse_daily import (
    DailyAdaptationService,
    RawProposal,
    adaptation_idempotency_key,
    apply_adaptation,
    build_adaptation,
    degraded_adaptation,
    due_for_adaptation,
    lesson_ref,
    version_id_for_day,
)
from tradebot.application.evolution import plan_replacements
from tradebot.application.portfolio import (
    DARK_HORSE_DAILY_DISPLAY_NAME,
    display_name,
    seed_portfolio,
)
from tradebot.domain.dark_horse_daily import (
    TUNABLES,
    Claim,
    DailyAdaptation,
    ParamAdjustment,
    clamp_adjustment,
    default_params,
)
from tradebot.domain.evaluations import WalletEvaluation
from tradebot.domain.ledger import Wallet
from tradebot.domain.lessons import DailyLesson
from tradebot.strategies.builtin import BUILTIN_STRATEGIES

NOW = dt.datetime(2026, 7, 18, 0, 5, 0)
DATE = "2026-07-17"
WALLET_ID = "dhd-1"
VERSION = "dark-horse-daily-v1"


def lesson(wallet_id="w-active-1", date=DATE, profit="-120.50"):
    """A schema-valid daily lesson another wallet committed."""

    return DailyLesson(
        date=date, wallet_id=wallet_id, strategy_version_id=f"{wallet_id}-v1",
        starting_equity=Decimal("10000.00"),
        ending_marked_equity=Decimal("10000.00") + Decimal(profit),
        net_daily_profit=Decimal(profit), fees=Decimal("3.20"),
        slippage_cost=Decimal("1.10"), fill_count=14, round_trips=6,
        market_regime="choppy",
        observation=Claim(statement="Tight grids churned fees in chop.",
                          evidence_ids=["snap-1"]),
        hypothesis=Claim(statement="Wider spacing would cut fee bleed.",
                         is_hypothesis=True),
        confidence=Decimal("0.7"),
    )


def own_lesson():
    return lesson(wallet_id=WALLET_ID, profit="45.00")


def proposal(parameter="accumulate_confidence", value="0.65",
             sources=None, statement="Peers won by demanding more confluence."):
    refs = sources if sources is not None else (f"{DATE}:w-active-1",)
    return RawProposal(parameter=parameter, proposed_value=Decimal(value),
                       statement=statement, source_lesson_ids=tuple(refs))


# ---- guardrails: bounds and daily step budget -------------------------------

def test_clamp_respects_daily_step_budget():
    # accumulate_confidence: step budget 0.05 — a jump to 0.90 is trimmed.
    assert clamp_adjustment("accumulate_confidence", Decimal("0.60"),
                            Decimal("0.90")) == Decimal("0.65")


def test_clamp_respects_hard_bounds():
    # reduce_fraction: hi bound 1.00, step 0.15 from 0.95 would allow 1.10.
    assert clamp_adjustment("reduce_fraction", Decimal("0.95"),
                            Decimal("2.00")) == Decimal("1.00")
    assert clamp_adjustment("accumulate_fraction", Decimal("0.10"),
                            Decimal("0.01")) == Decimal("0.05")


def test_clamp_rejects_unknown_parameter():
    with pytest.raises(ValueError, match="unknown tunable"):
        clamp_adjustment("leverage", Decimal("1"), Decimal("100"))


def test_adjustment_schema_rejects_hypothesis_and_oversized_moves():
    with pytest.raises(ValidationError, match="hypothesis may never move"):
        ParamAdjustment(
            parameter="accumulate_confidence",
            previous_value=Decimal("0.60"), new_value=Decimal("0.65"),
            rationale=Claim(statement="A hunch.", is_hypothesis=True),
            source_lesson_ids=["x"])
    with pytest.raises(ValidationError, match="step budget"):
        ParamAdjustment(
            parameter="accumulate_confidence",
            previous_value=Decimal("0.60"), new_value=Decimal("0.80"),
            rationale=Claim(statement="ok", evidence_ids=["x"]),
            source_lesson_ids=["x"])
    with pytest.raises(ValidationError, match="min_length|at least 1"):
        ParamAdjustment(
            parameter="accumulate_confidence",
            previous_value=Decimal("0.60"), new_value=Decimal("0.65"),
            rationale=Claim(statement="ok", evidence_ids=["x"]),
            source_lesson_ids=[])


# ---- learning from own + peer lessons ---------------------------------------

def test_adaptation_learns_from_own_and_peer_lessons():
    peers = [lesson("w-active-1"), lesson("w-active-2", profit="300.00")]
    adaptation = build_adaptation(
        date=DATE, wallet_id=WALLET_ID, current_version_id=VERSION,
        params=default_params(), own_lesson=own_lesson(), peer_lessons=peers,
        proposals=[proposal()])

    assert adaptation.changed
    assert adaptation.lessons_considered == 3
    assert adaptation.own_lesson_id == f"{DATE}:{WALLET_ID}"
    assert adaptation.peer_lesson_ids == [f"{DATE}:w-active-1",
                                          f"{DATE}:w-active-2"]
    assert adaptation.parameters["accumulate_confidence"] == Decimal("0.65")
    assert adaptation.new_version_id == version_id_for_day(DATE)
    adj = adaptation.adjustments[0]
    assert adj.source_lesson_ids == [f"{DATE}:w-active-1"]
    assert not adj.rationale.is_hypothesis


def test_uncited_proposal_is_dropped_never_applied():
    """A tweak that cites no considered lesson is not learning — dropped."""

    adaptation = build_adaptation(
        date=DATE, wallet_id=WALLET_ID, current_version_id=VERSION,
        params=default_params(), own_lesson=own_lesson(), peer_lessons=[],
        proposals=[proposal(sources=("made-up:lesson",))])
    assert not adaptation.changed
    assert adaptation.parameters == default_params()
    assert adaptation.new_version_id == VERSION  # no version churn


def test_unknown_parameter_proposal_is_dropped():
    adaptation = build_adaptation(
        date=DATE, wallet_id=WALLET_ID, current_version_id=VERSION,
        params=default_params(), own_lesson=own_lesson(), peer_lessons=[],
        proposals=[RawProposal("leverage", Decimal("50"), "moon",
                               (f"{DATE}:{WALLET_ID}",))])
    assert not adaptation.changed


def test_oversized_proposal_is_clamped_not_rejected():
    adaptation = build_adaptation(
        date=DATE, wallet_id=WALLET_ID, current_version_id=VERSION,
        params=default_params(), own_lesson=own_lesson(), peer_lessons=[],
        proposals=[proposal(value="0.90", sources=(f"{DATE}:{WALLET_ID}",))])
    assert adaptation.parameters["accumulate_confidence"] == Decimal("0.65")


def test_first_proposal_per_parameter_wins():
    src = (f"{DATE}:{WALLET_ID}",)
    adaptation = build_adaptation(
        date=DATE, wallet_id=WALLET_ID, current_version_id=VERSION,
        params=default_params(), own_lesson=own_lesson(), peer_lessons=[],
        proposals=[proposal(value="0.65", sources=src),
                   proposal(value="0.55", sources=src)])
    assert adaptation.parameters["accumulate_confidence"] == Decimal("0.65")
    assert len(adaptation.adjustments) == 1


def test_step_budget_measured_from_day_start():
    """Two same-day proposals cannot chain to exceed one day's budget."""

    src = (f"{DATE}:{WALLET_ID}",)
    adaptation = build_adaptation(
        date=DATE, wallet_id=WALLET_ID, current_version_id=VERSION,
        params=default_params(), own_lesson=own_lesson(), peer_lessons=[],
        proposals=[proposal(value="0.65", sources=src),
                   RawProposal("reduce_confidence", Decimal("0.70"),
                               "peers cut losers earlier", src)])
    # Each parameter moved at most its own daily budget.
    assert adaptation.parameters["accumulate_confidence"] == Decimal("0.65")
    assert adaptation.parameters["reduce_confidence"] == Decimal("0.65")


# ---- degraded paths: never fabricate ----------------------------------------

def test_no_lessons_yields_degraded_no_change():
    service = DailyAdaptationService()
    adaptation, cached = service.adapt(
        date=DATE, wallet_id=WALLET_ID, current_version_id=VERSION,
        params=default_params(), own_lesson=None, peer_lessons=[],
        proposer=lambda own, peers, params: [proposal()])
    assert not cached
    assert adaptation.degraded
    assert "no daily lessons" in adaptation.degraded_reason
    assert adaptation.parameters == default_params()
    assert adaptation.new_version_id == VERSION


def test_model_failure_yields_degraded_no_change():
    def broken(own, peers, params):
        raise TimeoutError("model unavailable")

    service = DailyAdaptationService()
    adaptation, _ = service.adapt(
        date=DATE, wallet_id=WALLET_ID, current_version_id=VERSION,
        params=default_params(), own_lesson=own_lesson(),
        peer_lessons=[lesson()], proposer=broken)
    assert adaptation.degraded
    assert "TimeoutError" in adaptation.degraded_reason
    assert adaptation.parameters == default_params()


def test_degraded_adaptation_schema_forbids_adjustments():
    with pytest.raises(ValidationError, match="degraded"):
        DailyAdaptation(
            date=DATE, wallet_id=WALLET_ID, previous_version_id=VERSION,
            new_version_id="v2", parameters=default_params(),
            adjustments=[ParamAdjustment(
                parameter="accumulate_confidence",
                previous_value=Decimal("0.60"), new_value=Decimal("0.65"),
                rationale=Claim(statement="ok", evidence_ids=["x"]),
                source_lesson_ids=["x"])],
            own_lesson_id="x", degraded=True, degraded_reason="down")


def test_schema_rejects_citations_outside_considered_lessons():
    with pytest.raises(ValidationError, match="not considered"):
        DailyAdaptation(
            date=DATE, wallet_id=WALLET_ID, previous_version_id=VERSION,
            new_version_id="v2", parameters={
                **default_params(),
                "accumulate_confidence": Decimal("0.65"),
            },
            adjustments=[ParamAdjustment(
                parameter="accumulate_confidence",
                previous_value=Decimal("0.60"), new_value=Decimal("0.65"),
                rationale=Claim(statement="ok", evidence_ids=["ghost"]),
                source_lesson_ids=["ghost"])],
            peer_lesson_ids=[f"{DATE}:w-active-1"])


def test_no_change_day_may_not_mint_a_version():
    with pytest.raises(ValidationError, match="no-change day"):
        DailyAdaptation(
            date=DATE, wallet_id=WALLET_ID, previous_version_id=VERSION,
            new_version_id="v2", parameters=default_params())


# ---- 24h cadence and idempotency --------------------------------------------

def test_daily_cadence():
    assert due_for_adaptation(None, NOW) is True
    assert due_for_adaptation(NOW - dt.timedelta(hours=23), NOW) is False
    assert due_for_adaptation(NOW - dt.timedelta(hours=24), NOW) is True


def test_adaptation_is_idempotent_per_day():
    service = DailyAdaptationService()
    kwargs = dict(
        date=DATE, wallet_id=WALLET_ID, current_version_id=VERSION,
        params=default_params(), own_lesson=own_lesson(),
        peer_lessons=[lesson()],
        proposer=lambda own, peers, params: [proposal()])
    first, cached1 = service.adapt(**kwargs)
    second, cached2 = service.adapt(**kwargs)
    assert (cached1, cached2) == (False, True)
    assert second is first
    assert adaptation_idempotency_key(DATE) == f"darkhorse_daily_adaptation:{DATE}"


# ---- permanent wallet continuity --------------------------------------------

def _adapted():
    return build_adaptation(
        date=DATE, wallet_id=WALLET_ID, current_version_id=VERSION,
        params=default_params(), own_lesson=own_lesson(), peer_lessons=[],
        proposals=[proposal(sources=(f"{DATE}:{WALLET_ID}",))])


def test_apply_adaptation_upgrades_version_without_touching_wallet():
    wallet = Wallet(WALLET_ID, quote_cash=Decimal("12345.67"),
                    base_qty=Decimal("0.15"), realized_pnl=Decimal("2345.67"))
    dh = DarkHorseWallet(wallet=wallet, active_version_id=VERSION)
    assert apply_adaptation(dh, _adapted(), NOW) is True
    assert dh.active_version_id == version_id_for_day(DATE)
    assert dh.wallet is wallet
    assert dh.wallet.quote_cash == Decimal("12345.67")
    assert dh.wallet.base_qty == Decimal("0.15")


def test_degraded_or_no_change_adaptation_activates_nothing():
    dh = DarkHorseWallet(wallet=Wallet(WALLET_ID), active_version_id=VERSION)
    degraded = degraded_adaptation(
        date=DATE, wallet_id=WALLET_ID, current_version_id=VERSION,
        params=default_params(), reason="model down")
    assert apply_adaptation(dh, degraded, NOW) is False
    assert dh.active_version_id == VERSION


def test_defective_daily_version_rolls_back_without_reset():
    wallet = Wallet(WALLET_ID, quote_cash=Decimal("9000.00"))
    dh = DarkHorseWallet(wallet=wallet, active_version_id=VERSION)
    apply_adaptation(dh, _adapted(), NOW)
    assert dh.rollback(NOW + dt.timedelta(minutes=10)) == VERSION
    assert dh.wallet.quote_cash == Decimal("9000.00")  # never reset


# ---- elimination exemption --------------------------------------------------

def test_darkhorse_daily_exempt_from_elimination():
    assert is_exempt_from_elimination("dark_horse_daily") is True

    def _eval(wallet_id, kind, profit, fills):
        return WalletEvaluation(
            wallet_id=wallet_id, strategy_version_id=f"{wallet_id}-v1",
            code_hash=f"{wallet_id}-hash",
            structural_fingerprint=f"{wallet_id}-fp",
            kind=kind, evaluation_start_equity=Decimal("10000.00"),
            pre_liquidation_equity=Decimal("10000.00") + profit,
            liquidation_adjusted_equity=Decimal("10000.00") + profit,
            fill_count=fills, completed_round_trip_count=0,
        )

    evaluations = [_eval(f"a{i}", "active", Decimal("100"), 5) for i in range(12)]
    evaluations.append(_eval("dhd", "dark_horse_daily", Decimal("-4000"), 0))
    plan = plan_replacements(evaluations)
    assert "dhd" not in {e.wallet_id for e in plan.eliminations}


# ---- portfolio composition --------------------------------------------------

def test_seeded_portfolio_includes_darkhorse_daily():
    names = [cls().metadata().name for cls in BUILTIN_STRATEGIES]
    p = seed_portfolio(names, now=NOW, id_factory=lambda h: f"w-{h}")
    slot = p.dark_horse_daily
    assert slot is not None
    assert slot.kind == "dark_horse_daily"
    assert slot.wallet.quote_cash == Decimal("10000.00")
    assert display_name(slot, NOW + dt.timedelta(days=40)) == \
        DARK_HORSE_DAILY_DISPLAY_NAME
    # Counted in the active baseline alongside Dark Horse.
    assert p.active_equity(Decimal("60000")) == Decimal("140000.00")


def test_lesson_ref_format():
    assert lesson_ref(lesson("w-9")) == f"{DATE}:w-9"


def test_tunable_surface_is_complete_and_bounded():
    assert set(default_params()) == set(TUNABLES)
    for spec in TUNABLES.values():
        assert spec.lo < spec.hi
        assert spec.max_daily_step > 0
        assert spec.lo <= spec.default <= spec.hi
