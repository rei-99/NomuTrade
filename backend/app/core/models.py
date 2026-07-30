"""All SQLAlchemy models for the STP platform.

Conventions (module agents: follow these):
- IDs are String(36) uuid4 strings, portable across SQLite/Postgres.
- Timestamps are DateTime(timezone=True), always UTC (app.core.timeutil.utcnow).
- Money/quantities are Numeric(24, 8) — never float.
- Enum-ish fields are String columns with the StrEnum constants below.
- JSON columns use the generic sqlalchemy JSON type (works on SQLite + Postgres).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.timeutil import utcnow


def uuid_str() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enum-ish constants (stored as String columns)
# ---------------------------------------------------------------------------


class PortfolioType(StrEnum):
    CLIENT = "CLIENT"
    HOUSE = "HOUSE"
    PAPER = "PAPER"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"


class TimeInForce(StrEnum):
    """Order time-in-force (design 24 §D-24.1). GTC preserves the pre-TIF
    resting behavior and is the column default."""

    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"


class OrderStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


class LifecycleState(StrEnum):
    EXECUTED = "EXECUTED"
    AFFIRMED = "AFFIRMED"
    SETTLED = "SETTLED"


class RequestStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    PENDING_INFO = "PENDING_INFO"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class Decision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class GrantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class Severity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    HIGH = "HIGH"


# ---------------------------------------------------------------------------
# IAM / RBAC / JIT / PAM
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    upn: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    manager_upn: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Role(Base):
    __tablename__ = "roles"

    role_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    built_in: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")

    permissions: Mapped[list[Permission]] = relationship(
        secondary="role_permissions", lazy="selectin"
    )


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("action", "resource_type"),)

    permission_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=uuid_str
    )
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("roles.role_id"), primary_key=True
    )
    permission_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("permissions.permission_id"), primary_key=True
    )


class AccessRequest(Base):
    __tablename__ = "access_requests"

    request_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=uuid_str
    )
    requester_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.user_id")
    )
    on_behalf_of: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.user_id"), nullable=True
    )
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("roles.role_id"))
    justification: Mapped[str] = mapped_column(Text)
    requested_duration_hours: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default=RequestStatus.SUBMITTED)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    steps: Mapped[list[ApprovalStep]] = relationship(lazy="selectin")


class ApprovalStep(Base):
    __tablename__ = "approval_steps"

    step_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("access_requests.request_id")
    )
    level: Mapped[int] = mapped_column(Integer)
    approver_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.user_id")
    )
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AccessGrant(Base):
    __tablename__ = "access_grants"

    grant_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"))
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("roles.role_id"))
    request_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("access_requests.request_id"), nullable=True
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default=GrantStatus.ACTIVE)
    revoked_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    user: Mapped[User] = relationship(lazy="selectin")
    role: Mapped[Role] = relationship(lazy="selectin")


class BreakGlassActivation(Base):
    __tablename__ = "break_glass_activations"

    bg_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"))
    emergency_role_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("roles.role_id")
    )
    incident_ref: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    review_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)


class BreakGlassEligibility(Base):
    __tablename__ = "break_glass_eligibility"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.user_id"), primary_key=True
    )
    emergency_role_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("roles.role_id"), primary_key=True
    )


class CredentialCheckout(Base):
    __tablename__ = "credential_checkouts"

    checkout_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=uuid_str
    )
    grant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("access_grants.grant_id")
    )
    safe_name: Mapped[str] = mapped_column(String(255))
    account_id: Mapped[str] = mapped_column(String(255))
    checked_out_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    checked_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    psm_session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class SoDRule(Base):
    __tablename__ = "sod_rules"

    role_a_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("roles.role_id"), primary_key=True
    )
    role_b_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("roles.role_id"), primary_key=True
    )
    effect: Mapped[str] = mapped_column(String(10))  # FLAGGED | BLOCKED


# ---------------------------------------------------------------------------
# Market data / trading / STP
# ---------------------------------------------------------------------------


class Instrument(Base):
    __tablename__ = "instruments"

    instrument_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=uuid_str
    )
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    asset_class: Mapped[str] = mapped_column(String(50))
    currency: Mapped[str] = mapped_column(String(3))
    lot_size: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    tick_size: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    tradable: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bond reference data (design 24 §D-24.3): annual coupon % of par and
    # maturity (midnight UTC). Nullable — equities carry neither.
    coupon_rate: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    maturity_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PriceTick(Base):
    __tablename__ = "price_ticks"

    instrument_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instruments.instrument_id"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    high: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    low: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    close: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 8))


class RestrictedInstrument(Base):
    """Admin-managed restricted list (order-restriction rule, A4/design 21).

    Orders on listed symbols are rejected in pre-trade validation with
    RESTRICTED_INSTRUMENT. Entries are never hard-deleted by the app —
    `active` flips instead of a DELETE for audit-friendliness.
    """

    __tablename__ = "restricted_instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    reason: Mapped[str] = mapped_column(String(500), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class NewsItem(Base):
    """A simulated market-news headline (dataset pack 3, D-14).

    Reference data only — loaded once by the dataset loader, never replayed.
    """

    __tablename__ = "news_items"

    news_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=uuid_str
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    title: Mapped[str] = mapped_column(Text)
    topics: Mapped[list] = mapped_column(JSON, default=list)

    sentiments: Mapped[list["NewsSentiment"]] = relationship(
        back_populates="news_item", cascade="all, delete-orphan"
    )


class NewsSentiment(Base):
    """Per-ticker sentiment annotation of a NewsItem (dataset schema)."""

    __tablename__ = "news_sentiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    news_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("news_items.news_id"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    relevance: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    label: Mapped[str | None] = mapped_column(String(32), nullable=True)

    news_item: Mapped[NewsItem] = relationship(back_populates="sentiments")


class Portfolio(Base):
    __tablename__ = "portfolios"

    portfolio_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=uuid_str
    )
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(10))  # PortfolioType
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"))
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    orders: Mapped[list[Order]] = relationship(lazy="selectin")


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    portfolio_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("portfolios.portfolio_id")
    )
    instrument_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instruments.instrument_id")
    )
    side: Mapped[str] = mapped_column(String(4))  # OrderSide
    order_type: Mapped[str] = mapped_column(String(20))  # OrderType
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    # Time-in-force (design 24 §D-24.1): GTC default keeps pre-TIF behavior;
    # DAY sets expire_after (sim end-of-day) at acceptance; IOC never rests.
    time_in_force: Mapped[str] = mapped_column(String(10), default=TimeInForce.GTC)
    expire_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Trailing stop (design 24 §D-24.2): exactly one of trail_amount /
    # trail_pct; trail_reference is the persisted extreme water-mark.
    trail_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    trail_pct: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    trail_reference: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 8), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.ACCEPTED)
    reject_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    executions: Mapped[list[Execution]] = relationship(lazy="selectin")


class Execution(Base):
    __tablename__ = "executions"

    execution_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=uuid_str
    )
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.order_id"))
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    settlement: Mapped[SettlementInstruction | None] = relationship(lazy="selectin")


class SettlementInstruction(Base):
    __tablename__ = "settlement_instructions"

    settlement_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=uuid_str
    )
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("executions.execution_id"), unique=True
    )
    lifecycle_state: Mapped[str] = mapped_column(
        String(10), default=LifecycleState.EXECUTED
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Position(Base):
    __tablename__ = "positions"

    portfolio_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("portfolios.portfolio_id"), primary_key=True
    )
    instrument_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instruments.instrument_id"), primary_key=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ValuationSnapshot(Base):
    __tablename__ = "valuation_snapshots"

    portfolio_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("portfolios.portfolio_id"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    market_value: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    cash: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 8))


# ---------------------------------------------------------------------------
# Reporting / notifications / assistant
# ---------------------------------------------------------------------------


class Report(Base):
    __tablename__ = "reports"

    report_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    type: Mapped[str] = mapped_column(String(50))
    portfolio_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("portfolios.portfolio_id")
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    format: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="REQUESTED")
    file_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ReportSchedule(Base):
    __tablename__ = "report_schedules"

    schedule_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=uuid_str
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"))
    portfolio_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("portfolios.portfolio_id")
    )
    type: Mapped[str] = mapped_column(String(50))
    format: Mapped[str] = mapped_column(String(10))
    frequency: Mapped[str] = mapped_column(String(10))  # DAILY | WEEKLY
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AlertRule(Base):
    __tablename__ = "alert_rules"

    rule_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"))
    instrument_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("instruments.instrument_id")
    )
    condition: Mapped[str] = mapped_column(String(50))
    threshold: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Notification(Base):
    __tablename__ = "notifications"

    notification_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=uuid_str
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"))
    category: Mapped[str] = mapped_column(String(50))
    channel: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AssistantInteraction(Base):
    __tablename__ = "assistant_interactions"

    interaction_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=uuid_str
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"))
    prompt: Mapped[str] = mapped_column(Text)
    response: Mapped[str] = mapped_column(Text)
    grounded_refs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


# ---------------------------------------------------------------------------
# Audit + outbox (infrastructure tables)
# ---------------------------------------------------------------------------


class AuditEvent(Base):
    __tablename__ = "audit_events"

    # seq is the integer PK used for chain ordering; event_id is the public id.
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, default=uuid_str)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    severity: Mapped[str] = mapped_column(String(10), default=Severity.INFO)
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stream: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
