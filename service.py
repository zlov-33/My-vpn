from __future__ import annotations

import random
import secrets
import string
import logging
from datetime import datetime, timedelta, timezone

import telegram
from config import settings
from vless_api import VlessApiClient
from models import Client, AuditLog, Server, PLAN_TRAFFIC_GB

logger = logging.getLogger(__name__)


def _make_vless_username(name: str) -> str:
    slug = "".join(c if c.isalnum() else "_" for c in name.lower())
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{slug}_{suffix}"


def _get_vless_client(server: Server | None = None) -> VlessApiClient:
    """Return VlessApiClient for a server or fall back to settings defaults."""
    if server:
        from crypto import decrypt
        api_pass = decrypt(server.api_pass_encrypted or "")
        return VlessApiClient(server.api_url, server.api_user, api_pass)
    return VlessApiClient(
        settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass
    )


async def _get_active_servers(db) -> list[Server]:
    from sqlalchemy import select
    result = await db.execute(
        select(Server).where(Server.is_active == True).order_by(Server.priority)
    )
    return result.scalars().all()


async def create_client_full(
    db,
    user_id: int | None,
    name: str,
    plan: str,
    months: int,
    admin_user_id: int | None = None,
) -> Client:
    traffic_gb = PLAN_TRAFFIC_GB.get(plan, 500)
    expires_at = datetime.now(timezone.utc) + timedelta(days=months * 30)

    vless_username = _make_vless_username(name)
    sub_token = secrets.token_urlsafe(12)

    client = Client(
        user_id=user_id,
        name=name,
        plan=plan,
        traffic_limit_gb=traffic_gb,
        traffic_used_bytes=0,
        vless_username=vless_username,
        vless_sub_token=sub_token,
        expires_at=expires_at,
        is_active=True,
    )
    db.add(client)
    await db.flush()  # get client.id

    # Create VLESS user on all active servers
    servers = await _get_active_servers(db)
    if servers:
        for server in servers:
            vless = _get_vless_client(server)
            try:
                await vless.create_user(
                    vless_username,
                    int(expires_at.timestamp()),
                    data_limit_gb=traffic_gb,
                )
                logger.info(f"VLESS user {vless_username} created on server {server.name}")
            except Exception as e:
                logger.warning(f"VLESS user creation failed on server {server.name}: {e}")
    else:
        # Fallback: single node from settings
        vless = _get_vless_client()
        try:
            await vless.create_user(
                vless_username,
                int(expires_at.timestamp()),
                data_limit_gb=traffic_gb,
            )
            logger.info(f"VLESS user {vless_username} created (fallback node)")
        except Exception as e:
            logger.warning(f"VLESS API fallback user creation failed: {e}")

    log = AuditLog(
        user_id=admin_user_id,
        action="create_client",
        target_type="client",
        target_id=client.id,
        details=f'{{"name":"{name}","plan":"{plan}","months":{months}}}',
    )
    db.add(log)
    await db.commit()

    await telegram.notify_admin(
        f"✅ Новый клиент: {name} (тариф {plan}, {months} мес., трафик {traffic_gb or '∞'} ГБ)"
    )
    return client


async def deactivate_client(db, client: Client, admin_user_id: int | None = None):
    """Disable VLESS user on all servers and mark client inactive."""
    servers = await _get_active_servers(db)
    if client.vless_username:
        targets = servers if servers else [None]
        for server in targets:
            vless = _get_vless_client(server)
            try:
                await vless.disable_user(client.vless_username)
            except Exception as e:
                logger.warning(f"Failed to disable VLESS user on {getattr(server, 'name', 'fallback')}: {e}")

    client.is_active = False

    log = AuditLog(
        user_id=admin_user_id,
        action="deactivate_client",
        target_type="client",
        target_id=client.id,
    )
    db.add(log)
    await db.commit()
    await telegram.notify_admin(f"🔴 Клиент {client.name} деактивирован.")


