import random
import string
import logging
from datetime import datetime, timedelta, timezone

import awg
import telegram
from config import settings
from vless_api import VlessApiClient, get_subscription_url
from models import Client, Device, AuditLog, Server, PLAN_LIMITS_STR

logger = logging.getLogger(__name__)

PLAN_LIMITS = PLAN_LIMITS_STR


async def create_client_full(db, user_id, name, plan, months, admin_user_id=None):
    from sqlalchemy import select

    limit = PLAN_LIMITS.get(plan, 3)
    expires_at = datetime.now(timezone.utc) + timedelta(days=months * 30)

    client = Client(
        user_id=user_id,
        name=name,
        plan=plan,
        awg_devices_limit=limit,
        expires_at=expires_at,
        is_active=True,
    )
    db.add(client)
    await db.flush()

    # Create VLESS user
    slug = "".join(c if c.isalnum() else "_" for c in name.lower())
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    vless_username = f"{slug}_{suffix}"

    vless = VlessApiClient(
        settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass
    )
    try:
        await vless.create_user(vless_username, int(expires_at.timestamp()))
        client.vless_username = vless_username
        client.vless_sub_url = get_subscription_url(settings.vless_api_url, vless_username)
    except Exception as e:
        logger.warning(f"VLESS API user creation failed: {e}")

    # Get first active server
    srv_result = await db.execute(select(Server).where(Server.is_active == True).limit(1))
    server = srv_result.scalar_one_or_none()

    if server:
        server_params = awg.get_server_params(server.awg_config_path)
        subnet_base = ".".join(server.awg_subnet.split(".")[:3])  # e.g. 10.8.0
        for i in range(limit):
            try:
                private_key, public_key = awg.generate_keypair()
                psk = awg.generate_preshared_key()
                ip = await awg.get_next_available_ip(db, subnet=subnet_base)
                awg.add_peer(public_key, psk, ip, server.awg_interface)
                device = Device(
                    client_id=client.id,
                    server_id=server.id,
                    device_name=f"Устройство {i + 1}",
                    public_key=public_key,
                    private_key=private_key,
                    preshared_key=psk,
                    ip_address=ip,
                    is_active=True,
                )
                db.add(device)
            except Exception as e:
                logger.warning(f"Failed to create AWG device {i+1}: {e}")

    log = AuditLog(
        user_id=admin_user_id,
        action="create_client",
        target_type="client",
        target_id=client.id,
        details=f'{{"name":"{name}","plan":"{plan}","months":{months}}}',
    )
    db.add(log)
    await db.commit()

    await telegram.notify_admin(f"✅ Новый клиент: {name} ({plan}, {months} мес.)")
    return client


async def add_device_to_client(db, client: Client, device_name: str = "Новое устройство"):
    from sqlalchemy import select

    if len(client.devices) >= client.awg_devices_limit:
        raise ValueError("Достигнут лимит устройств для данного тарифа")

    srv_result = await db.execute(select(Server).where(Server.is_active == True).limit(1))
    server = srv_result.scalar_one_or_none()

    if not server:
        raise ValueError("Нет активных серверов")

    server_params = awg.get_server_params(server.awg_config_path)
    subnet_base = ".".join(server.awg_subnet.split(".")[:3])

    private_key, public_key = awg.generate_keypair()
    psk = awg.generate_preshared_key()
    ip = await awg.get_next_available_ip(db, subnet=subnet_base)
    awg.add_peer(public_key, psk, ip, server.awg_interface)

    device = Device(
        client_id=client.id,
        server_id=server.id,
        device_name=device_name,
        public_key=public_key,
        private_key=private_key,
        preshared_key=psk,
        ip_address=ip,
        is_active=True,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device


async def remove_device(db, device: Device):
    from config import settings as cfg
    awg.remove_peer(device.public_key, cfg.awg_interface)
    await db.delete(device)
    await db.commit()


async def build_device_config(db, device: Device) -> str:
    from sqlalchemy import select
    from config import settings as cfg

    server = device.server
    if not server:
        # Fall back to config defaults
        return awg.generate_client_config(
            private_key=device.private_key,
            client_ip=device.ip_address,
            server_public_key=server.awg_public_key if server else "",
            server_endpoint=cfg.awg_server_endpoint,
            server_port=cfg.awg_server_port,
            preshared_key=device.preshared_key,
            dns=cfg.awg_dns,
        )

    server_params = awg.get_server_params(server.awg_config_path)
    return awg.generate_client_config(
        private_key=device.private_key,
        client_ip=device.ip_address,
        server_public_key=server.awg_public_key or "",
        server_endpoint=server.awg_endpoint,
        server_port=server.awg_port,
        preshared_key=device.preshared_key,
        jc=server_params.get("jc", 4),
        jmin=server_params.get("jmin", 40),
        jmax=server_params.get("jmax", 70),
        s1=server_params.get("s1", 0),
        s2=server_params.get("s2", 0),
        h1=server_params.get("h1", 1),
        h2=server_params.get("h2", 2),
        h3=server_params.get("h3", 3),
        h4=server_params.get("h4", 4),
        dns=cfg.awg_dns,
    )


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
    elif b < 1024**2:
        return f"{b/1024:.1f} KB"
    elif b < 1024**3:
        return f"{b/1024**2:.1f} MB"
    else:
        return f"{b/1024**3:.2f} GB"
