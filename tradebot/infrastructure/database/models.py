"""Canonical normalized schema (SQLAlchemy 2.x).

The database is the single source of truth; JSON/Markdown are derived exports.
Foreign keys and check constraints enforce accounting and lifecycle invariants
(A04/A22).

**Money is stored as TEXT, not Numeric.** SQLite has no native decimal type:
``Numeric`` there is stored as ``REAL`` and SQLAlchemy binds it through a binary
``float``, which (a) violates the "never binary floating point" rule and (b)
makes the declared 24-digit precision a false promise — float64 carries ~15-17
significant digits. Verified: ``Decimal("123456789012.12345678")`` round-tripped
as ``123456789012.12345886``. :class:`DecimalText` stores the exact decimal
string instead, so persistence matches the in-memory fixed-point invariant.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class DecimalText(TypeDecorator):
    """Exact fixed-point storage: Decimal <-> TEXT, never via float.

    ``scale`` quantizes on the way in so a column's precision is enforced at
    the boundary rather than trusted. Binary floats are rejected outright, in
    keeping with ``tradebot.domain.money``.
    """

    impl = Text
    cache_ok = True

    def __init__(self, scale: str) -> None:
        super().__init__()
        self.scale = Decimal(scale)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError("float is not allowed in money columns; use Decimal")
        return str(Decimal(value).quantize(self.scale))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value)


QUOTE = DecimalText("0.01")
BASE = DecimalText("0.00000001")
PRICE = DecimalText("0.01")


@event.listens_for(Engine, "connect")
def _enable_sqlite_fk(dbapi_connection, _record):  # pragma: no cover - driver glue
    """Enforce foreign keys on every SQLite connection."""

    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Base(DeclarativeBase):
    pass


WALLET_KINDS = ("active", "shadow", "dark_horse", "dark_horse_daily", "archived")
STRATEGY_ORIGINS = ("builtin", "novel", "mutation", "dark_horse",
                    "dark_horse_daily")


class Wallet(Base):
    __tablename__ = "wallets"

    wallet_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    wallet_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    stable_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    initial_quote_balance: Mapped[Decimal] = mapped_column(QUOTE, nullable=False)
    quote_cash: Mapped[Decimal] = mapped_column(QUOTE, nullable=False)
    base_qty: Mapped[Decimal] = mapped_column(BASE, nullable=False, default=Decimal("0"))
    avg_cost: Mapped[Decimal] = mapped_column(PRICE, nullable=False, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(QUOTE, nullable=False, default=Decimal("0"))
    total_fees: Mapped[Decimal] = mapped_column(QUOTE, nullable=False, default=Decimal("0"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    assignments: Mapped[list["WalletStrategyAssignment"]] = relationship(
        back_populates="wallet"
    )

    __table_args__ = (
        CheckConstraint(
            "wallet_kind IN "
            "('active','shadow','dark_horse','dark_horse_daily','archived')",
            name="ck_wallet_kind"),
        CheckConstraint("quote_cash >= 0", name="ck_wallet_quote_nonneg"),
        CheckConstraint("base_qty >= 0", name="ck_wallet_base_nonneg"),
    )


class StrategyDefinition(Base):
    __tablename__ = "strategy_definitions"

    strategy_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conceptual_family: Mapped[str] = mapped_column(String(80), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(120), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    permanently_banned: Mapped[bool] = mapped_column(default=False, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "origin IN "
            "('builtin','novel','mutation','dark_horse','dark_horse_daily')",
            name="ck_strategy_origin"),
    )


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    strategy_version_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_definitions.strategy_id"), nullable=False
    )
    semantic_version: Mapped[str] = mapped_column(String(32), nullable=False)
    generation: Mapped[int] = mapped_column(nullable=False, default=0)
    bundle_path: Mapped[str] = mapped_column(String(255), nullable=False)
    source_code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    structural_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="candidate")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    retired_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class WalletStrategyAssignment(Base):
    __tablename__ = "wallet_strategy_assignments"

    assignment_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.wallet_id"), nullable=False)
    strategy_version_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_versions.strategy_version_id"), nullable=False
    )
    activated_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    deactivated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    starting_quote: Mapped[Decimal] = mapped_column(QUOTE, nullable=False)
    starting_base: Mapped[Decimal] = mapped_column(BASE, nullable=False, default=Decimal("0"))

    wallet: Mapped[Wallet] = relationship(back_populates="assignments")

    __table_args__ = (
        # At most one *active* (not-yet-deactivated) assignment per wallet.
        UniqueConstraint("wallet_id", "deactivated_at", name="uq_wallet_active_assignment"),
    )


class StrategyBan(Base):
    __tablename__ = "strategy_bans"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    structural_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    banned_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    permanent: Mapped[bool] = mapped_column(default=True, nullable=False)


class MarketSnapshotRow(Base):
    __tablename__ = "market_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    open_time_ms: Mapped[int] = mapped_column(nullable=False)
    close_time_ms: Mapped[int] = mapped_column(nullable=False)
    is_closed: Mapped[bool] = mapped_column(nullable=False)
    open: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    high: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    low: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    close: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    volume: Mapped[Decimal] = mapped_column(BASE, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at_ms: Mapped[int] = mapped_column(nullable=False)
    source_time_ms: Mapped[int] = mapped_column(nullable=False)


class LedgerTransactionRow(Base):
    __tablename__ = "ledger_transactions"

    transaction_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.wallet_id"), nullable=False)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    fill_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    strategy_version_id: Mapped[str] = mapped_column(String(40), nullable=False)
    market_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    qty: Mapped[Decimal] = mapped_column(BASE, nullable=False)
    price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    fee: Mapped[Decimal] = mapped_column(QUOTE, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)

    postings: Mapped[list["LedgerPostingRow"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("side IN ('BUY','SELL')", name="ck_txn_side"),
        CheckConstraint("qty > 0", name="ck_txn_qty_pos"),
        CheckConstraint("price > 0", name="ck_txn_price_pos"),
    )


class LedgerPostingRow(Base):
    __tablename__ = "ledger_postings"

    posting_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("ledger_transactions.transaction_id"), nullable=False
    )
    account: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    # Postings span both currencies, so use the finer (base) scale.
    amount: Mapped[Decimal] = mapped_column(BASE, nullable=False)

    transaction: Mapped[LedgerTransactionRow] = relationship(back_populates="postings")


class JobRun(Base):
    __tablename__ = "job_runs"

    job_run_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    job_type: Mapped[str] = mapped_column(String(48), nullable=False)
    scheduled_window: Mapped[str] = mapped_column(String(48), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    failed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


SCHEMA_VERSION = "v2.0.0"
