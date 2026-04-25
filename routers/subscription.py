"""
Public subscription endpoints — consumed by VPN client apps.

GET /sub/{token}          → default format (from settings or ?fmt= query param)
GET /sub/{token}/json     → XRay JSON config (Happ, Hiddify, v2rayN JSON import)
GET /sub/{token}/v2ray    → base64-encoded VLESS links (v2rayNG, NekoBox)
GET /sub/{token}/clash    → YAML config (Clash Meta)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models import Client, Server
from subscription import build_user_subscription

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sub", tags=["subscription"])

# CORS headers so VPN apps can fetch via XHR
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}


async def _get_client_by_token(token: str, db: AsyncSession) -> Client | None:
    result = await db.execute(
        select(Client).where(Client.vless_sub_token == token, Client.is_active == True)
    )
    return result.scalar_one_or_none()


async def _get_servers(db: AsyncSession) -> list[Server]:
    result = await db.execute(
        select(Server).where(Server.is_active == True).order_by(Server.priority)
    )
    return result.scalars().all()


async def _build_response(token: str, fmt: str, db: AsyncSession) -> Response:
    client = await _get_client_by_token(token, db)
    if not client:
        return Response(content="Not found", status_code=404)

    servers = await _get_servers(db)
    try:
        content, media_type = await build_user_subscription(client, servers, fmt)
    except Exception as e:
        logger.error(f"Subscription build failed for token={token}: {e}")
        return Response(content="Error building subscription", status_code=500)

    if isinstance(content, str):
        content = content.encode("utf-8")

    return Response(
        content=content,
        media_type=media_type,
        headers={
            **_CORS_HEADERS,
            "Content-Disposition": f'attachment; filename="vpn-prime-{fmt}.txt"',
            "Profile-Title": "VPN Prime",
            "Support-URL": settings.site_url,
        },
    )


@router.options("/{token}")
@router.options("/{token}/{fmt}")
async def sub_options():
    """Handle CORS preflight."""
    return Response(status_code=204, headers=_CORS_HEADERS)


@router.get("/{token}")
async def sub_default(
    token: str,
    fmt: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Subscription in default or query-param format."""
    effective_fmt = fmt or settings.sub_default_format or "json"
    return await _build_response(token, effective_fmt, db)


@router.get("/{token}/json")
async def sub_json(token: str, db: AsyncSession = Depends(get_db)):
    """XRay JSON config with routing rules."""
    return await _build_response(token, "json", db)


@router.get("/{token}/v2ray")
async def sub_v2ray(token: str, db: AsyncSession = Depends(get_db)):
    """Base64-encoded VLESS links (v2rayNG, NekoBox)."""
    return await _build_response(token, "v2ray", db)


@router.get("/{token}/clash")
async def sub_clash(token: str, db: AsyncSession = Depends(get_db)):
    """Clash Meta YAML config."""
    return await _build_response(token, "clash", db)
