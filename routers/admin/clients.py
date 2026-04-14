import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from auth import require_admin
from models import Client, Device, User, Payment, AuditLog, Server
from service import create_client_full, add_device_to_client, remove_device
import awg
import telegram
from config import settings
from vless_api import VlessApiClient

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates"))

router = APIRouter(prefix="/admin/clients", tags=["admin-clients"])


def flash(request: Request, message: str, category: str = "info"):
    msgs = request.session.get("flash_messages", [])
    msgs.append({"message": message, "category": category})
    request.session["flash_messages"] = msgs


@router.get("", response_class=HTMLResponse)
async def list_clients(
    request: Request,
    search: str = None,
    plan: str = None,
    status: str = None,
    db: AsyncSession = Depends(get_db),
):
    user = await require_admin(request, db)

    query = select(Client).order_by(Client.created_at.desc())
    result = await db.execute(query)
    clients = result.scalars().all()

    # Apply filters in Python (simple enough for small datasets)
    if search:
        s = search.lower()
        clients = [c for c in clients if s in c.name.lower() or (c.vless_username and s in c.vless_username.lower())]
    if plan:
        clients = [c for c in clients if c.plan == plan]
    if status == "active":
        clients = [c for c in clients if c.is_active]
    elif status == "inactive":
        clients = [c for c in clients if not c.is_active]

    now = datetime.now(timezone.utc)
    clients_data = []
    for client in clients:
        dev_result = await db.execute(select(Device).where(Device.client_id == client.id))
        devices = dev_result.scalars().all()
        expires_at = client.expires_at
        days_left = None
        if expires_at:
            exp_tz = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
            days_left = (exp_tz - now).days
        clients_data.append({
            "client": client,
            "devices": devices,
            "days_left": days_left,
        })

    return templates.TemplateResponse(
        "admin/clients.html",
        {
            "request": request,
            "user": user,
            "clients_data": clients_data,
            "search": search or "",
            "filter_plan": plan or "",
            "filter_status": status or "",
        },
    )


