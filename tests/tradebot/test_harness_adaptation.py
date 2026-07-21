"""Daily LLM re-tune loop: analyst, proposer, adaptation, and degrade paths.

Uses a fake LLM client so the loop is exercised deterministically without the
local model.
"""

import types
from decimal import Decimal

import tradebot.api.devserver as ds
from tradebot.api.harness_adaptation import (
    DailyReTuner,
    LessonAnalysis,
    LlmAnalyst,
    LlmProposer,
    Proposal,
    ProposalList,
    build_daily_facts,
)
from tradebot.application.dark_horse_daily import lesson_ref
from tradebot.application.lessons import DailyFacts
from tradebot.domain.dark_horse_daily import TUNABLES, default_params
from tradebot.domain.ledger import Wallet


class FakeClient:
    """Returns pre-canned schema instances by requested schema type."""

    def __init__(self, analysis=None, proposals=None):
        self.analysis = analysis
        self.proposals = proposals

    def generate_structured(self, schema, messages):
        run = types.SimpleNamespace(prompt_hash="deadbeefcafe")
        if schema is LessonAnalysis:
            return self.analysis, run
        if schema is ProposalList:
            return self.proposals, run
        return None, run


ANALYSIS = LessonAnalysis(market_regime="range", observation="chop",
                          hypothesis="mean reversion holds", confidence=Decimal("0.6"))


def facts(wallet_id="w-dark-horse-daily", profit="50") -> DailyFacts:
    return DailyFacts(
        date="2026-07-18", wallet_id=wallet_id, strategy_version_id="v1",
        starting_equity=Decimal("10000"), ending_marked_equity=Decimal("10000") + Decimal(profit),
        net_daily_profit=Decimal(profit), fees=Decimal("2"), slippage_cost=Decimal("0"),
        fill_count=3, round_trips=1, trade_ids=("o1", "o2"))


def test_analyst_assembles_a_valid_lesson_from_facts_and_analysis():
    lesson = LlmAnalyst(FakeClient(analysis=ANALYSIS))(facts())
    assert lesson is not None
    # Engine figures preserved; analysis fields taken from the model.
    assert lesson.net_daily_profit == Decimal("50")
    assert lesson.market_regime == "range"
    assert lesson.observation.evidence_ids == ["o1", "o2"]
    assert lesson.hypothesis.is_hypothesis is True


def test_analyst_degrades_to_none_when_model_returns_nothing():
    assert LlmAnalyst(FakeClient(analysis=None))(facts()) is None


def test_proposer_maps_model_json_to_raw_proposals():
    own = LlmAnalyst(FakeClient(analysis=ANALYSIS))(facts())
    ref = lesson_ref(own)
    proposals = ProposalList(proposals=[Proposal(
        parameter="entry_limit_bps", proposed_value=Decimal("25"),
        statement="widen the resting bid", source_lesson_ids=[ref])])
    raw = LlmProposer(FakeClient(proposals=proposals))(own, [], default_params())
    assert len(raw) == 1 and raw[0].parameter == "entry_limit_bps"
    assert raw[0].source_lesson_ids == (ref,)


def _wallets():
    return {"w-dark-horse-daily": Wallet("w-dark-horse-daily"),
            "w-peer": Wallet("w-peer")}


def _versions():
    return {"w-dark-horse-daily": "dark-horse-daily-v1", "w-peer": "builtin-x-v1"}


def test_retuner_applies_a_cited_in_bounds_adjustment():
    own = LlmAnalyst(FakeClient(analysis=ANALYSIS))(facts())
    ref = lesson_ref(own)
    proposals = ProposalList(proposals=[Proposal(
        parameter="entry_limit_bps", proposed_value=Decimal("25"),
        statement="widen", source_lesson_ids=[ref])])
    retuner = DailyReTuner(
        daily_wallet_id="w-dark-horse-daily", daily_version_id="dark-horse-daily-v1",
        params=default_params(), analyst=LlmAnalyst(FakeClient(analysis=ANALYSIS)),
        proposer=LlmProposer(FakeClient(proposals=proposals)))
    retuner.begin_day(_wallets(), Decimal("60000"))
    new = retuner.end_day("2026-07-18", _wallets(), Decimal("60000"),
                          _versions(), {"w-dark-horse-daily": [{"order_id": "o1",
                          "side": "BUY", "fee": "1"}]})
    # 15 -> 25 is exactly one daily step; within [0,60].
    assert new["entry_limit_bps"] == Decimal("25")
    assert set(new) == set(TUNABLES)  # full guardrailed param set preserved


def test_retuner_uncited_or_out_of_bounds_proposal_is_dropped():
    # Cites a non-existent lesson id -> guardrail drops it -> no change.
    proposals = ProposalList(proposals=[Proposal(
        parameter="entry_limit_bps", proposed_value=Decimal("25"),
        statement="uncited", source_lesson_ids=["1999-01-01:ghost"])])
    retuner = DailyReTuner(
        daily_wallet_id="w-dark-horse-daily", daily_version_id="dark-horse-daily-v1",
        params=default_params(), analyst=LlmAnalyst(FakeClient(analysis=ANALYSIS)),
        proposer=LlmProposer(FakeClient(proposals=proposals)))
    retuner.begin_day(_wallets(), Decimal("60000"))
    new = retuner.end_day("2026-07-18", _wallets(), Decimal("60000"),
                          _versions(), {})
    assert new["entry_limit_bps"] == default_params()["entry_limit_bps"]


def test_retuner_no_proposals_is_a_no_op():
    retuner = DailyReTuner(
        daily_wallet_id="w-dark-horse-daily", daily_version_id="dark-horse-daily-v1",
        params=default_params(), analyst=LlmAnalyst(FakeClient(analysis=None)),
        proposer=LlmProposer(FakeClient(proposals=None)))
    retuner.begin_day(_wallets(), Decimal("60000"))
    new = retuner.end_day("2026-07-18", _wallets(), Decimal("60000"), _versions(), {})
    assert new == default_params()


def test_build_daily_facts_from_recorded_fills():
    f = build_daily_facts("2026-07-18", "w1", "v1", Decimal("10000"),
                          Decimal("10030"),
                          [{"order_id": "o1", "side": "BUY", "fee": "1.50"},
                           {"order_id": "o2", "side": "SELL", "fee": "1.00"}])
    assert f.net_daily_profit == Decimal("30.00")
    assert f.fees == Decimal("2.50") and f.fill_count == 2 and f.round_trips == 1
    assert f.trade_ids == ("o1", "o2")


def test_build_view_llm_adapt_degrades_to_no_op_when_model_down(monkeypatch):
    """With the model unreachable, --llm-adapt must produce the SAME result as off."""

    monkeypatch.setattr(ds, "_build_llm_client", lambda: (None, "unavailable"))
    import datetime as dt
    now = dt.datetime(2026, 7, 20, 12, 0, 0)
    off = ds.build_view(now, live=False, llm_adapt=False)
    on = ds.build_view(now, live=False, llm_adapt=True)
    daily_off = off.portfolio.dark_horse_daily.wallet
    daily_on = on.portfolio.dark_horse_daily.wallet
    assert daily_on.base_qty == daily_off.base_qty
    assert daily_on.quote_cash == daily_off.quote_cash
