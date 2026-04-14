import secrets
import string
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models import User
from config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
signer = URLSafeTimedSerializer(settings.secret_key)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def generate_referral_code() -> str:
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))


def create_session_token(user_id: int) -> str:
    return signer.dumps({"user_id": user_id})


def verify_session_token(token: str, max_age: int = 30 * 24 * 3600) -> dict | None:
    try:
        return signer.loads(token, max_age=max_age)
    except Exception:
        return None


async def get_current_user(request: Request, db: AsyncSession) -> User | None:
    token = request.cookies.get("session")
    if not token:
        return None
    data = verify_session_token(token)
    if not data:
        return None
    result = await db.execute(select(User).where(User.id == data["user_id"]))
    return result.scalar_one_or_none()


async def require_user(request: Request, db: AsyncSession) -> User:
    user = await get_current_user(request, db)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=307,
            headers={"Location": "/auth/login"},
        )
    return user


async def require_admin(request: Request, db: AsyncSession) -> User:
    user = await require_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    return user


def generate_reset_token() -> str:
    return secrets.token_urlsafe(32)