@router.post("")
async def create_client(
    request: Request,
    name: str = Form(...),
    plan: str = Form("standard"),
    months: int = Form(1),
    user_email: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    user_id = None
    if user_email:
        u_result = await db.execute(select(User).where(User.email == user_email.lower().strip()))
        linked_user = u_result.scalar_one_or_none()
        if linked_user:
            user_id = linked_user.id

    try:
        client = await create_client_full(db, user_id, name, plan, months, admin_user_id=admin.id)
        flash(request, f"Клиент {name} создан успешно", "success")
        return RedirectResponse(f"/admin/clients/{client.id}", status_code=303)
    except Exception as e:
        flash(request, f"Ошибка создания клиента: {e}", "danger")
        return RedirectResponse("/admin/clients", status_code=303)


@router.get("/{client_id}", response_class=HTMLResponse)
async def client_detail(
    client_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_admin(request, db)

    c_result = await db.execute(select(Client).where(Client.id == client_id))
    client = c_result.scalar_one_or_none()
    if not client:
        flash(request, "Клиент не найден", "danger")
        return RedirectResponse("/admin/clients", status_code=303)

    dev_result = await db.execute(select(Device).where(Device.client_id == client_id))
    devices = dev_result.scalars().all()

    pay_result = await db.execute(
        select(Payment).where(Payment.client_id == client_id).order_by(Payment.created_at.desc())
    )
    payments = pay_result.scalars().all()

    linked_user = None
    if client.user_id:
        u_result = await db.execute(select(User).where(User.id == client.user_id))
        linked_user = u_result.scalar_one_or_none()

    # VLESS stats
    marz_stats = None
    if client.vless_username:
        try:
            vless = VlessApiClient(settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass)
            marz_stats = await vless.get_user_stats(client.vless_username)
        except Exception:
            pass

    expires_at = client.expires_at
    days_left = None
    if expires_at:
        exp_tz = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
        days_left = (exp_tz - datetime.now(timezone.utc)).days

    return templates.TemplateResponse(
        "admin/client.html",
        {
            "request": request,
            "user": user,
            "client": client,
            "devices": devices,
            "payments": payments,
            "linked_user": linked_user,
            "marz_stats": marz_stats,
            "days_left": days_left,
        },
    )


@router.post("/{client_id}")
async def update_client(
    client_id: int,
    request: Request,
    name: str = Form(None),
    plan: str = Form(None),
    notes: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    c_result = await db.execute(select(Client).where(Client.id == client_id))
    client = c_result.scalar_one_or_none()
    if not client:
        flash(request, "Клиент не найден", "danger")
        return RedirectResponse("/admin/clients", status_code=303)

    if name:
        client.name = name
    if plan and plan in ("lite", "standard", "family"):
        from models import PLAN_LIMITS_STR
        client.plan = plan
        client.awg_devices_limit = PLAN_LIMITS_STR[plan]
    if notes is not None:
        client.notes = notes

    log = AuditLog(
        user_id=admin.id,
        action="update_client",
        target_type="client",
        target_id=client_id,
    )
    db.add(log)
    await db.commit()

    flash(request, "Клиент обновлён", "success")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/{client_id}/delete")
async def delete_client(
    client_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    c_result = await db.execute(select(Client).where(Client.id == client_id))
    client = c_result.scalar_one_or_none()
    if not client:
        flash(request, "Клиент не найден", "danger")
        return RedirectResponse("/admin/clients", status_code=303)

    # Remove all AWG peers
    dev_result = await db.execute(select(Device).where(Device.client_id == client_id))
    devices = dev_result.scalars().all()
    for device in devices:
        awg.remove_peer(device.public_key, settings.awg_interface)

    # Delete VLESS user
    if client.vless_username:
        try:
            vless = VlessApiClient(settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass)
            await vless.delete_user(client.vless_username)
        except Exception:
            pass

    log = AuditLog(
        user_id=admin.id,
        action="delete_client",
        target_type="client",
        target_id=client_id,
        details=f'{{"name":"{client.name}"}}',
    )
    db.add(log)
    await db.delete(client)
    await db.commit()

    flash(request, f"Клиент {client.name} удалён", "success")
    return RedirectResponse("/admin/clients", status_code=303)


@router.post("/{client_id}/devices")
async def add_device(
    client_id: int,
    request: Request,
    device_name: str = Form("Новое устройство"),
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    c_result = await db.execute(select(Client).where(Client.id == client_id))
    client = c_result.scalar_one_or_none()
    if not client:
        flash(request, "Клиент не найден", "danger")
        return RedirectResponse("/admin/clients", status_code=303)

    # Refresh devices count
    dev_result = await db.execute(select(Device).where(Device.client_id == client_id))
    client.devices = dev_result.scalars().all()

    try:
        device = await add_device_to_client(db, client, device_name)
        flash(request, f"Устройство '{device_name}' добавлено", "success")
    except ValueError as e:
        flash(request, str(e), "danger")

    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/{client_id}/devices/{device_id}/delete")
async def delete_device(
    client_id: int,
    device_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    dev_result = await db.execute(
        select(Device).where(Device.id == device_id, Device.client_id == client_id)
    )
    device = dev_result.scalar_one_or_none()
    if not device:
        flash(request, "Устройство не найдено", "danger")
        return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)

    await remove_device(db, device)

    log = AuditLog(
        user_id=admin.id,
        action="delete_device",
        target_type="device",
        target_id=device_id,
    )
    db.add(log)
    await db.commit()

    flash(request, "Устройство удалено", "success")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/{client_id}/extend")
async def extend_subscription(
    client_id: int,
    request: Request,
    months: int = Form(1),
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    c_result = await db.execute(select(Client).where(Client.id == client_id))
    client = c_result.scalar_one_or_none()
    if not client:
        flash(request, "Клиент не найден", "danger")
        return RedirectResponse("/admin/clients", status_code=303)

    now = datetime.now(timezone.utc)
    if client.expires_at:
        exp = client.expires_at.replace(tzinfo=timezone.utc) if client.expires_at.tzinfo is None else client.expires_at
        base = max(exp, now)
    else:
        base = now

    client.expires_at = base + timedelta(days=months * 30)
    client.is_active = True

    # Update VLESS expire
    if client.vless_username:
        try:
            vless = VlessApiClient(settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass)
            await vless.update_user_expire(client.vless_username, int(client.expires_at.timestamp()))
            await vless.enable_user(client.vless_username)
        except Exception:
            pass

    # Re-add AWG peers if suspended
    dev_result = await db.execute(select(Device).where(Device.client_id == client_id))
    devices = dev_result.scalars().all()
    for device in devices:
        awg.add_peer(device.public_key, device.preshared_key, device.ip_address, settings.awg_interface)

    log = AuditLog(
        user_id=admin.id,
        action="extend_subscription",
        target_type="client",
        target_id=client_id,
        details=f'{{"months":{months}}}',
    )
    db.add(log)

    # Record manual payment
    pay = Payment(
        client_id=client_id,
        amount=0,
        method="manual",
        status="success",
        months=months,
        notes=f"Ручное продление на {months} мес. администратором",
        paid_at=now,
    )
    db.add(pay)
    await db.commit()

    flash(request, f"Подписка продлена на {months} мес.", "success")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/{client_id}/suspend")
async def suspend_client(
    client_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    c_result = await db.execute(select(Client).where(Client.id == client_id))
    client = c_result.scalar_one_or_none()
    if not client:
        flash(request, "Клиент не найден", "danger")
        return RedirectResponse("/admin/clients", status_code=303)

    # Remove AWG peers
    dev_result = await db.execute(select(Device).where(Device.client_id == client_id))
    devices = dev_result.scalars().all()
    for device in devices:
        awg.remove_peer(device.public_key, settings.awg_interface)

    # Disable VLESS
    if client.vless_username:
        try:
            vless = VlessApiClient(settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass)
            await vless.disable_user(client.vless_username)
        except Exception:
            pass

    client.is_active = False

    log = AuditLog(
        user_id=admin.id,
        action="suspend_client",
        target_type="client",
        target_id=client_id,
    )
    db.add(log)
    await db.commit()

    await telegram.notify_admin(f"⏸ Клиент {client.name} приостановлен администратором.")
    flash(request, f"Клиент {client.name} приостановлен", "warning")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/{client_id}/resume")
async def resume_client(
    client_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    c_result = await db.execute(select(Client).where(Client.id == client_id))
    client = c_result.scalar_one_or_none()
    if not client:
        flash(request, "Клиент не найден", "danger")
        return RedirectResponse("/admin/clients", status_code=303)

    client.is_active = True

    # Re-add AWG peers
    dev_result = await db.execute(select(Device).where(Device.client_id == client_id))
    devices = dev_result.scalars().all()
    for device in devices:
        awg.add_peer(device.public_key, device.preshared_key, device.ip_address, settings.awg_interface)

    # Enable VLESS
    if client.vless_username:
        try:
            vless = VlessApiClient(settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass)
            await vless.enable_user(client.vless_username)
        except Exception:
            pass

    log = AuditLog(
        user_id=admin.id,
        action="resume_client",
        target_type="client",
        target_id=client_id,
    )
    db.add(log)
    await db.commit()

    await telegram.notify_admin(f"▶️ Клиент {client.name} восстановлен администратором.")
    flash(request, f"Клиент {client.name} восстановлен", "success")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)
