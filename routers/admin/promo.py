import os
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from auth import require_admin
from models import Promo, AuditLog

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates"))

router = APIRouter(prefix="/admin/promo", tags=["admin-promo"])


def flash(request: Request, message: str, category: str = "info"):
    msgs = request.session.get("flash_messages", [])
    msgs.append({"message": message, "category": category})
    request.session["flash_messages"] = msgs


@router.get("", response_class=HTMLResponse)
async def list_promos(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)

    result = await db.execute(select(Promo).order_by(Promo.created_at.desc()))
    promos = result.scalars().all()

    return templates.TemplateResponse(
        "admin/promo.html",
        {
            "request": request,
            "user": user,
            "promos": promos,
        },
    )


@router.post("")
async def create_promo(
    request: Request,
    code: str = Form(...),
    discount_percent: int = Form(0),
    extra_days: int = Form(0),
    max_uses: int = Form(1),
    expires_at: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    # Check uniqueness
    existing = await db.execute(select(Promo).where(Promo.code == code.upper().strip()))
    if existing.scalar_one_or_none():
        flash(request, "Промокод с таким названием уже существует", "danger")
        return RedirectResponse("/admin/promo", status_code=303)

    expires = None
    if expires_at:
        try:
            expires = datetime.fromisoformat(expires_at).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    promo = Promo(
        code=code.upper().strip(),
        discount_percent=discount_percent,
        extra_days=extra_days,
        max_uses=max_uses,
        expires_at=expires,
        is_active=True,
    )
    db.add(promo)

    log = AuditLog(
        user_id=admin.id,
        action="create_promo",
        target_type="promo",
        details=f'{{"code":"{code}","extra_days":{extra_days},"discount":{discount_percent}}}',
    )
    db.add(log)
    await db.commit()

    flash(request, f"Промокод {code.upper()} создан", "success")
    return RedirectResponse("/admin/promo", status_code=303)


@router.post("/{promo_id}/delete")
async def delete_promo(
    promo_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    result = await db.execute(select(Promo).where(Promo.id == promo_id))
    promo = result.scalar_one_or_none()
    if not promo:
        flash(request, "Промокод не найден", "danger")
        return RedirectResponse("/admin/promo", status_code=303)

    log = AuditLog(
        user_id=admin.id,
        action="delete_promo",
        target_type="promo",
        target_id=promo_id,
        details=f'{{"code":"{promo.code}"}}',
    )
    db.add(log)
    await db.delete(promo)
    await db.commit()

    flash(request, f"Промокод {promo.code} удалён", "success")
    return RedirectResponse("/admin/promo", status_code=303)


@router.post("/{promo_id}/toggle")
async def toggle_promo(
    promo_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    result = await db.execute(select(Promo).where(Promo.id == promo_id))
    promo = result.scalar_one_or_none()
    if not promo:
        flash(request, "Промокод не найден", "danger")
        return RedirectResponse("/admin/promo", status_code=303)

    promo.is_active = not promo.is_active
    await db.commit()

    status = "активирован" if promo.is_active else "деактивирован"
    flash(request, f"Промокод {promo.code} {status}", "info")
    return RedirectResponse("/admin/promo", status_code=303)
