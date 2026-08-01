"""Password-login tests (design 26 §R2).

Covers: good login, uniform 401 for wrong password vs unknown email (no
enumeration), lockout after LOGIN_MAX_FAILURES consecutive failures (with
retry hint), counter reset on success, audit rows, and dev-login sanity.
"""

import pytest
from sqlalchemy import select

from app.core.audit import AUTH_LOGIN_FAILURE, AUTH_LOGIN_SUCCESS
from app.core.models import AuditEvent
from app.modules import auth as auth_module
from app.modules.auth.passwords import DEMO_PASSWORD
from conftest import login

TRADER = "trader@demo.nomura"


@pytest.fixture(autouse=True)
def _clear_login_failures():
    """The lockout counter is process-global module state — isolate tests."""
    auth_module._LOGIN_FAILURES.clear()
    yield
    auth_module._LOGIN_FAILURES.clear()


async def _login(client, email: str, password: str):
    return await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


async def test_login_success_and_me(client):
    response = await _login(client, TRADER, DEMO_PASSWORD)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"]
    assert body["user"]["email"] == TRADER
    assert body["user"]["upn"] == TRADER

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert me.status_code == 200
    assert me.json()["user"]["email"] == TRADER
    assert "Trader" in me.json()["roles"]


async def test_wrong_password_and_unknown_email_are_indistinguishable(client):
    wrong_pw = await _login(client, TRADER, "not-the-password")
    unknown = await _login(client, "nobody@demo.nomura", DEMO_PASSWORD)

    for response in (wrong_pw, unknown):
        assert response.status_code == 401
    wrong_err = wrong_pw.json()["error"]
    unknown_err = unknown.json()["error"]
    # Same code + message for both failure modes — no user enumeration.
    assert wrong_err["code"] == unknown_err["code"] == "UNAUTHENTICATED"
    assert wrong_err["message"] == unknown_err["message"] == "invalid credentials"
    assert wrong_err["traceId"] and unknown_err["traceId"]


async def test_lockout_after_max_failures(client):
    for _ in range(auth_module.LOGIN_MAX_FAILURES):
        response = await _login(client, TRADER, "bad")
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "invalid credentials"

    # Next attempt within the window is locked — even with the right password.
    locked = await _login(client, TRADER, DEMO_PASSWORD)
    assert locked.status_code == 401
    error = locked.json()["error"]
    assert error["code"] == "UNAUTHENTICATED"
    assert error["message"].startswith("temporarily locked, retry in ")
    retry_after = error["details"][0]["retry_after_seconds"]
    assert 1 <= retry_after <= auth_module.LOGIN_LOCKOUT_SECONDS

    # Window expiry resets the counter (monkeypatched short window).
    import time

    original = auth_module.LOGIN_LOCKOUT_SECONDS
    auth_module._LOGIN_FAILURES.clear()
    try:
        auth_module.LOGIN_LOCKOUT_SECONDS = 0.2
        for _ in range(auth_module.LOGIN_MAX_FAILURES):
            await _login(client, TRADER, "bad")
        time.sleep(0.25)
        recovered = await _login(client, TRADER, DEMO_PASSWORD)
        assert recovered.status_code == 200
    finally:
        auth_module.LOGIN_LOCKOUT_SECONDS = original


async def test_success_resets_failure_counter(client):
    for _ in range(auth_module.LOGIN_MAX_FAILURES - 1):
        response = await _login(client, TRADER, "bad")
        assert response.status_code == 401

    ok = await _login(client, TRADER, DEMO_PASSWORD)
    assert ok.status_code == 200

    # One failure after the success: plain invalid-credentials, not locked.
    fail = await _login(client, TRADER, "bad")
    assert fail.status_code == 401
    assert fail.json()["error"]["message"] == "invalid credentials"


async def test_login_audit_rows(client, app):
    await _login(client, TRADER, "bad")
    await _login(client, TRADER, DEMO_PASSWORD)

    async with app.state.sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type.in_(
                            [AUTH_LOGIN_FAILURE, AUTH_LOGIN_SUCCESS]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    event_types = {row.event_type for row in rows}
    assert AUTH_LOGIN_FAILURE in event_types
    assert AUTH_LOGIN_SUCCESS in event_types
    failure = next(r for r in rows if r.event_type == AUTH_LOGIN_FAILURE)
    assert failure.payload["email"] == TRADER


async def test_dev_login_still_works(client):
    headers = await login(client, TRADER)
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["user"]["email"] == TRADER
