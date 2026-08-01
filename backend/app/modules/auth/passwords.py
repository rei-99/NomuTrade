"""Password hashing helpers + the idempotent demo-password startup patch.

Design 26 §R2 — training-environment credential check. The login endpoint
calls `verify_password` behind the same seam a real SSO/IdP check would
replace later; production stays SSO-only per the SRS.

Hash: PBKDF2-HMAC-SHA256 (stdlib `hashlib` only, no new dependencies),
120,000 iterations, 16-byte random salt, stored as
"pbkdf2$<iterations>$<salt_hex>$<hash_hex>", verified with
`hmac.compare_digest`. Malformed or NULL stored values verify as False.

TRAINING-ENV DEFAULT: `ensure_demo_passwords` gives every user without a
hash the shared demo password `demo1234` (documented in README and the
login-page hint box). Demo convenience only — never for production.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.models import User

logger = logging.getLogger(__name__)

_ALGORITHM = "pbkdf2"
_ITERATIONS = 120_000
_SALT_BYTES = 16

# Training-environment default password (design 26 §R2). Shared by all demo
# users; clearly marked as demo-only wherever it is documented.
DEMO_PASSWORD = "demo1234"

# Lazily computed hash of DEMO_PASSWORD, reused for every patched user: all
# demo users share the demo password by design, and computing PBKDF2 once per
# process keeps app startup (and the test-suite lifespan) fast.
_demo_hash: str | None = None


def hash_password(password: str) -> str:
    """Hash a password as "pbkdf2$<iterations>$<salt_hex>$<hash_hex>"."""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _ITERATIONS
    )
    return f"{_ALGORITHM}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Check a password against a stored hash.

    Returns False for NULL or malformed stored values (never raises).
    """
    if not stored:
        return False
    try:
        algorithm, iterations, salt_hex, hash_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


def demo_password_hash() -> str:
    """The (process-cached) hash of the shared training demo password."""
    global _demo_hash
    if _demo_hash is None:
        _demo_hash = hash_password(DEMO_PASSWORD)
    return _demo_hash


async def ensure_demo_passwords(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> int:
    """Idempotent startup patch: backfill the demo password on hash-less users.

    Every user with password_hash NULL gets the training demo password. Runs
    on every boot (main lifespan, after seeding) so it also reaches dev DBs
    created before password login existed, which the once-only seed cannot.
    Returns the number of users patched (0 on subsequent boots).
    """
    async with sessionmaker() as session:
        users = (
            (await session.execute(select(User).where(User.password_hash.is_(None))))
            .scalars()
            .all()
        )
        for user in users:
            user.password_hash = demo_password_hash()
        if users:
            await session.commit()
    logger.info(
        "demo password (%r, training default) ensured for %d user(s)",
        DEMO_PASSWORD,
        len(users),
    )
    return len(users)
