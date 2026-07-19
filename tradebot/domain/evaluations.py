"""Weekly evaluation value objects and the canonical profit definition.

`weekly_net_profit_usdt` is the ONE canonical value used for ranking. It equals
``liquidation_adjusted_cutoff_equity - evaluation_start_equity`` and already
includes realized P&L, unrealized P&L marked at the common cutoff, acquisition
and disposal fees, and simulated slippage — each counted exactly once (the
ledger enforces fee-once; liquidation adds the disposal cost of the remaining
position). Profitability is the ONLY ranking value (product rule 8).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .money import quote

RANKING_FORMULA_VERSION = "profit-only-v1"


@dataclass(frozen=True, slots=True)
class WalletEvaluation:
    wallet_id: str
    strategy_version_id: str
    code_hash: str
    structural_fingerprint: str
    kind: str  # active | shadow | dark_horse | dark_horse_daily
    evaluation_start_equity: Decimal
    pre_liquidation_equity: Decimal
    liquidation_adjusted_equity: Decimal
    fill_count: int
    completed_round_trip_count: int

    @property
    def weekly_net_profit_usdt(self) -> Decimal:
        """The single canonical, fixed-point weekly profit value."""

        return quote(self.liquidation_adjusted_equity - self.evaluation_start_equity)

    @property
    def is_losing(self) -> bool:
        return self.weekly_net_profit_usdt < Decimal("0")

    @property
    def traded(self) -> bool:
        return self.fill_count > 0
