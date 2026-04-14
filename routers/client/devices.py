import os
from fastapi import APIRouter, Request, Depends
from fastapi.responses import Response, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from auth import require_user
from models import Device, Client
from service import build_device_config, generate_qr_bytes

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates"))

router = APIRouter(prefix="/cabinet/devices", tags=["client-devices"])


@router.get("/{device_id}/config")
async def download_config(
    device_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    # Verify ownership
    dev_result = await db.execute(select(Device).where(Device.id == device_id))
    device = dev_result.scalar_one_or_none()
    if not device:
        return Response(content="Device not found", status_code=404)

    client_result = await db.execute(
        select(Client).where(Client.id == device.client_id, Client.user_id == user.id)
    )
    client = client_result.scalar_one_or_none()
    if not client:
        return Response(content="Access denied", status_code=403)

    # Load server relationship
    if device.server_id:
        from models import Server
        srv_result = await db.execute(select(Server).where(Server.id == device.server_id))  # noqa
        device.server = srv_result.scalar_one_or_none()

    config_text = await build_device_config(db, device)
    filename = f"{device.device_name.replace(' ', '_')}.conf"
    return Response(
        content=config_text,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{device_id}/qr", response_class=Response)
async def device_qr(
    device_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    dev_result = await db.execute(select(Device).where(Device.id == device_id))
    device = dev_result.scalar_one_or_none()
    if not device:
        return Response(content="Device not found", status_code=404)

    client_result = await db.execute(
        select(Client).where(Client.id == device.client_id, Client.user_id == user.id)
    )
    client = client_result.scalar_one_or_none()
    if not client:
        return Response(content="Access denied", status_code=403)

    if device.server_id:
        from models import Server
        srv_result = await db.execute(select(Server).where(Server.id == device.server_id))
        device.server = srv_result.scalar_one_or_none()

    config_text = await build_device_config(db, device)
    qr_bytes = generate_qr_bytes(config_text)
    return Response(content=qr_bytes, media_type="image/png")
