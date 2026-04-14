import os
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from auth import require_admin
from models import Server, AuditLog

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates"))

router = APIRouter(prefix="/admin/servers", tags=["admin-servers"])


def flash(request: Request, message: str, category: str = "info"):
    msgs = request.session.get("flash_messages", [])
    msgs.append({"message": message, "category": category})
    request.session["flash_messages"] = msgs


@router.get("", response_class=HTMLResponse)
async def list_servers(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)

    result = await db.execute(select(Server).order_by(Server.created_at.desc()))
    servers = result.scalars().all()

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "servers": servers,
            "view": "servers",
        },
    )


@router.post("")
async def add_server(
    request: Request,
    name: str = Form(...),
    host: str = Form(...),
    awg_interface: str = Form("awg0"),
    awg_config_path: str = Form("/etc/amnezia/amneziawg/awg0.conf"),
    awg_endpoint: str = Form(...),
    awg_port: int = Form(48336),
    awg_subnet: str = Form("10.8.0.0/24"),
    awg_public_key: str = Form(None),
    notes: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    server = Server(
        name=name,
        host=host,
        awg_interface=awg_interface,
        awg_config_path=awg_config_path,
        awg_endpoint=awg_endpoint,
        awg_port=awg_port,
        awg_subnet=awg_subnet,
        awg_public_key=awg_public_key,
        notes=notes,
        is_active=True,
    )
    db.add(server)

    log = AuditLog(
        user_id=admin.id,
        action="add_server",
        target_type="server",
        details=f'{{"name":"{name}","host":"{host}"}}',
    )
    db.add(log)
    await db.commit()

    flash(request, f"Сервер {name} добавлен", "success")
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

    status = "активирован" if server.is_active else "деактивирован"
    flash(request, f"Сервер {server.name} {status}", "info")
    return RedirectResponse("/admin/servers", status_code=303)
