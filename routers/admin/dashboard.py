from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_admin
from database import get_db
from models import AuditLog, Client, Payment

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)

    now = datetime.now(timezone.utc)
    in_3_days = now + timedelta(days=3)

    total_clients = (await db.execute(select(func.count(Client.id)))).scalar()
    active_clients = (
        await db.execute(
            select(func.count(Client.id)).where(Client.is_active == True)
        )
    ).scalar()
    expiring = (
        await db.execute(
            select(func.count(Client.id)).where(
                and_(
                    Client.is_active == True,
                    Client.expires_at <= in_3_days,
                    Client.expires_at >= now,
                )
            )
        )
    ).scalar()
    total_revenue = (
        await db.execute(
            select(func.sum(Payment.amount)).where(Payment.status == "success")
        )
    ).scalar() or 0

    recent_result = await db.execute(
        select(Client).order_by(Client.created_at.desc()).limit(10)
    )
    recent_clients = recent_result.scalars().all()

    logs_result = await db.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(20)
    )
    recent_logs = logs_result.scalars().all()

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "total_clients": total_clients,
            "active_clients": active_clients,
            "expiring": expiring,
            "total_revenue": total_revenue / 100,
            "recent_clients": recent_clients,
            "recent_logs": recent_logs,
        },
    )


@router.get("/stats")
async def admin_stats(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)

    now = datetime.now(timezone.utc)
    in_3_days = now + timedelta(days=3)

    total_clients = (await db.execute(select(func.count(Client.id)))).scalar()
    active_clients = (
        await db.execute(
            select(func.count(Client.id)).where(Client.is_active == True)
        )
    ).scalar()
    expiring = (
        await db.execute(
            select(func.count(Client.id)).where(
                and_(
                    Client.is_active == True,
                    Client.expires_at <= in_3_days,
                    Client.expires_at >= now,
                )
            )
        )
    ).scalar()
    total_revenue = (
        await db.execute(
            select(func.sum(Payment.amount)).where(Payment.status == "success")
        )
    ).scalar() or 0

    return JSONResponse(
        {
            "total_clients": total_clients,
            "active_clients": active_clients,
            "expiring_soon": expiring,
            "total_revenue_rub": total_revenue / 100,
        }
    )


@router.post("/stats/refresh")
async def refresh_stats(request: Request, db: AsyncSession = Depends(get_db)):
    """Trigger immediate traffic sync from all VLESS servers."""
    await require_admin(request, db)
    try:
        from scheduler import refresh_traffic_stats
        await refresh_traffic_stats()
        return JSONResponse({"status": "ok", "message": "Traffic stats refreshed"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request, page: int = 1, db: AsyncSession = Depends(get_db)
):
    user = await require_admin(request, db)
    per_page = 50
    offset = (page - 1) * per_page

    logs_result = await db.execute(
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    logs = logs_result.scalars().all()

    total = (await db.execute(select(func.count(AuditLog.id)))).scalar()
    total_pages = (total + per_page - 1) // per_page

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "logs": logs,
            "page": page,
            "total_pages": total_pages,
            "view": "audit",
        },
    )


@router.get("/payments", response_class=HTMLResponse)
async def payments_list(
    request: Request, page: int = 1, db: AsyncSession = Depends(get_db)
):
    user = await require_admin(request, db)
    per_page = 50
    offset = (page - 1) * per_page

    pay_result = await db.execute(
        select(Payment)
        .order_by(Payment.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    payments = pay_result.scalars().all()

    total = (await db.execute(select(func.count(Payment.id)))).scalar()
    total_pages = (total + per_page - 1) // per_page

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "payments": payments,
            "page": page,
            "total_pages": total_pages,
            "view": "payments",
        },
    )
