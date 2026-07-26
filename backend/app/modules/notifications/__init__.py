"""Notifications module: in-app delivery + simulated email, preferences (FR-NTF).

Worker consumes the `notify` stream (payload {"user_id", "category", "title",
"body"}), persists an IN_APP Notification row and logs a line simulating an
SMTP send (DESIGN 14: real email delivery is out of MVP scope).

PREFERENCES ARE PROCESS-LOCAL: the per-user channel/category matrix lives in a
module-level dict — it is lost on restart and not shared across replicas.
Accepted MVP limitation; move to a table when multi-replica deployment lands.
Security-critical categories (BREAK_GLASS, GRANT, PAM) are hard-coded
non-suppressible (FR-NTF-003 E1).
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import BusinessRuleViolation, NotFound, ValidationError
from app.core.models import Notification, User
from app.core.security import SessionData, get_current_user
from app.core.timeutil import as_utc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["notifications"])

# Categories that can never be silenced (FR-NTF-003 E1).
NON_SUPPRESSIBLE_CATEGORIES = frozenset({"BREAK_GLASS", "GRANT", "PAM"})
DEFAULT_CHANNELS = {"IN_APP": True, "EMAIL": True}

# Process-local preferences: {user_id: {"channels": {...}, "categories": {...}}}.
# Absent category keys mean "enabled" (default everything enabled).
_preferences: dict[str, dict] = {}


def _prefs_for(user_id: str) -> dict:
    return _preferences.setdefault(
        user_id, {"channels": dict(DEFAULT_CHANNELS), "categories": {}}
    )


def _prefs_json(prefs: dict) -> dict:
    return {
        "channels": {**DEFAULT_CHANNELS, **prefs["channels"]},
        "categories": dict(prefs["categories"]),
    }


# ---------------------------------------------------------------------------
# Worker: notify stream -> Notification row + simulated email
# ---------------------------------------------------------------------------


async def handle_notify(sessionmaker, event: dict) -> bool:
    """Persist one `notify` event as an IN_APP notification and simulate email.

    At-least-once delivery is acceptable here (no dedup key on the stream), so
    a redelivery may create a duplicate row. Returns True when delivered.
    """
    user_id = event.get("user_id")
    if not user_id:
        logger.warning("notify event without user_id dropped: %r", event)
        return False
    category = event.get("category") or "GENERAL"
    title = event.get("title") or ""
    body = event.get("body") or ""

    prefs = _preferences.get(user_id)
    if prefs and not prefs["categories"].get(category, True):
        logger.info(
            "notification suppressed by user preferences: user=%s category=%s",
            user_id,
            category,
        )
        return False

    in_app = prefs["channels"].get("IN_APP", True) if prefs else True
    email = prefs["channels"].get("EMAIL", True) if prefs else True

    user_email = None
    if in_app:
        async with sessionmaker() as session:
            user = await session.get(User, user_id)
            if user is None:
                logger.warning("notify event for unknown user %s dropped", user_id)
                return False
            user_email = user.email
            session.add(
                Notification(
                    user_id=user_id,
                    category=category,
                    channel="IN_APP",
                    payload={"title": title, "body": body},
                    status="UNREAD",
                )
            )
            await session.commit()
    else:
        async with sessionmaker() as session:
            user = await session.get(User, user_id)
            user_email = user.email if user else None

    if email and user_email:
        # Simulated SMTP send (DESIGN 14): real relay + CyberArk credentials later.
        logger.info("email to %s: %s", user_email, title)
    return in_app


async def notification_worker(bus, sessionmaker) -> None:
    """Consume the `notify` stream forever; isolate per-event failures."""
    subscription = await bus.subscribe("notify")
    async for event in subscription:
        try:
            await handle_notify(sessionmaker, event)
        except Exception:  # keep the worker alive on a bad event
            logger.exception("notification worker failed on event: %r", event)


def get_workers(settings):
    return [notification_worker]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _notification_json(n: Notification) -> dict:
    return {
        "notification_id": n.notification_id,
        "category": n.category,
        "channel": n.channel,
        "payload": n.payload,
        "status": n.status,
        "created_at": as_utc(n.created_at).isoformat(),
    }


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    ts_raw, sep, nid = cursor.partition("|")
    if not sep or not nid:
        raise ValidationError("invalid cursor")
    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError:
        raise ValidationError("invalid cursor")
    return ts, nid


@router.get("/notifications")
async def list_notifications(
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """My notifications, newest first, cursor-paginated."""
    stmt = select(Notification).where(Notification.user_id == session.user_id)
    if cursor:
        ts, nid = _decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                Notification.created_at < ts,
                and_(
                    Notification.created_at == ts,
                    Notification.notification_id < nid,
                ),
            )
        )
    stmt = stmt.order_by(
        Notification.created_at.desc(), Notification.notification_id.desc()
    ).limit(limit + 1)
    rows = (await db.execute(stmt)).scalars().all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = f"{as_utc(last.created_at).isoformat()}|{last.notification_id}"
    return {
        "items": [_notification_json(n) for n in page],
        "next_cursor": next_cursor,
    }


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    n = await db.get(Notification, notification_id)
    if n is None or n.user_id != session.user_id:
        raise NotFound("notification not found")
    n.status = "READ"
    await db.commit()
    return {"notification_id": n.notification_id, "status": n.status}


class PreferencesPatch(BaseModel):
    channels: dict[str, bool] | None = None
    categories: dict[str, bool] | None = None


@router.get("/notification-preferences")
async def get_preferences(session: SessionData = Depends(get_current_user)):
    """My preferences; absent category keys mean enabled (default all enabled)."""
    return _prefs_json(_prefs_for(session.user_id))


@router.patch("/notification-preferences")
async def patch_preferences(
    body: PreferencesPatch,
    session: SessionData = Depends(get_current_user),
):
    prefs = _prefs_for(session.user_id)
    if body.channels:
        for channel, enabled in body.channels.items():
            if channel not in DEFAULT_CHANNELS:
                raise ValidationError(f"unknown channel: {channel}")
            prefs["channels"][channel] = enabled
    if body.categories:
        for category, enabled in body.categories.items():
            if not enabled and category in NON_SUPPRESSIBLE_CATEGORIES:
                raise BusinessRuleViolation(
                    f"category {category} is security-critical and cannot be disabled"
                )
        for category, enabled in body.categories.items():
            prefs["categories"][category] = enabled
    return _prefs_json(prefs)
