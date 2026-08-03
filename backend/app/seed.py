"""Idempotent seed: permission catalog, built-in roles, demo users + grants,
US-equity + bond instruments, and demo portfolios.

Usage:
    python -m app.seed          # seeds the default ./stp.db from Settings
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.db import create_all, get_engine, get_sessionmaker
from app.core.models import (
    AccessGrant,
    GrantStatus,
    Instrument,
    Permission,
    Portfolio,
    PortfolioType,
    Role,
    RolePermission,
    User,
)
from app.core.timeutil import utcnow
from app.modules.auth.passwords import demo_password_hash

logger = logging.getLogger(__name__)

# Audience demo accounts (owner ask, 2026-08): trader_1..trader_100 share the
# demo password, hold the Trader role, and each gets an empty funded HOUSE
# book — every demo participant can log in and trade as their own trader.
DEMO_TRADER_COUNT = 100
DEMO_TRADER_BOOK_CASH = Decimal("100000")  # 100k USD per audience book


async def ensure_demo_traders(sessionmaker, count: int = DEMO_TRADER_COUNT) -> int:
    """Idempotent startup patch: trader_1..trader_N@demo.nomura accounts.

    Missing accounts are created with an ACTIVE Trader grant, the shared demo
    password (same patch mechanism as design 26 §R2), and an empty funded
    HOUSE book. Existing accounts are skipped, so it runs on every boot and
    converges dev DBs and fresh remote machines alike (the once-only seed
    cannot). Returns the number of accounts created.
    """
    async with sessionmaker() as session:
        role = (
            await session.execute(select(Role).where(Role.name == "Trader"))
        ).scalar_one_or_none()
        if role is None:
            return 0  # unseeded DB — the seed itself creates the catalog first
        emails = [f"trader_{i}@demo.nomura" for i in range(1, count + 1)]
        existing = set(
            (
                await session.execute(select(User.email).where(User.email.in_(emails)))
            ).scalars().all()
        )
        now = utcnow()
        created = 0
        for email in emails:
            if email in existing:
                continue
            user = User(
                upn=email,
                display_name=f"Demo Trader {email.split('@')[0].split('_')[1]}",
                email=email,
                status="ACTIVE",
                synced_at=now,
                password_hash=demo_password_hash(),
            )
            session.add(user)
            await session.flush()
            session.add(
                AccessGrant(
                    user_id=user.user_id,
                    role_id=role.role_id,
                    request_id=None,
                    start_at=now - timedelta(days=1),
                    end_at=now + timedelta(days=3650),
                    status=GrantStatus.ACTIVE,
                )
            )
            session.add(
                Portfolio(
                    name=f"Trader {user.display_name.rsplit(' ', 1)[1]} Book",
                    type=PortfolioType.HOUSE,
                    owner_id=user.user_id,
                    cash_balance=DEMO_TRADER_BOOK_CASH,
                )
            )
            created += 1
        if created:
            await session.commit()
    if created:
        logger.info("demo traders ensured: %d new account(s) (trader_N@demo.nomura)", created)
    return created

# (action, resource_type) — action names are globally unique and double as the
# permission strings used with require_permission().
PERMISSION_CATALOG: list[tuple[str, str]] = [
    ("ORDER_SUBMIT", "ORDER"),
    ("ORDER_VIEW", "ORDER"),
    ("ORDER_CANCEL", "ORDER"),
    ("TRADE_VIEW", "TRADE"),
    ("STP_EXCEPTION_HANDLE", "TRADE"),
    ("PORTFOLIO_VIEW", "PORTFOLIO"),
    ("PORTFOLIO_VIEW_ALL", "PORTFOLIO"),
    ("REPORT_VIEW", "REPORT"),
    ("PAPER_TRADE", "PAPER_ACCOUNT"),
    ("ASSISTANT_USE", "ASSISTANT"),
    ("APPROVE_ACCESS", "ACCESS_REQUEST"),
    ("ROLE_VIEW", "ROLE"),
    ("ROLE_MANAGE", "ROLE"),
    ("GRANT_VIEW", "ACCESS_GRANT"),
    ("GRANT_REVOKE", "ACCESS_GRANT"),
    ("GRANT_MANAGE", "ACCESS_GRANT"),
    ("PAM_CHECKOUT", "PAM_ACCOUNT"),
    ("BREAKGLASS_ELIGIBLE", "BREAK_GLASS"),
    ("BREAKGLASS_REVIEW", "BREAK_GLASS"),
    ("AUDIT_VIEW", "AUDIT"),
    ("AUDIT_EXPORT", "AUDIT"),
    ("GOVERNANCE_VIEW", "GOVERNANCE"),
    ("INTEGRATION_MONITOR", "INTEGRATION"),
]

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "Trader": [
        "ORDER_SUBMIT", "ORDER_VIEW", "ORDER_CANCEL", "TRADE_VIEW",
        "PORTFOLIO_VIEW", "REPORT_VIEW", "PAPER_TRADE", "ASSISTANT_USE",
    ],
    "Client": ["PORTFOLIO_VIEW", "REPORT_VIEW", "ASSISTANT_USE"],
    "Operations Analyst": [
        "TRADE_VIEW", "PORTFOLIO_VIEW_ALL", "STP_EXCEPTION_HANDLE",
        "INTEGRATION_MONITOR",
    ],
    "Risk & Compliance": [
        "PORTFOLIO_VIEW_ALL", "TRADE_VIEW", "AUDIT_VIEW", "GOVERNANCE_VIEW",
        "REPORT_VIEW",
    ],
    "Approver": ["APPROVE_ACCESS"],
    "System Administrator": [
        "PAM_CHECKOUT", "BREAKGLASS_ELIGIBLE", "INTEGRATION_MONITOR",
    ],
    "Security Administrator": [
        "ROLE_VIEW", "ROLE_MANAGE", "GRANT_VIEW", "GRANT_REVOKE",
        "GRANT_MANAGE", "BREAKGLASS_ELIGIBLE", "BREAKGLASS_REVIEW",
        "AUDIT_VIEW", "AUDIT_EXPORT", "GOVERNANCE_VIEW", "APPROVE_ACCESS",
    ],
    "Auditor": ["AUDIT_VIEW", "AUDIT_EXPORT"],
}

# (email/upn, display_name, role name)
DEMO_USERS: list[tuple[str, str, str]] = [
    ("trader@demo.nomura", "Demo Trader", "Trader"),
    ("client@demo.nomura", "Demo Client", "Client"),
    ("ops@demo.nomura", "Demo Operations Analyst", "Operations Analyst"),
    ("risk@demo.nomura", "Demo Risk Analyst", "Risk & Compliance"),
    ("approver@demo.nomura", "Demo Approver", "Approver"),
    ("sysadmin@demo.nomura", "Demo System Administrator", "System Administrator"),
    ("secadmin@demo.nomura", "Demo Security Administrator", "Security Administrator"),
    ("auditor@demo.nomura", "Demo Auditor", "Auditor"),
]

# (symbol, name, asset_class, lot_size, tick_size, coupon_rate, maturity_date)
# — fallback instrument universe when the simulation dataset (data/, INT-04)
# is absent: 7 US equities (lot 1) + 4 bonds quoted % of par (lot 1000 face
# value, design 21 §A2), USD, tick 0.01. Bond rows carry the structured
# coupon (% of par, annual) / maturity of design 24 §D-24.3 (None on
# equities). When the dataset is present the marketdata loader upserts these
# same symbols from it. Kept in sync with
# app.modules.marketdata.loader.DATASET_INSTRUMENTS / BOND_INSTRUMENTS.
INSTRUMENTS: list[
    tuple[str, str, str, Decimal, Decimal, Decimal | None, date | None]
] = [
    ("AAPL", "Apple", "EQUITY", Decimal("1"), Decimal("0.01"), None, None),
    ("GOOG", "Alphabet", "EQUITY", Decimal("1"), Decimal("0.01"), None, None),
    ("IBM", "IBM", "EQUITY", Decimal("1"), Decimal("0.01"), None, None),
    ("MSFT", "Microsoft", "EQUITY", Decimal("1"), Decimal("0.01"), None, None),
    ("TSLA", "Tesla", "EQUITY", Decimal("1"), Decimal("0.01"), None, None),
    ("UL", "Unilever", "EQUITY", Decimal("1"), Decimal("0.01"), None, None),
    ("WMT", "Walmart", "EQUITY", Decimal("1"), Decimal("0.01"), None, None),
    ("UST10Y", "US Treasury 4.25% 2035", "BOND", Decimal("1000"), Decimal("0.01"),
     Decimal("4.25"), date(2035, 8, 15)),
    ("UST2Y", "US Treasury 3.75% 2027", "BOND", Decimal("1000"), Decimal("0.01"),
     Decimal("3.75"), date(2027, 6, 15)),
    ("AAPL29", "Apple Corp 3.40% 2029", "BOND", Decimal("1000"), Decimal("0.01"),
     Decimal("3.40"), date(2029, 3, 15)),
    ("MSFT31", "Microsoft Corp 3.10% 2031", "BOND", Decimal("1000"), Decimal("0.01"),
     Decimal("3.10"), date(2031, 9, 15)),
]


async def seed(session: AsyncSession) -> None:
    """Seed catalog + demo data. No-op if any user exists. Caller commits."""
    existing = await session.scalar(select(func.count(User.user_id)))
    if existing:
        return

    # Permission catalog.
    perm_by_action: dict[str, Permission] = {}
    for action, resource_type in PERMISSION_CATALOG:
        perm = Permission(
            action=action,
            resource_type=resource_type,
            description=f"{action} on {resource_type}",
        )
        session.add(perm)
        perm_by_action[action] = perm

    # Built-in roles.
    roles: dict[str, Role] = {}
    for role_name in ROLE_PERMISSIONS:
        role = Role(
            name=role_name,
            description=f"Built-in role: {role_name}",
            built_in=True,
            version=1,
            status="ACTIVE",
        )
        session.add(role)
        roles[role_name] = role
    await session.flush()  # materialize permission/role ids

    # Role -> permission assignments.
    for role_name, actions in ROLE_PERMISSIONS.items():
        for action in actions:
            session.add(
                RolePermission(
                    role_id=roles[role_name].role_id,
                    permission_id=perm_by_action[action].permission_id,
                )
            )

    # Demo users.
    users: dict[str, User] = {}
    for email, display_name, _role in DEMO_USERS:
        user = User(
            upn=email,
            display_name=display_name,
            email=email,
            status="ACTIVE",
            synced_at=utcnow(),
        )
        session.add(user)
        users[email] = user
    await session.flush()

    # Each demo user gets an ACTIVE grant for their same-named role:
    # start now-1d, end now+10y, request_id null.
    now = utcnow()
    for email, _display_name, role_name in DEMO_USERS:
        session.add(
            AccessGrant(
                user_id=users[email].user_id,
                role_id=roles[role_name].role_id,
                request_id=None,
                start_at=now - timedelta(days=1),
                end_at=now + timedelta(days=3650),
                status=GrantStatus.ACTIVE,
            )
        )

    # Instruments (7 US equities + 4 generated-price bonds, USD — dataset
    # universe, D-12 + design 21 §A2; bonds carry coupon/maturity, design 24).
    for symbol, name, asset_class, lot_size, tick_size, coupon, maturity in INSTRUMENTS:
        session.add(
            Instrument(
                symbol=symbol,
                name=name,
                asset_class=asset_class,
                currency="USD",
                lot_size=lot_size,
                tick_size=tick_size,
                tradable=True,
                coupon_rate=coupon,
                maturity_date=(
                    datetime(
                        maturity.year, maturity.month, maturity.day,
                        tzinfo=timezone.utc,
                    )
                    if maturity is not None
                    else None
                ),
            )
        )

    # Demo portfolios (USD).
    session.add(
        Portfolio(
            name="Client Portfolio A",
            type=PortfolioType.CLIENT,
            owner_id=users["client@demo.nomura"].user_id,
            cash_balance=Decimal("1000000"),  # 1M USD
        )
    )
    session.add(
        Portfolio(
            name="Desk Book 1",
            type=PortfolioType.HOUSE,
            owner_id=users["trader@demo.nomura"].user_id,
            cash_balance=Decimal("500000"),  # 500k USD
        )
    )

    await session.flush()


def _main() -> None:
    settings = Settings()
    engine = get_engine(settings)
    sessionmaker = get_sessionmaker(settings, engine)

    async def run() -> None:
        await create_all(engine)
        async with sessionmaker() as session:
            await seed(session)
            await session.commit()
        await engine.dispose()

    asyncio.run(run())
    print(f"Seeded database: {settings.DATABASE_URL}")


if __name__ == "__main__":
    _main()
