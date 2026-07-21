"""Wires the daily LLM re-tune loop into the dev harness (opt-in).

The production adaptation machinery already exists but was never invoked in the
harness: `LessonService.generate_daily` turns engine facts into a `DailyLesson`
(with an LLM analyst), and `DailyAdaptationService.adapt` turns the day's lessons
into a guardrailed parameter change (with an LLM proposer). This module supplies
those two LLM callables and an orchestrator that runs the loop once per simulated
UTC day, re-tuning Darkhorse - Daily's knobs — including the new limit offsets —
before the next day is replayed.

Everything degrades to a no-op when the local model is unavailable or returns
invalid JSON: `generate_structured` never raises, `LessonService` falls back to a
deterministic degraded lesson, and `build_adaptation`'s guardrails drop unknown
or uncited proposals and clamp every move. So with the model down the replay
completes unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Sequence

from pydantic import BaseModel, Field

from ..application.dark_horse_daily import (
    DailyAdaptationService,
    RawProposal,
    lesson_ref,
)
from ..application.lessons import DailyFacts, LessonService
from ..domain.dark_horse_daily import TUNABLES
from ..domain.lessons import Claim, DailyLesson
from ..domain.money import quote

DAILY_WALLET_KIND = "dark_horse_daily"


# ---- model-authored JSON (kept deliberately small and robust) --------------


class LessonAnalysis(BaseModel):
    """The qualitative fields the model fills; figures stay engine-computed."""

    market_regime: str = "unknown"
    observation: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    confidence: Decimal = Field(ge=0, le=1, default=Decimal("0.5"))


class Proposal(BaseModel):
    parameter: str
    proposed_value: Decimal
    statement: str = Field(min_length=1)
    source_lesson_ids: list[str] = Field(default_factory=list)


class ProposalList(BaseModel):
    proposals: list[Proposal] = Field(default_factory=list)


# ---- LLM callables ---------------------------------------------------------


@dataclass(slots=True)
class LlmAnalyst:
    """facts -> DailyLesson, filling only the analytical fields via the model."""

    client: object  # LlamaCppClient-like: generate_structured(schema, messages)

    def __call__(self, facts: DailyFacts) -> DailyLesson | None:
        messages = [
            {"role": "system",
             "content": "You analyse one paper-trading wallet's day. Reply with "
                        "JSON only: market_regime, observation, hypothesis, "
                        "confidence (0..1). Be concise and evidence-based."},
            {"role": "user", "content": json.dumps({
                "date": facts.date,
                "wallet_id": facts.wallet_id,
                "net_daily_profit": str(facts.net_daily_profit),
                "fees": str(facts.fees),
                "fill_count": facts.fill_count,
                "round_trips": facts.round_trips,
            })},
        ]
        analysis, run = self.client.generate_structured(LessonAnalysis, messages)
        if analysis is None:
            return None
        cited = list(facts.trade_ids)
        return DailyLesson(
            date=facts.date, wallet_id=facts.wallet_id,
            strategy_version_id=facts.strategy_version_id,
            starting_equity=facts.starting_equity,
            ending_marked_equity=facts.ending_marked_equity,
            net_daily_profit=facts.net_daily_profit,
            fees=facts.fees, slippage_cost=facts.slippage_cost,
            fill_count=facts.fill_count, round_trips=facts.round_trips,
            market_regime=analysis.market_regime,
            observation=Claim(statement=analysis.observation,
                              evidence_ids=cited, is_hypothesis=not cited),
            hypothesis=Claim(statement=analysis.hypothesis, is_hypothesis=True),
            confidence=analysis.confidence,
            supporting_trade_ids=cited,
            supporting_snapshot_ids=list(facts.snapshot_ids),
            model_run_id=getattr(run, "prompt_hash", "")[:12],
        )


@dataclass(slots=True)
class LlmProposer:
    """(own, peers, params) -> RawProposals citing the lessons they learned from."""

    client: object

    def __call__(self, own_lesson: DailyLesson | None,
                 peer_lessons: Sequence[DailyLesson],
                 params: dict[str, Decimal]) -> list[RawProposal]:
        lessons = ([own_lesson] if own_lesson is not None else []) + list(peer_lessons)
        catalogue = [{
            "lesson_id": lesson_ref(l),
            "wallet_id": l.wallet_id,
            "net_daily_profit": str(l.net_daily_profit),
            "observation": l.observation.statement,
        } for l in lessons]
        tunables = {name: {"lo": str(s.lo), "hi": str(s.hi),
                           "max_daily_step": str(s.max_daily_step),
                           "current": str(params.get(name, s.default))}
                    for name, s in TUNABLES.items()}
        messages = [
            {"role": "system",
             "content": "You tune ONE wallet's strategy parameters from daily "
                        "lessons. Reply JSON only: {\"proposals\": [{parameter, "
                        "proposed_value, statement, source_lesson_ids}]}. Every "
                        "proposal MUST cite lesson_id(s) from the catalogue. Only "
                        "propose listed parameters; stay within bounds."},
            {"role": "user", "content": json.dumps({
                "tunables": tunables, "lessons": catalogue})},
        ]
        result, _run = self.client.generate_structured(ProposalList, messages)
        if result is None:
            return []
        return [RawProposal(
            parameter=p.parameter, proposed_value=p.proposed_value,
            statement=p.statement,
            source_lesson_ids=tuple(p.source_lesson_ids),
        ) for p in result.proposals]


# ---- facts + orchestration -------------------------------------------------


def build_daily_facts(date: str, wallet_id: str, version_id: str,
                      start_equity: Decimal, end_equity: Decimal,
                      day_fills: list[dict]) -> DailyFacts:
    """Engine-computed figures for one wallet's completed day."""

    fees = sum((Decimal(t.get("fee") or "0") for t in day_fills), Decimal("0"))
    trade_ids = tuple(t["order_id"] for t in day_fills)
    round_trips = sum(1 for t in day_fills if t.get("side") == "SELL")
    return DailyFacts(
        date=date, wallet_id=wallet_id, strategy_version_id=version_id,
        starting_equity=quote(start_equity), ending_marked_equity=quote(end_equity),
        net_daily_profit=quote(end_equity - start_equity), fees=quote(fees),
        slippage_cost=Decimal("0"), fill_count=len(day_fills),
        round_trips=round_trips, trade_ids=trade_ids)


