"""Darkhorse - Daily domain types: bounded, evidence-driven daily adaptation.

Same principles as Dark Horse (spot-only, permanent wallet, no forced
trading), but the strategy re-tunes itself once per completed UTC day. The
learning inputs are the daily lessons: the wallet's own lesson plus every
lesson the other wallets committed for that day.

The schema enforces the learning contract rather than trusting the model:

* Every parameter adjustment must cite the lesson(s) it learned from.
* A hypothesis may never move a parameter — "uncertainty never tweaks",
  mirroring Dark Horse's "uncertainty never buys".
* Every move is clamped to per-parameter hard bounds AND a maximum daily
  step, so a single day's lesson can only nudge the strategy, never rewrite
  it.
* A degraded adaptation (model unavailable, no lessons) changes nothing and
  says so explicitly — parameters are never invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from .lessons import Claim

ADAPTATION_SCHEMA_VERSION = "darkhorse-daily-adaptation-v1"
DARK_HORSE_DAILY_KIND = "dark_horse_daily"


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """Hard bounds and per-day step budget for one tunable parameter."""

    name: str
    lo: Decimal
    hi: Decimal
    max_daily_step: Decimal
    default: Decimal


def _spec(name: str, lo: str, hi: str, step: str, default: str) -> ParamSpec:
    return ParamSpec(name, Decimal(lo), Decimal(hi), Decimal(step),
                     Decimal(default))


# The complete tunable surface. Anything outside this table is not a strategy
# parameter and cannot be touched by an adaptation.
TUNABLES: dict[str, ParamSpec] = {s.name: s for s in (
    # Confidence needed before the committee read adds exposure.
    _spec("accumulate_confidence", "0.50", "0.90", "0.05", "0.60"),
    # Confidence needed before a bearish read trims exposure.
    _spec("reduce_confidence", "0.50", "0.90", "0.05", "0.60"),
    # Fraction of free cash deployed per accumulate decision.
    _spec("accumulate_fraction", "0.05", "0.50", "0.10", "0.25"),
    # Fraction of the position sold per reduce decision.
    _spec("reduce_fraction", "0.10", "1.00", "0.15", "0.50"),
    # Short-horizon evaluation cadence (hours between signal evaluations).
    _spec("signal_cadence_hours", "0.5", "4", "0.5", "1"),
    # Resting-limit offset (basis points) for accumulate bids below the mark.
    _spec("entry_limit_bps", "0", "60", "10", "15"),
    # Resting-limit offset (basis points) for reduce asks above the mark.
    _spec("exit_limit_bps", "0", "60", "10", "15"),
)}


def default_params() -> dict[str, Decimal]:
    return {name: spec.default for name, spec in TUNABLES.items()}


def clamp_adjustment(name: str, current: Decimal, proposed: Decimal) -> Decimal:
    """Engine-side guardrail: clamp a proposed value to the daily step budget
    and the hard bounds. Raises on an unknown parameter — silently accepting
    an unknown knob would let the model grow the tunable surface."""

    spec = TUNABLES.get(name)
    if spec is None:
        raise ValueError(f"unknown tunable parameter: {name}")
    lo_step = current - spec.max_daily_step
    hi_step = current + spec.max_daily_step
    stepped = min(max(proposed, lo_step), hi_step)
    return min(max(stepped, spec.lo), spec.hi)


class ParamAdjustment(BaseModel):
    """One parameter move, bound to the lessons that motivated it."""

    parameter: str
    previous_value: Decimal
    new_value: Decimal
    rationale: Claim
    source_lesson_ids: list[str] = Field(min_length=1)

    @field_validator("parameter")
    @classmethod
    def _known_parameter(cls, v: str) -> str:
        if v not in TUNABLES:
            raise ValueError(f"unknown tunable parameter: {v}")
        return v

    @model_validator(mode="after")
    def _evidence_bound_and_within_budget(self) -> ParamAdjustment:
        if self.rationale.is_hypothesis:
            raise ValueError("a hypothesis may never move a parameter")
        spec = TUNABLES[self.parameter]
        if not (spec.lo <= self.new_value <= spec.hi):
            raise ValueError(
                f"{self.parameter}={self.new_value} outside [{spec.lo}, {spec.hi}]")
        if abs(self.new_value - self.previous_value) > spec.max_daily_step:
            raise ValueError(
                f"{self.parameter} move exceeds daily step budget "
                f"{spec.max_daily_step}")
        if self.new_value == self.previous_value:
            raise ValueError("adjustment must change the value")
        return self


class DailyAdaptation(BaseModel):
    """One completed-UTC-day adaptation record for Darkhorse - Daily."""

    schema_version: str = ADAPTATION_SCHEMA_VERSION
    date: str  # YYYY-MM-DD (the completed UTC day that was learned from)
    wallet_id: str
    previous_version_id: str
    new_version_id: str

    # The FULL resulting parameter set — always complete, never partial.
    parameters: dict[str, Decimal]
    adjustments: list[ParamAdjustment] = Field(default_factory=list)

    own_lesson_id: str = ""
    peer_lesson_ids: list[str] = Field(default_factory=list)
    lessons_considered: int = 0

    model_run_id: str = ""
    degraded: bool = False
    degraded_reason: str = ""

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: str) -> str:
        if v != ADAPTATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported adaptation schema version: {v}")
        return v

    @model_validator(mode="after")
    def _parameters_complete_and_in_bounds(self) -> DailyAdaptation:
        if set(self.parameters) != set(TUNABLES):
            raise ValueError("parameters must cover exactly the tunable surface")
        for name, value in self.parameters.items():
            spec = TUNABLES[name]
            if not (spec.lo <= value <= spec.hi):
                raise ValueError(f"{name}={value} outside [{spec.lo}, {spec.hi}]")
        return self

    @model_validator(mode="after")
    def _degraded_changes_nothing(self) -> DailyAdaptation:
        if self.degraded:
            if self.adjustments:
                raise ValueError("a degraded adaptation may not adjust parameters")
            if not self.degraded_reason:
                raise ValueError("a degraded adaptation must record its reason")
        return self

    @model_validator(mode="after")
    def _adjustments_consistent_with_result(self) -> DailyAdaptation:
        for adj in self.adjustments:
            if self.parameters.get(adj.parameter) != adj.new_value:
                raise ValueError(
                    f"parameters[{adj.parameter}] disagrees with its adjustment")
        return self

    @model_validator(mode="after")
    def _version_moves_iff_something_changed(self) -> DailyAdaptation:
        if self.adjustments and self.new_version_id == self.previous_version_id:
            raise ValueError("an adaptation with adjustments needs a new version id")
        if not self.adjustments and self.new_version_id != self.previous_version_id:
            raise ValueError("a no-change day may not mint a new version")
        return self

    @model_validator(mode="after")
    def _citations_must_come_from_considered_lessons(self) -> DailyAdaptation:
        known = set(self.peer_lesson_ids)
        if self.own_lesson_id:
            known.add(self.own_lesson_id)
        for adj in self.adjustments:
            unknown = set(adj.source_lesson_ids) - known
            if unknown:
                raise ValueError(
                    f"adjustment cites lessons that were not considered: "
                    f"{sorted(unknown)}")
        return self

    @property
    def changed(self) -> bool:
        return bool(self.adjustments)
