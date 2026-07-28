"""Idempotent seed: permission catalog, built-in roles, demo users + grants,
US-equity + bond instruments, and demo portfolios.

Usage:
    python -m app.seed          # seeds the default ./stp.db from Settings
"""

import asyncio
from datetime import timedelta
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

# (symbol, name, asset_class, lot_size, tick_size) — fallback instrument
# universe when the simulation dataset (data/, INT-04) is absent: 7 US
# equities (lot 1) + 4 bonds quoted % of par (lot 1000 face value, design 21
# §A2), USD, tick 0.01. When the dataset is present the marketdata loader
# upserts these same symbols from it. Kept in sync with
# app.modules.marketdata.loader.DATASET_INSTRUMENTS / BOND_INSTRUMENTS.
INSTRUMENTS: list[tuple[str, str, str, Decimal, Decimal]] = [
    ("AAPL", "Apple", "EQUITY", Decimal("1"), Decimal("0.01")),
    ("GOOG", "Alphabet", "EQUITY", Decimal("1"), Decimal("0.01")),
    ("IBM", "IBM", "EQUITY", Decimal("1"), Decimal("0.01")),
    ("MSFT", "Microsoft", "EQUITY", Decimal("1"), Decimal("0.01")),
    ("TSLA", "Tesla", "EQUITY", Decimal("1"), Decimal("0.01")),
    ("UL", "Unilever", "EQUITY", Decimal("1"), Decimal("0.01")),
    ("WMT", "Walmart", "EQUITY", Decimal("1"), Decimal("0.01")),
    ("UST10Y", "US Treasury 4.25% 2035", "BOND", Decimal("1000"), Decimal("0.01")),
    ("UST2Y", "US Treasury 3.75% 2027", "BOND", Decimal("1000"), Decimal("0.01")),
    ("AAPL29", "Apple Corp 3.40% 2029", "BOND", Decimal("1000"), Decimal("0.01")),
    ("MSFT31", "Microsoft Corp 3.10% 2031", "BOND", Decimal("1000"), Decimal("0.01")),
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
    # universe, D-12 + design 21 §A2).
    for symbol, name, asset_class, lot_size, tick_size in INSTRUMENTS:
        session.add(
            Instrument(
                symbol=symbol,
                name=name,
                asset_class=asset_class,
                currency="USD",
                lot_size=lot_size,
                tick_size=tick_size,
                tradable=True,
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