async def activate_client(db, client: Client, admin_user_id: int | None = None):
    """Enable VLESS user on all servers and mark client active."""
    servers = await _get_active_servers(db)
    if client.vless_username:
        targets = servers if servers else [None]
        for server in targets:
            vless = _get_vless_client(server)
            try:
                await vless.enable_user(client.vless_username)
            except Exception as e:
                logger.warning(f"Failed to enable VLESS user on {getattr(server, 'name', 'fallback')}: {e}")

    client.is_active = True

    log = AuditLog(
        user_id=admin_user_id,
        action="activate_client",
        target_type="client",
        target_id=client.id,
    )
    db.add(log)
    await db.commit()
    await telegram.notify_admin(f"▶️ Клиент {client.name} активирован.")


async def extend_client(
    db,
    client: Client,
    months: int,
    admin_user_id: int | None = None,
    reset_traffic: bool = False,
):
    """Extend subscription and optionally reset traffic counter."""
    now = datetime.now(timezone.utc)
    if client.expires_at:
        base = client.expires_at.replace(tzinfo=timezone.utc) if client.expires_at.tzinfo is None else client.expires_at
        base = max(base, now)
    else:
        base = now
    client.expires_at = base + timedelta(days=months * 30)
    client.is_active = True

    if reset_traffic:
        client.traffic_used_bytes = 0

    servers = await _get_active_servers(db)
    targets = servers if servers else [None]

    for server in targets:
        vless = _get_vless_client(server)
        if client.vless_username:
            try:
                await vless.update_user_expire(
                    client.vless_username, int(client.expires_at.timestamp())
                )
                await vless.enable_user(client.vless_username)
                if reset_traffic:
                    await vless.reset_user_traffic(client.vless_username)
            except Exception as e:
                logger.warning(f"Failed to extend VLESS user on {getattr(server, 'name', 'fallback')}: {e}")

    log = AuditLog(
        user_id=admin_user_id,
        action="extend_client",
        target_type="client",
        target_id=client.id,
        details=f'{{"months":{months},"reset_traffic":{str(reset_traffic).lower()}}}',
    )
    db.add(log)
    await db.commit()


async def reset_client_traffic(db, client: Client, admin_user_id: int | None = None):
    """Reset traffic counter in DB and on all VLESS servers."""
    client.traffic_used_bytes = 0

    servers = await _get_active_servers(db)
    targets = servers if servers else [None]
    if client.vless_username:
        for server in targets:
            vless = _get_vless_client(server)
            try:
                await vless.reset_user_traffic(client.vless_username)
            except Exception as e:
                logger.warning(f"Failed to reset traffic on {getattr(server, 'name', 'fallback')}: {e}")

    log = AuditLog(
        user_id=admin_user_id,
        action="reset_traffic",
        target_type="client",
        target_id=client.id,
    )
    db.add(log)
    await db.commit()


async def change_client_plan(
    db,
    client: Client,
    new_plan: str,
    admin_user_id: int | None = None,
):
    """Change plan and update traffic limit on all servers."""
    client.plan = new_plan
    new_limit_gb = PLAN_TRAFFIC_GB.get(new_plan, 500)
    client.traffic_limit_gb = new_limit_gb

    servers = await _get_active_servers(db)
    targets = servers if servers else [None]
    if client.vless_username:
        for server in targets:
            vless = _get_vless_client(server)
            try:
                await vless.update_user_data_limit(client.vless_username, new_limit_gb)
            except Exception as e:
                logger.warning(f"Failed to update data limit on {getattr(server, 'name', 'fallback')}: {e}")

    log = AuditLog(
        user_id=admin_user_id,
        action="change_plan",
        target_type="client",
        target_id=client.id,
        details=f'{{"new_plan":"{new_plan}","traffic_gb":{new_limit_gb}}}',
    )
    db.add(log)
    await db.commit()


async def regenerate_sub_token(db, client: Client) -> str:
    """Generate a new subscription token (invalidates old URL)."""
    token = secrets.token_urlsafe(12)
    client.vless_sub_token = token
    await db.commit()
    return token


def generate_qr_bytes(data: str) -> bytes:
    import qrcode
    import io
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    elif b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    else:
        return f"{b / 1024 ** 3:.2f} GB"
