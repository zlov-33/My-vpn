from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from auth import require_admin
from models import Server, AuditLog
from crypto import encrypt

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

router = APIRouter(prefix="/admin/servers", tags=["admin-servers"])


def flash(request: Request, message: str, category: str = "info"):
    msgs = request.session.get("flash_messages", [])
    msgs.append({"message": message, "category": category})
    request.session["flash_messages"] = msgs


@router.get("", response_class=HTMLResponse)
async def list_servers(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)

    result = await db.execute(select(Server).order_by(Server.priority, Server.created_at))
    servers = result.scalars().all()

    return templates.TemplateResponse(
        "admin/servers.html",
        {
            "request": request,
            "user": user,
            "servers": servers,
        },
    )


@router.post("")
async def add_server(
    request: Request,
    name: str = Form(...),
    ip: str = Form(...),
    location: str = Form(""),
    api_url: str = Form(...),
    api_user: str = Form("admin"),
    api_pass: str = Form(""),
    reality_sni: str = Form(""),
    priority: int = Form(0),
    notes: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    server = Server(
        name=name,
        ip=ip,
        location=location,
        api_url=api_url.rstrip("/"),
        api_user=api_user,
        api_pass_encrypted=encrypt(api_pass) if api_pass else "",
        reality_sni=reality_sni,
        priority=priority,
        notes=notes,
        is_active=True,
    )
    db.add(server)

    log = AuditLog(
        user_id=admin.id,
        action="add_server",
        target_type="server",
        details=f'{{"name":"{name}","ip":"{ip}","api_url":"{api_url}"}}',
    )
    db.add(log)
    await db.commit()

    flash(request, f"Сервер {name} добавлен", "success")
    return RedirectResponse("/admin/servers", status_code=303)


@router.post("/{server_id}/edit")
async def edit_server(
    server_id: int,
    request: Request,
    name: str = Form(None),
    ip: str = Form(None),
    location: str = Form(None),
    api_url: str = Form(None),
    api_user: str = Form(None),
    api_pass: str = Form(None),
    reality_sni: str = Form(None),
    priority: int = Form(None),
    notes: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        flash(request, "Сервер не найден", "danger")
        return RedirectResponse("/admin/servers", status_code=303)

    if name:
        server.name = name
    if ip:
        server.ip = ip
    if location is not None:
        server.location = location
    if api_url:
        server.api_url = api_url.rstrip("/")
    if api_user:
        server.api_user = api_user
    if api_pass:
        server.api_pass_encrypted = encrypt(api_pass)
    if reality_sni is not None:
        server.reality_sni = reality_sni
    if priority is not None:
        server.priority = priority
    if notes is not None:
        server.notes = notes

    log = AuditLog(
        user_id=admin.id,
        action="edit_server",
        target_type="server",
        target_id=server_id,
        details=f'{{"name":"{server.name}"}}',
    )
    db.add(log)
    await db.commit()

    flash(request, f"Сервер {server.name} обновлён", "success")
    return RedirectResponse("/admin/servers", status_code=303)


@router.post("/{server_id}/delete")
async def delete_server(
    server_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        flash(request, "Сервер не найден", "danger")
        return RedirectResponse("/admin/servers", status_code=303)

    log = AuditLog(
        user_id=admin.id,
        action="delete_server",
        target_type="server",
        target_id=server_id,
        details=f'{{"name":"{server.name}"}}',
    )
    db.add(log)
    await db.delete(server)
    await db.commit()

    flash(request, f"Сервер {server.name} удалён", "success")
    return RedirectResponse("/admin/servers", status_code=303)


@router.post("/{server_id}/toggle")
async def toggle_server(
    server_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        flash(request, "Сервер не найден", "danger")
        return RedirectResponse("/admin/servers", status_code=303)

    server.is_active = not server.is_active
    await db.commit()

    status_str = "активирован" if server.is_active else "деактивирован"
    flash(request, f"Сервер {server.name} {status_str}", "info")
    return RedirectResponse("/admin/servers", status_code=303)


@router.post("/{server_id}/check")
async def check_server(
    server_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Ping server API and show result."""
    admin = await require_admin(request, db)

    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        flash(request, "Сервер не найден", "danger")
        return RedirectResponse("/admin/servers", status_code=303)

    from vless_api import VlessApiClient
    from crypto import decrypt as dec
    api_pass = dec(server.api_pass_encrypted or "")
    vless = VlessApiClient(server.api_url, server.api_user, api_pass)
    try:
        info = await vless.get_system_info()
        flash(request, f"Сервер {server.name} доступен. Инфо: {info}", "success")
    except Exception as e:
        flash(request, f"Сервер {server.name} недоступен: {e}", "danger")

    return RedirectResponse("/admin/servers", status_code=303)
