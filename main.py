import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from database import init_db, AsyncSessionLocal
from scheduler import setup_scheduler

from routers.auth import router as auth_router
from routers.client.cabinet import router as cabinet_router
from routers.admin.dashboard import router as admin_dashboard_router
from routers.admin.clients import router as admin_clients_router
from routers.admin.servers import router as admin_servers_router
from routers.admin.promo import router as admin_promo_router
from routers.webhook import router as webhook_router
from routers.subscription import router as subscription_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await create_default_admin()
    setup_scheduler()
    logger.info("VPN Prime Panel started")
    yield
    logger.info("VPN Prime Panel shutting down")


async def create_default_admin():
    from sqlalchemy import select
    from models import User
    from auth import hash_password, generate_referral_code

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == settings.admin_email))
        if not result.scalar_one_or_none():
            admin = User(
                email=settings.admin_email,
                password_hash=hash_password(settings.admin_password),
                role="admin",
                referral_code=generate_referral_code(),
                is_active=True,
            )
            db.add(admin)
            await db.commit()
            logger.info(f"Default admin created: {settings.admin_email}")


app = FastAPI(title="VPN Prime Panel", lifespan=lifespan)

# CORS for /sub/* endpoints (VPN client apps fetch subscriptions via XHR)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="vpn_session",
    max_age=30 * 24 * 3600,
    https_only=False,
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# Public endpoints first
app.include_router(subscription_router)
app.include_router(auth_router)
app.include_router(webhook_router)

# Authenticated client area
app.include_router(cabinet_router)

# Admin area
app.include_router(admin_dashboard_router)
app.include_router(admin_clients_router)
app.include_router(admin_servers_router)
app.include_router(admin_promo_router)


@app.get("/")
async def root(request: Request):
    session = request.session.get("user_id")
    if session:
        return RedirectResponse("/cabinet")
    return RedirectResponse("/auth/login")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=True,
    )
