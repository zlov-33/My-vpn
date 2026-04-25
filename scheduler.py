from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

import telegram
from database import AsyncSessionLocal
from models import Client, Server

scheduler = AsyncIOScheduler()
logger = logging.getLogger(__name__)


async def refresh_traffic_stats():
    """
    Run hourly. Fetch all VLESS users from each server, update traffic_used_bytes.
    Disable client and notify if limit exceeded.
    """
    from vless_api import VlessApiClient
    from crypto import decrypt
    from config import settings

    async with AsyncSessionLocal() as db:
        srv_result = await db.execute(select(Server).where(Server.is_active == True))
        servers = srv_result.scalars().all()

        # Aggregate traffic across all servers keyed by vless_username
        aggregated: dict[str, int] = {}

        if servers:
            for server in servers:
                api_pass = decrypt(server.api_pass_encrypted or "")
                vless = VlessApiClient(server.api_url, server.api_user, api_pass)
                try:
                    users = await vless.get_all_users()
                    for u in users:
                        username = u.get("username", "")
                        used = u.get("used_traffic", 0) or 0
                        aggregated[username] = aggregated.get(username, 0) + used
                except Exception as e:
                    logger.warning(f"Traffic sync failed for server {server.name}: {e}")
        else:
            # Fallback single node
            vless = VlessApiClient(
                settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass
            )
            try:
                users = await vless.get_all_users()
                for u in users:
                    username = u.get("username", "")
                    used = u.get("used_traffic", 0) or 0
                    aggregated[username] = used
            except Exception as e:
                logger.warning(f"Traffic sync failed (fallback node): {e}")

        if not aggregated:
            logger.debug("No traffic data received — skipping update.")
            return

        # Update clients in DB
        result = await db.execute(select(Client).where(Client.is_active == True))
        clients = result.scalars().all()

        for client in clients:
            if not client.vless_username or client.vless_username not in aggregated:
                continue

            client.traffic_used_bytes = aggregated[client.vless_username]

            # Check limit (0 = unlimited)
            if (
                client.traffic_limit_gb > 0
                and client.traffic_used_bytes >= client.traffic_limit_bytes
            ):
                logger.info(f"Client {client.name}: traffic limit reached, disabling.")
                client.is_active = False
                # Disable on all servers
                if servers:
                    for server in servers:
                        api_pass = decrypt(server.api_pass_encrypted or "")
                        vless = VlessApiClient(server.api_url, server.api_user, api_pass)
                        try:
                            await vless.disable_user(client.vless_username)
                        except Exception as e:
                            logger.warning(f"Failed to disable {client.vless_username} on {server.name}: {e}")
                else:
                    vless = VlessApiClient(
                        settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass
                    )
                    try:
                        await vless.disable_user(client.vless_username)
                    except Exception as e:
                        logger.warning(f"Failed to disable {client.vless_username} (fallback): {e}")

                await telegram.notify_admin(
                    f"🚫 Клиент {client.name} исчерпал трафик "
                    f"({client.traffic_limit_gb} ГБ) — отключён."
                )

                # Notify user via Telegram if linked
                if client.user_id:
                    from models import User
                    u_result = await db.execute(select(User).where(User.id == client.user_id))
                    user = u_result.scalar_one_or_none()
                    if user and user.telegram_id:
                        await telegram.notify_user(
                            user.telegram_id,
                            f"🚫 Ваш трафик ({client.traffic_limit_gb} ГБ) исчерпан. "
                            f"VPN отключён. Продлите подписку в личном кабинете.",
                        )

        await db.commit()
        logger.info(f"Traffic stats updated for {len(aggregated)} VLESS users.")


async def check_expiring_subscriptions():
    """Run hourly. Notify admin + user when 3 or 1 day(s) remain."""
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Client).where(Client.is_active == True))
        clients = result.scalars().all()
        for client in clients:
            if not client.expires_at:
                continue
            expires = (
                client.expires_at.replace(tzinfo=timezone.utc)
                if client.expires_at.tzinfo is None
                else client.expires_at
            )
            days_left = (expires - now).days
            if days_left in (3, 1):
                await telegram.notify_admin(
                    f"⚠️ Клиент {client.name} (тариф {client.plan}) — "
                    f"подписка истекает через {days_left} дн."
                )
                if client.user_id:
                    from models import User
                    u_result = await db.execute(select(User).where(User.id == client.user_id))
                    user = u_result.scalar_one_or_none()
                    if user and user.telegram_id:
                        await telegram.notify_user(
                            user.telegram_id,
                            f"⚠️ Ваша подписка VPN Prime истекает через {days_left} дн. "
                            f"Продлите её в личном кабинете.",
                        )


async def deactivate_expired_subscriptions():
    """Run hourly. Deactivate clients whose subscription has expired."""
    from service import deactivate_client
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Client).where(Client.is_active == True))
        clients = result.scalars().all()
        for client in clients:
            if not client.expires_at:
                continue
            expires = (
                client.expires_at.replace(tzinfo=timezone.utc)
                if client.expires_at.tzinfo is None
                else client.expires_at
            )
            if expires < now:
                logger.info(f"Deactivating expired client: {client.name}")
                await deactivate_client(db, client)


async def check_servers_health():
    """
    Run every 5 minutes. Ping each server via /api/system.
    Mark inactive if unreachable and notify admin.
    """
    from vless_api import VlessApiClient
    from crypto import decrypt

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Server))
        servers = result.scalars().all()

        for server in servers:
            api_pass = decrypt(server.api_pass_encrypted or "")
            vless = VlessApiClient(server.api_url, server.api_user, api_pass)
            try:
                await vless.get_system_info()
                if not server.is_active:
                    server.is_active = True
                    await telegram.notify_admin(
                        f"✅ Сервер {server.name} снова доступен."
                    )
                    logger.info(f"Server {server.name} is back online.")
            except Exception as e:
                if server.is_active:
                    server.is_active = False
                    await telegram.notify_admin(
                        f"🔴 Сервер {server.name} недоступен: {e}"
                    )
                    logger.warning(f"Server {server.name} is down: {e}")

        await db.commit()


def setup_scheduler():
    scheduler.add_job(refresh_traffic_stats, "interval", hours=1, id="refresh_traffic")
    scheduler.add_job(check_expiring_subscriptions, "interval", hours=1, id="check_expiring")
    scheduler.add_job(deactivate_expired_subscriptions, "interval", hours=1, id="deactivate_expired")
    scheduler.add_job(check_servers_health, "interval", minutes=5, id="check_servers")
    scheduler.start()
    logger.info("Scheduler started with 4 jobs.")
