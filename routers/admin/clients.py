from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from auth import require_admin
from models import Client, User, Payment, AuditLog, Server, PLAN_TRAFFIC_GB
from service import (
    create_client_full,
    deactivate_client,
    activate_client,
    extend_client,
    reset_client_traffic,
    change_client_plan,
)
from vless_api import VlessApiClient
import telegram

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

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

    result = await db.execute(select(Client).order_by(Client.created_at.desc()))
    clients = result.scalars().all()

    if search:
        s = search.lower()
        clients = [
            c for c in clients
            if s in c.name.lower() or (c.vless_username and s in c.vless_username.lower())
        ]
    if plan:
        clients = [c for c in clients if c.plan == plan]
    if status == "active":
        clients = [c for c in clients if c.is_active]
    elif status == "inactive":
        clients = [c for c in clients if not c.is_active]

    now = datetime.now(timezone.utc)
    clients_data = []
    for client in clients:
        expires_at = client.expires_at
        days_left = None
        if expires_at:
            exp_tz = (
                expires_at.replace(tzinfo=timezone.utc)
                if expires_at.tzinfo is None
                else expires_at
            )
            days_left = (exp_tz - now).days
        clients_data.append(
            {
                "client": client,
                "days_left": days_left,
            }
        )

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
        u_result = await db.execute(
            select(User).where(User.email == user_email.lower().strip())
        )
        linked_user = u_result.scalar_one_or_none()
        if linked_user:
            user_id = linked_user.id

    try:
        client = await create_client_full(
            db, user_id, name, plan, months, admin_user_id=admin.id
        )
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

    pay_result = await db.execute(
        select(Payment)
        .where(Payment.client_id == client_id)
        .order_by(Payment.created_at.desc())
    )
    payments = pay_result.scalars().all()

    linked_user = None
    if client.user_id:
        u_result = await db.execute(select(User).where(User.id == client.user_id))
        linked_user = u_result.scalar_one_or_none()

    # Live VLESS stats from API
    vless_stats = None
    if client.vless_username:
        try:
            vless = VlessApiClient(
                settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass
            )
            vless_stats = await vless.get_user_stats(client.vless_username)
        except Exception:
            pass

    days_left = None
    if client.expires_at:
        exp_tz = (
            client.expires_at.replace(tzinfo=timezone.utc)
            if client.expires_at.tzinfo is None
            else client.expires_at
        )
        days_left = (exp_tz - datetime.now(timezone.utc)).days

    return templates.TemplateResponse(
        "admin/client.html",
        {
            "request": request,
            "user": user,
            "client": client,
            "payments": payments,
            "linked_user": linked_user,
            "vless_stats": vless_stats,
            "days_left": days_left,
            "plan_traffic": PLAN_TRAFFIC_GB,
        },
    )


@router.post("/{client_id}")
async def update_client(
    client_id: int,
    request: Request,
    name: str = Form(None),
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

    # Delete VLESS user on all servers
    if client.vless_username:
        srv_result = await db.execute(select(Server).where(Server.is_active == True))
        servers = srv_result.scalars().all()
        targets = servers if servers else [None]
        for server in targets:
            if server:
                from crypto import decrypt
                api_pass = decrypt(server.api_pass_encrypted or "")
                vless = VlessApiClient(server.api_url, server.api_user, api_pass)
            else:
                vless = VlessApiClient(
                    settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass
                )
            try:
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


@router.post("/{client_id}/extend")
async def extend_subscription(
    client_id: int,
    request: Request,
    months: int = Form(1),
    reset_traffic: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    c_result = await db.execute(select(Client).where(Client.id == client_id))
    client = c_result.scalar_one_or_none()
    if not client:
        flash(request, "Клиент не найден", "danger")
        return RedirectResponse("/admin/clients", status_code=303)

    await extend_client(db, client, months, admin_user_id=admin.id, reset_traffic=reset_traffic)

    # Record manual payment
    pay = Payment(
        client_id=client_id,
        amount=0,
        method="manual",
        status="success",
        months=months,
        notes=f"Ручное продление на {months} мес. администратором",
        paid_at=datetime.now(timezone.utc),
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

    await deactivate_client(db, client, admin_user_id=admin.id)

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

    await activate_client(db, client, admin_user_id=admin.id)

    flash(request, f"Клиент {client.name} восстановлен", "success")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/{client_id}/reset-traffic")
async def reset_traffic(
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

    await reset_client_traffic(db, client, admin_user_id=admin.id)

    flash(request, f"Трафик клиента {client.name} сброшен", "success")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/{client_id}/change-plan")
async def change_plan(
    client_id: int,
    request: Request,
    plan: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    if plan not in PLAN_TRAFFIC_GB:
        flash(request, "Неверный тариф", "danger")
        return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)

    c_result = await db.execute(select(Client).where(Client.id == client_id))
    client = c_result.scalar_one_or_none()
    if not client:
        flash(request, "Клиент не найден", "danger")
        return RedirectResponse("/admin/clients", status_code=303)

    await change_client_plan(db, client, plan, admin_user_id=admin.id)

    flash(request, f"Тариф изменён на {plan}", "success")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)
