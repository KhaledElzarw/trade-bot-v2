"""Dark Horse domain types: five-domain evidence and the long-horizon decision.

Every decision must record the state of all five required analysis domains.
Missing or stale domains are explicit — never silently defaulted, and never
fabricated (closes A18/A19/A20 for this path).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

MACRO = "macro"
TECHNICAL = "technical"
FUNDAMENTAL = "bitcoin_fundamental"
ONCHAIN = "onchain"
LIQUIDITY = "liquidity_derivatives"

REQUIRED_DOMAINS: tuple[str, ...] = (MACRO, TECHNICAL, FUNDAMENTAL, ONCHAIN,
                                     LIQUIDITY)


class DomainStatus(str, Enum):
    OK = "ok"
    STALE = "stale"
    MISSING = "missing"
    ERROR = "error"


class DarkHorseAction(str, Enum):
    """Spot-only: no shorting, no borrowing. Holding cash is always valid."""

    ACCUMULATE = "accumulate"
    HOLD = "hold"
    REDUCE = "reduce"
    EXIT_TO_CASH = "exit_to_cash"


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    source_id: str
    metric: str
    value: str  # stringified; never a binary float for monetary values
    interpretation: str
    confidence: Decimal
    source_time: dt.datetime
    retrieved_at: dt.datetime
    data_snapshot_id: str

    def freshness_seconds(self, now: dt.datetime) -> float:
        return (now - self.source_time).total_seconds()

    def is_stale(self, now: dt.datetime, max_age_seconds: float) -> bool:
        return self.freshness_seconds(now) > max_age_seconds


@dataclass(frozen=True, slots=True)
class DomainReport:
    domain: str
    status: DomainStatus
    items: tuple[EvidenceItem, ...] = ()
    note: str = ""

    @property
    def usable(self) -> bool:
        return self.status is DomainStatus.OK and bool(self.items)

    def mean_confidence(self) -> Decimal:
        if not self.items:
            return Decimal("0")
        total = sum((i.confidence for i in self.items), start=Decimal("0"))
        return total / Decimal(len(self.items))


@dataclass(frozen=True, slots=True)
class DarkHorseDecision:
    action: DarkHorseAction
    rationale: str
    reports: tuple[DomainReport, ...]
    degraded_domains: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    strategy_version_id: str

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded_domains)
