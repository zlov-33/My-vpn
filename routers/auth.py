from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models import User, AuditLog
from auth import (
    hash_password,
    verify_password,
    generate_referral_code,
    create_session_token,
    get_current_user,
    generate_reset_token,
)
from email_service import send_reset_password_email
from config import settings
import os

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "templates"))

router = APIRouter(prefix="/auth", tags=["auth"])


def flash(request: Request, message: str, category: str = "info"):
    msgs = request.session.get("flash_messages", [])
    msgs.append({"message": message, "category": category})
    request.session["flash_messages"] = msgs


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user:
        return RedirectResponse("/cabinet" if user.role != "admin" else "/admin", status_code=303)
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login")
async def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == email.lower().strip()))
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        flash(request, "Неверный email или пароль", "danger")
        return templates.TemplateResponse(
            "auth/login.html", {"request": request}, status_code=400
        )

    if not user.is_active:
        flash(request, "Аккаунт заблокирован", "danger")
        return templates.TemplateResponse(
            "auth/login.html", {"request": request}, status_code=403
        )

    token = create_session_token(user.id)
    redirect_url = "/admin" if user.role == "admin" else "/cabinet"
    response = RedirectResponse(redirect_url, status_code=303)
    response.set_cookie("session", token, httponly=True, max_age=30 * 24 * 3600, samesite="lax")

    log = AuditLog(
        user_id=user.id,
        action="login",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)
    await db.commit()

    return response


@router.get("/register", response_class=HTMLResponse)
async def register_get(request: Request, ref: str = None):
    return templates.TemplateResponse("auth/register.html", {"request": request, "ref": ref})


@router.post("/register")
async def register_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    ref: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    email = email.lower().strip()

    if password != password_confirm:
        flash(request, "Пароли не совпадают", "danger")
        return templates.TemplateResponse(
            "auth/register.html", {"request": request, "ref": ref}, status_code=400
        )

    if len(password) < 6:
        flash(request, "Пароль должен содержать минимум 6 символов", "danger")
        return templates.TemplateResponse(
            "auth/register.html", {"request": request, "ref": ref}, status_code=400
        )

    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        flash(request, "Пользователь с таким email уже существует", "danger")
        return templates.TemplateResponse(
            "auth/register.html", {"request": request, "ref": ref}, status_code=400
        )

    referred_by_id = None
    if ref:
        ref_result = await db.execute(select(User).where(User.referral_code == ref))
        ref_user = ref_result.scalar_one_or_none()
        if ref_user:
            referred_by_id = ref_user.id

    user = User(
        email=email,
        password_hash=hash_password(password),
        role="client",
        referral_code=generate_referral_code(),
        referred_by=referred_by_id,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    log = AuditLog(
        user_id=user.id,
        action="register",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)
    await db.commit()

    token = create_session_token(user.id)
    response = RedirectResponse("/cabinet", status_code=303)
    response.set_cookie("session", token, httponly=True, max_age=30 * 24 * 3600, samesite="lax")
    return response


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie("session")
    return response


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_get(request: Request):
    return templates.TemplateResponse("auth/reset_password.html", {"request": request, "mode": "forgot"})


@router.post("/forgot-password")
async def forgot_password_post(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    email = email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Always show success to avoid user enumeration
    if user:
        token = generate_reset_token()
        user.reset_token = token
        user.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=2)
        await db.commit()
        reset_url = f"{settings.site_url}/auth/reset-password?token={token}"
        await send_reset_password_email(email, reset_url)

    flash(request, "Если аккаунт существует, на email будет отправлена ссылка для сброса пароля", "success")
    return templates.TemplateResponse("auth/reset_password.html", {"request": request, "mode": "forgot"})


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_get(request: Request, token: str = None):
    if not token:
        return RedirectResponse("/auth/forgot-password", status_code=303)
    return templates.TemplateResponse(
        "auth/reset_password.html", {"request": request, "mode": "reset", "token": token}
    )


@router.post("/reset-password")
async def reset_password_post(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if password != password_confirm:
        flash(request, "Пароли не совпадают", "danger")
        return templates.TemplateResponse(
            "auth/reset_password.html",
            {"request": request, "mode": "reset", "token": token},
            status_code=400,
        )

    if len(password) < 6:
        flash(request, "Пароль должен содержать минимум 6 символов", "danger")
        return templates.TemplateResponse(
            "auth/reset_password.html",
            {"request": request, "mode": "reset", "token": token},
            status_code=400,
        )

    result = await db.execute(select(User).where(User.reset_token == token))
    user = result.scalar_one_or_none()

    if not user or not user.reset_token_expires:
        flash(request, "Ссылка недействительна или устарела", "danger")
        return RedirectResponse("/auth/forgot-password", status_code=303)

    expires = (
        user.reset_token_expires.replace(tzinfo=timezone.utc)
        if user.reset_token_expires.tzinfo is None
        else user.reset_token_expires
    )
    if expires < datetime.now(timezone.utc):
        flash(request, "Ссылка для сброса пароля истекла", "danger")
        return RedirectResponse("/auth/forgot-password", status_code=303)

    user.password_hash = hash_password(password)
    user.reset_token = None
    user.reset_token_expires = None
    await db.commit()

    flash(request, "Пароль успешно изменён. Войдите в аккаунт.", "success")
    return RedirectResponse("/auth/login", status_code=303)
