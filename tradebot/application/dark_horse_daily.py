"""Darkhorse - Daily service: learn from the day's lessons, adapt the strategy.

Every 24 hours (once per completed UTC day) the wallet:

1. reads its OWN daily lesson (what its trades earned and why),
2. reads every lesson the OTHER wallets committed for that day,
3. asks the model to propose parameter tweaks, each citing the lessons it
   learned from,
4. applies the tweaks through engine-side guardrails (unknown parameters
   dropped, uncited proposals dropped, every move clamped to bounds and the
   daily step budget), and
5. activates the result as a new daily strategy version — the wallet itself
   is permanent and is NEVER reset, exactly like Dark Horse.

Model failure never blocks the wallet: it yields an explicit degraded
adaptation that changes nothing, and trading continues on yesterday's
parameters. Re-running a completed day is a cached no-op (idempotent).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Sequence

from ..domain.dark_horse_daily import (
    TUNABLES,
    Claim,
    DailyAdaptation,
    ParamAdjustment,
    clamp_adjustment,
)
from ..domain.lessons import DailyLesson
from .dark_horse import DarkHorseWallet
from .lessons import JobStore

ADAPTATION_JOB = "darkhorse_daily_adaptation"
ADAPTATION_CADENCE_SECONDS = 24 * 60 * 60  # once per completed UTC day


def adaptation_idempotency_key(date: str) -> str:
    return f"{ADAPTATION_JOB}:{date}"


def due_for_adaptation(last_adapted: dt.datetime | None, now: dt.datetime,
                       cadence_seconds: float = ADAPTATION_CADENCE_SECONDS
                       ) -> bool:
    """Daily cadence gate — one adaptation per 24h window."""

    if last_adapted is None:
        return True
    return (now - last_adapted).total_seconds() >= cadence_seconds


def lesson_ref(lesson: DailyLesson) -> str:
    """Stable citation id for a stored daily lesson."""

    return f"{lesson.date}:{lesson.wallet_id}"


def version_id_for_day(date: str) -> str:
    return f"dark-horse-daily-{date}"


@dataclass(frozen=True, slots=True)
class RawProposal:
    """A model-proposed tweak BEFORE the engine's guardrails are applied."""

    parameter: str
    proposed_value: Decimal
    statement: str
    source_lesson_ids: tuple[str, ...] = ()


Proposer = Callable[
    [DailyLesson | None, Sequence[DailyLesson], dict[str, Decimal]],
    Sequence[RawProposal],
]


def build_adaptation(
    *,
    date: str,
    wallet_id: str,
    current_version_id: str,
    params: dict[str, Decimal],
    own_lesson: DailyLesson | None,
    peer_lessons: Sequence[DailyLesson],
    proposals: Sequence[RawProposal],
    model_run_id: str = "",
) -> DailyAdaptation:
    """Apply engine guardrails to the model's proposals and record the result.

    Guardrails (never delegated to the model):
    * unknown parameters are dropped;
    * proposals citing no considered lesson are dropped — learning must be
      traceable to actual lessons, never invented;
    * only the first proposal per parameter counts, and the step budget is
      measured from the day-start value;
    * values are clamped to the daily step budget and hard bounds; a proposal
      that clamps back to the current value is a no-op and is dropped.
    """

    own_ref = lesson_ref(own_lesson) if own_lesson is not None else ""
    peer_refs = [lesson_ref(lesson) for lesson in peer_lessons]
    known_refs = set(peer_refs) | ({own_ref} if own_ref else set())

    adjustments: list[ParamAdjustment] = []
    new_params = dict(params)
    touched: set[str] = set()
    for proposal in proposals:
        if proposal.parameter not in TUNABLES:
            continue
        if proposal.parameter in touched:
            continue
        cited = [r for r in proposal.source_lesson_ids if r in known_refs]
        if not cited:
            continue
        current = params[proposal.parameter]
        clamped = clamp_adjustment(proposal.parameter, current,
                                   proposal.proposed_value)
        if clamped == current:
            continue
        adjustments.append(ParamAdjustment(
            parameter=proposal.parameter,
            previous_value=current,
            new_value=clamped,
            rationale=Claim(statement=proposal.statement, evidence_ids=cited),
            source_lesson_ids=cited,
        ))
        new_params[proposal.parameter] = clamped
        touched.add(proposal.parameter)

    changed = bool(adjustments)
    return DailyAdaptation(
        date=date,
        wallet_id=wallet_id,
        previous_version_id=current_version_id,
        new_version_id=(version_id_for_day(date) if changed
                        else current_version_id),
        parameters=new_params,
        adjustments=adjustments,
        own_lesson_id=own_ref,
        peer_lesson_ids=peer_refs,
        lessons_considered=len(peer_refs) + (1 if own_ref else 0),
        model_run_id=model_run_id,
    )


