from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone
from sqlalchemy import select
from database import AsyncSessionLocal
from models import Client, Device
import awg
import telegram
import logging

scheduler = AsyncIOScheduler()
logger = logging.getLogger(__name__)


async def refresh_peer_stats():
    from config import settings
    stats = awg.get_peers_stats(settings.awg_interface)
    if not stats:
        return
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Device))
        devices = result.scalars().all()
        for device in devices:
            if device.public_key in stats:
                s = stats[device.public_key]
                device.bytes_received = s["rx"]
                device.bytes_sent = s["tx"]
                if s["latest_handshake"]:
                    device.last_handshake = datetime.fromtimestamp(
                        s["latest_handshake"], tz=timezone.utc
                    )
        await db.commit()


async def check_expiring_subscriptions():
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
            if 0 < days_left <= 3:
                await telegram.notify_admin(
                    f"⚠️ Клиент {client.name} (тариф {client.plan}) — подписка истекает через {days_left} дн."
                )
                if client.user_id:
                    from models import User
                    user_result = await db.execute(select(User).where(User.id == client.user_id))
                    user = user_result.scalar_one_or_none()
                    if user and user.telegram_id:
                        await telegram.notify_user(
                            user.telegram_id,
                            f"⚠️ Ваша подписка VPN Prime истекает через {days_left} дн. "
                            f"Продлите её в личном кабинете.",
                        )


async def deactivate_expired_subscriptions():
    from config import settings
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
                dev_result = await db.execute(
                    select(Device).where(Device.client_id == client.id)
                )
                devices = dev_result.scalars().all()
                for device in devices:
                    awg.remove_peer(device.public_key, settings.awg_interface)
                if client.vless_username:
                    from vless_api import VlessApiClient
                    vless = VlessApiClient(
                        settings.vless_api_url,
                        settings.vless_api_user,
                        settings.vless_api_pass,
                    )
                    try:
                        await vless.disable_user(client.vless_username)
                    except Exception:
                        pass
                client.is_active = False
                await db.commit()
                await telegram.notify_admin(
                    f"🔴 Клиент {client.name} деактивирован (подписка истекла)."
                )


def setup_scheduler():
    scheduler.add_job(refresh_peer_stats, "interval", hours=1, id="refresh_stats")
    scheduler.add_job(check_expiring_subscriptions, "interval", hours=1, id="check_expiring")
    scheduler.add_job(deactivate_expired_subscriptions, "interval", hours=1, id="deactivate_expired")
    scheduler.start()
    logger.info("Scheduler started with 3 jobs.")
