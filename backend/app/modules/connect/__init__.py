"""Connect guide — shared config for the /connect projector page.

Demo convenience, design 25-era audience demo: during a live demo the
projector shows the /connect page with the WiFi details, a QR code for the
app URL and a host message so the audience can join. Both endpoints are
login-gated only (get_current_user) — every logged-in demo participant may
read, and any logged-in host may edit; edits are full-replace, audited as
DEMO_CONFIG_UPDATED and committed with the business transaction.

Endpoints (mounted under /api/v1):
- GET /connect-config   current config + server-detected LAN URL
- PUT /connect-config   full replace of the four editable fields
"""

from __future__ import annotations

import socket

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit as audit_log
from app.core.db import get_db
from app.core.models import DemoConfig
from app.core.security import SessionData, get_current_user
from app.core.timeutil import as_utc, utcnow

router = APIRouter(tags=["connect"])

DEMO_CONFIG_UPDATED = "DEMO_CONFIG_UPDATED"


class ConnectConfigRequest(BaseModel):
    """Full replace of the editable fields; blanks are valid."""

    wifi_ssid: str = Field(default="", max_length=64)
    wifi_password: str = Field(default="", max_length=128)
    message: str = Field(default="", max_length=1000)
    url_override: str | None = Field(default=None, max_length=255)


def _lan_url() -> str | None:
    """Best-effort LAN URL of the dev UI (http://<lan-ip>:5173).

    The UDP "connect" trick picks the outbound interface without sending any
    traffic (192.0.2.1 is TEST-NET-1, never routed); OSError → None when the
    host has no LAN route at all.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            ip = sock.getsockname()[0]
    except OSError:
        return None
    return f"http://{ip}:5173"


async def _get_or_create(db: AsyncSession) -> DemoConfig:
    row = await db.get(DemoConfig, 1)
    if row is None:
        row = DemoConfig(id=1)
        db.add(row)
        await db.flush()
    return row


def _config_json(row: DemoConfig) -> dict:
    return {
        "wifi_ssid": row.wifi_ssid,
        "wifi_password": row.wifi_password,
        "message": row.message,
        "url_override": row.url_override,
        "lan_url": _lan_url(),
        "updated_at": as_utc(row.updated_at).isoformat(),
        "updated_by": row.updated_by,
    }


@router.get("/connect-config")
async def get_connect_config(
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Current connect-guide config; creates the single row on first read."""
    row = await _get_or_create(db)
    await db.commit()  # persists the defaults row when this read created it
    return _config_json(row)


@router.put("/connect-config")
async def put_connect_config(
    body: ConnectConfigRequest,
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full replace of the editable fields; visible to every page viewer."""
    row = await _get_or_create(db)
    row.wifi_ssid = body.wifi_ssid
    row.wifi_password = body.wifi_password
    row.message = body.message
    override = (body.url_override or "").strip()
    row.url_override = override or None
    row.updated_by = session.user_id
    row.updated_at = utcnow()
    await db.flush()
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=DEMO_CONFIG_UPDATED,
        resource_type="DEMO_CONFIG",
        resource_id=str(row.id),
        payload={
            "wifi_ssid": row.wifi_ssid,
            "url_override": row.url_override,
            "message_len": len(row.message),
        },
        flush_only=True,
    )
    await db.commit()
    return _config_json(row)
