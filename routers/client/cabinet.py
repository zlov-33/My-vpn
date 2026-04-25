from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from auth import require_user
from models import Client, AuditLog, Promo
from service import generate_qr_bytes, regenerate_sub_token

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

router = APIRouter(prefix="/cabinet", tags=["client"])


def flash(request: Request, message: str, category: str = "info"):
    msgs = request.session.get("flash_messages", [])
    msgs.append({"message": message, "category": category})
    request.session["flash_messages"] = msgs


@router.get("", response_class=HTMLResponse)
async def cabinet(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)

    result = await db.execute(
        select(Client).where(Client.user_id == user.id).order_by(Client.created_at.desc())
    )
    clients = result.scalars().all()

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
            days_left = (exp_tz - datetime.now(timezone.utc)).days
        clients_data.append(
            {
                "client": client,
                "expires_at": expires_at,
                "days_left": days_left,
            }
        )

    return templates.TemplateResponse(
        "client/cabinet.html",
        {
            "request": request,
            "user": user,
            "clients_data": clients_data,
        },
    )


@router.get("/sub/qr", response_class=Response)
async def sub_qr(
    request: Request,
    client_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Return QR-code PNG for the client's subscription URL."""
    user = await require_user(request, db)

    result = await db.execute(
        select(Client).where(Client.id == client_id, Client.user_id == user.id)
    )
    client = result.scalar_one_or_none()

    if not client or not client.sub_url:
        return Response(content="Not found", status_code=404)

    qr_bytes = generate_qr_bytes(client.sub_url)
    return Response(content=qr_bytes, media_type="image/png")


@router.post("/sub/regenerate")
async def regenerate_sub(
    request: Request,
    client_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Regenerate subscription token (invalidates old URL)."""
    user = await require_user(request, db)

    result = await db.execute(
        select(Client).where(Client.id == client_id, Client.user_id == user.id)
    )
    client = result.scalar_one_or_none()
    if not client:
        flash(request, "Клиент не найден", "danger")
        return RedirectResponse("/cabinet", status_code=303)

    await regenerate_sub_token(db, client)

    log = AuditLog(
        user_id=user.id,
        action="regenerate_sub_token",
        target_type="client",
        target_id=client_id,
    )
    db.add(log)
    await db.commit()

    flash(request, "Ссылка на подписку обновлена", "success")
    return RedirectResponse("/cabinet", status_code=303)


@router.post("/link-telegram")
async def link_telegram(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)

    code = str(secrets.randbelow(900000) + 100000)
    user.telegram_link_code = code
    await db.commit()

    flash(
        request,
        f"Отправьте боту команду: /link {code} — для привязки Telegram аккаунта",
        "info",
    )
    return RedirectResponse("/cabinet", status_code=303)


@router.post("/promo")
async def apply_promo(
    request: Request,
    promo_code: str = Form(...),
    client_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    c_result = await db.execute(
        select(Client).where(Client.id == client_id, Client.user_id == user.id)
    )
    client = c_result.scalar_one_or_none()
    if not client:
        flash(request, "Клиент не найден", "danger")
        return RedirectResponse("/cabinet", status_code=303)

    p_result = await db.execute(
        select(Promo).where(
            Promo.code == promo_code.upper().strip(), Promo.is_active == True
        )
    )
    promo = p_result.scalar_one_or_none()

    if not promo:
        flash(request, "Промокод не найден или неактивен", "danger")
        return RedirectResponse("/cabinet", status_code=303)

    now = datetime.now(timezone.utc)
    if promo.expires_at:
        exp = (
            promo.expires_at.replace(tzinfo=timezone.utc)
            if promo.expires_at.tzinfo is None
            else promo.expires_at
        )
        if exp < now:
            flash(request, "Промокод истёк", "danger")
            return RedirectResponse("/cabinet", status_code=303)

    if promo.used_count >= promo.max_uses:
        flash(request, "Промокод исчерпан", "danger")
        return RedirectResponse("/cabinet", status_code=303)

    if promo.extra_days > 0:
        from datetime import timedelta
        if client.expires_at:
            base = (
                client.expires_at.replace(tzinfo=timezone.utc)
                if client.expires_at.tzinfo is None
                else client.expires_at
            )
            client.expires_at = base + timedelta(days=promo.extra_days)
        else:
            client.expires_at = now + timedelta(days=promo.extra_days)

    promo.used_count += 1
    if promo.used_count >= promo.max_uses:
        promo.is_active = False

    log = AuditLog(
        user_id=user.id,
        action="apply_promo",
        target_type="client",
        target_id=client.id,
        details=f'{{"promo":"{promo_code}","extra_days":{promo.extra_days}}}',
    )
    db.add(log)
    await db.commit()

    flash(request, f"Промокод применён! +{promo.extra_days} дней к подписке", "success")
    return RedirectResponse("/cabinet", status_code=303)