def degraded_adaptation(
    *,
    date: str,
    wallet_id: str,
    current_version_id: str,
    params: dict[str, Decimal],
    reason: str,
    own_lesson: DailyLesson | None = None,
    peer_lessons: Sequence[DailyLesson] = (),
) -> DailyAdaptation:
    """Explicit no-change record when learning is impossible. Never invents."""

    own_ref = lesson_ref(own_lesson) if own_lesson is not None else ""
    peer_refs = [lesson_ref(lesson) for lesson in peer_lessons]
    return DailyAdaptation(
        date=date,
        wallet_id=wallet_id,
        previous_version_id=current_version_id,
        new_version_id=current_version_id,
        parameters=dict(params),
        own_lesson_id=own_ref,
        peer_lesson_ids=peer_refs,
        lessons_considered=len(peer_refs) + (1 if own_ref else 0),
        degraded=True,
        degraded_reason=reason,
    )


@dataclass(slots=True)
class DailyAdaptationService:
    """Runs the once-per-day learn-and-adapt job, idempotently."""

    job_store: JobStore = field(default_factory=JobStore)

    def adapt(
        self,
        *,
        date: str,
        wallet_id: str,
        current_version_id: str,
        params: dict[str, Decimal],
        own_lesson: DailyLesson | None,
        peer_lessons: Sequence[DailyLesson],
        proposer: Proposer,
        force: bool = False,
    ) -> tuple[DailyAdaptation, bool]:
        """Return (adaptation, was_cached). Idempotent per completed UTC day."""

        key = adaptation_idempotency_key(date)
        if not force and self.job_store.is_complete(key):
            return self.job_store.get(key), True  # type: ignore[return-value]

        if own_lesson is None and not peer_lessons:
            adaptation = degraded_adaptation(
                date=date, wallet_id=wallet_id,
                current_version_id=current_version_id, params=params,
                reason=f"no daily lessons available for {date}")
        else:
            try:
                proposals = proposer(own_lesson, peer_lessons, dict(params))
            except Exception as exc:
                adaptation = degraded_adaptation(
                    date=date, wallet_id=wallet_id,
                    current_version_id=current_version_id, params=params,
                    reason=f"{type(exc).__name__}: {exc}",
                    own_lesson=own_lesson, peer_lessons=peer_lessons)
            else:
                adaptation = build_adaptation(
                    date=date, wallet_id=wallet_id,
                    current_version_id=current_version_id, params=params,
                    own_lesson=own_lesson, peer_lessons=peer_lessons,
                    proposals=proposals)

        self.job_store.put(key, adaptation)
        return adaptation, False


def apply_adaptation(dh: DarkHorseWallet, adaptation: DailyAdaptation,
                     at: dt.datetime) -> bool:
    """Activate an adaptation on the permanent wallet.

    Reuses the Dark Horse continuity mechanics: only the strategy-version
    pointer moves; balances, lots, and lifetime P&L are untouched. A degraded
    or no-change adaptation activates nothing. Returns whether an upgrade
    happened; a defective version is undone with ``dh.rollback``.
    """

    if adaptation.degraded or not adaptation.changed:
        return False
    dh.upgrade(adaptation.new_version_id, at)
    return True