@dataclass(slots=True)
class DailyReTuner:
    """Runs lessons + adaptation at each simulated UTC-day boundary.

    ``begin_day`` snapshots per-wallet starting equity; ``end_day`` builds facts,
    generates lessons for every wallet, adapts Darkhorse - Daily from its own +
    peer lessons, and returns the new (guardrailed) params. The caller applies
    those to the daily runner before replaying the next day.
    """

    daily_wallet_id: str
    daily_version_id: str
    params: dict[str, Decimal]
    analyst: Callable[[DailyFacts], DailyLesson | None]
    proposer: Callable
    lesson_service: LessonService = field(default_factory=LessonService)
    adaptation_service: DailyAdaptationService = field(default_factory=DailyAdaptationService)
    _day_start_equity: dict[str, Decimal] = field(default_factory=dict)
    history: list = field(default_factory=list)

    def begin_day(self, wallets_by_id: dict, mark: Decimal) -> None:
        self._day_start_equity = {wid: w.equity(mark)
                                  for wid, w in wallets_by_id.items()}

    def end_day(self, date: str, wallets_by_id: dict, mark: Decimal,
                version_by_id: dict, day_fills_by_wallet: dict) -> dict[str, Decimal]:
        lessons: dict[str, DailyLesson] = {}
        for wid, w in wallets_by_id.items():
            facts = build_daily_facts(
                date, wid, version_by_id.get(wid, "unknown"),
                self._day_start_equity.get(wid, w.equity(mark)), w.equity(mark),
                day_fills_by_wallet.get(wid, []))
            lesson, _ = self.lesson_service.generate_daily(facts, self.analyst)
            lessons[wid] = lesson

        own = lessons.get(self.daily_wallet_id)
        peers = [l for wid, l in lessons.items() if wid != self.daily_wallet_id]
        adaptation, _ = self.adaptation_service.adapt(
            date=date, wallet_id=self.daily_wallet_id,
            current_version_id=self.daily_version_id, params=dict(self.params),
            own_lesson=own, peer_lessons=peers, proposer=self.proposer)
        self.params = {k: quote_or_decimal(v)
                       for k, v in adaptation.parameters.items()}
        self.daily_version_id = adaptation.new_version_id
        self.history.append(adaptation)
        return self.params


def quote_or_decimal(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))
