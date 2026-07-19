"""Dark Horse service: five-domain committee + permanent-wallet continuity.

Key product rules enforced here:

* Every decision records all five required domain states; missing/stale domains
  are explicit and degrade the decision toward caution — never fabricated.
* Spot-only: ACCUMULATE/HOLD/REDUCE/EXIT_TO_CASH. No shorting, ever.
* Forced trading is not required — HOLD (and holding cash) is always valid, and
  Dark Horse is exempt from the weekly no-trade elimination.
* Strategy code may evolve through versioned upgrades, but the wallet, lots,
  equity history, and lifetime P&L remain continuous — never reset.
* A technically defective new version rolls back to the last valid version
  WITHOUT touching the wallet.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from ..domain.dark_horse import (
    REQUIRED_DOMAINS,
    DarkHorseAction,
    DarkHorseDecision,
    DomainReport,
    DomainStatus,
)
from ..domain.ledger import Wallet

# Long-horizon cadence: evaluate once per completed 4h bar by default.
DEFAULT_CADENCE_SECONDS = 4 * 60 * 60
DEFAULT_MAX_EVIDENCE_AGE_SECONDS = 36 * 60 * 60  # a day and a half

# Confidence needed across usable domains to justify adding exposure.
ACCUMULATE_CONFIDENCE = Decimal("0.60")
REDUCE_CONFIDENCE = Decimal("0.60")


@dataclass(frozen=True, slots=True)
class DomainSignal:
    """A domain's directional read, derived from its own evidence."""

    domain: str
    bullish: bool | None  # None = no directional call
    confidence: Decimal


def assess_domain(report: DomainReport, now: dt.datetime,
                  max_age_seconds: float = DEFAULT_MAX_EVIDENCE_AGE_SECONDS
                  ) -> DomainReport:
    """Downgrade a report to STALE if its evidence is too old. Never invents."""

    if report.status is not DomainStatus.OK:
        return report
    if not report.items:
        return DomainReport(report.domain, DomainStatus.MISSING, (),
                            "no evidence items returned")
    if any(i.is_stale(now, max_age_seconds) for i in report.items):
        return DomainReport(report.domain, DomainStatus.STALE, report.items,
                            "evidence older than freshness budget")
    return report


def synthesize(
    reports: dict[str, DomainReport],
    signals: dict[str, DomainSignal],
    *,
    now: dt.datetime,
    strategy_version_id: str,
    holds_btc: bool,
    max_age_seconds: float = DEFAULT_MAX_EVIDENCE_AGE_SECONDS,
) -> DarkHorseDecision:
    """Committee synthesis across all five required domains.

    Degradation policy (explicit, conservative, never fabricated):
    * Any required domain missing/stale -> it is named in ``degraded_domains``.
    * Adding exposure (ACCUMULATE) requires ALL five domains usable and
      sufficient bullish confidence. Uncertainty never buys.
    * Reducing/exiting remains available when degraded — caution is always
      permitted.
    """

    assessed: list[DomainReport] = []
    degraded: list[str] = []
    for domain in REQUIRED_DOMAINS:
        report = reports.get(domain)
        if report is None:
            report = DomainReport(domain, DomainStatus.MISSING, (),
                                  "domain not reported")
        report = assess_domain(report, now, max_age_seconds)
        assessed.append(report)
        if not report.usable:
            degraded.append(domain)

    evidence_ids = tuple(
        item.data_snapshot_id for r in assessed for item in r.items
    )

    usable_signals = [s for d, s in signals.items()
                      if d not in degraded and s.bullish is not None]
    bullish = [s for s in usable_signals if s.bullish]
    bearish = [s for s in usable_signals if not s.bullish]

    def mean(items: list[DomainSignal]) -> Decimal:
        if not items:
            return Decimal("0")
        return sum((s.confidence for s in items), start=Decimal("0")) / Decimal(len(items))

    bull_conf, bear_conf = mean(bullish), mean(bearish)

    if degraded:
        # Never add exposure on incomplete evidence.
        if holds_btc and bear_conf >= REDUCE_CONFIDENCE and len(bearish) > len(bullish):
            action = DarkHorseAction.REDUCE
            rationale = (f"degraded evidence ({', '.join(degraded)}); "
                         f"bearish reads justify reducing exposure")
        else:
            action = DarkHorseAction.HOLD
            rationale = (f"degraded evidence ({', '.join(degraded)}); "
                         f"holding without adding exposure")
    elif len(bullish) > len(bearish) and bull_conf >= ACCUMULATE_CONFIDENCE:
        action = DarkHorseAction.ACCUMULATE
        rationale = (f"{len(bullish)}/5 domains bullish at "
                     f"{bull_conf:.2f} mean confidence")
    elif holds_btc and len(bearish) > len(bullish) and bear_conf >= REDUCE_CONFIDENCE:
        action = (DarkHorseAction.EXIT_TO_CASH if len(bearish) >= 4
                  else DarkHorseAction.REDUCE)
        rationale = (f"{len(bearish)}/5 domains bearish at "
                     f"{bear_conf:.2f} mean confidence")
    else:
        action = DarkHorseAction.HOLD
        rationale = "no decisive multi-domain signal; holding"

    return DarkHorseDecision(
        action=action,
        rationale=rationale,
        reports=tuple(assessed),
        degraded_domains=tuple(degraded),
        evidence_ids=evidence_ids,
        strategy_version_id=strategy_version_id,
    )


def due_for_evaluation(last_evaluated: dt.datetime | None, now: dt.datetime,
                       cadence_seconds: float = DEFAULT_CADENCE_SECONDS) -> bool:
    """Long-horizon cadence gate — avoids forced trading."""

    if last_evaluated is None:
        return True
    return (now - last_evaluated).total_seconds() >= cadence_seconds


@dataclass(slots=True)
class DarkHorseWallet:
    """The permanent Dark Horse wallet + its strategy-version history.

    The wallet object is never replaced and never reset; only the strategy
    version pointer moves.
    """

    wallet: Wallet
    active_version_id: str
    version_history: list[tuple[str, dt.datetime]] = field(default_factory=list)
    _last_valid_version_id: str | None = None

    def __post_init__(self) -> None:
        if self._last_valid_version_id is None:
            self._last_valid_version_id = self.active_version_id

    def upgrade(self, new_version_id: str, at: dt.datetime) -> None:
        """Activate a validated new strategy version. Wallet state untouched."""

        if new_version_id == self.active_version_id:
            raise ValueError("version already active")
        self._last_valid_version_id = self.active_version_id
        self.version_history.append((self.active_version_id, at))
        self.active_version_id = new_version_id

    def rollback(self, at: dt.datetime) -> str:
        """Roll back a technically defective version. Wallet state untouched."""

        if self._last_valid_version_id is None:  # pragma: no cover - guarded by init
            raise ValueError("no previous version to roll back to")
        self.version_history.append((self.active_version_id, at))
        self.active_version_id = self._last_valid_version_id
        return self.active_version_id


def is_exempt_from_elimination(kind: str) -> bool:
    """Both permanent wallets (Dark Horse and Darkhorse - Daily) are exempt
    from weekly loss AND no-trade elimination."""

    return kind in ("dark_horse", "dark_horse_daily")
