import httpx
from config import settings
from jinja2 import Environment, FileSystemLoader
import os

env = Environment(loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates/emails")))


async def send_reset_password_email(email: str, reset_url: str):
    if not settings.resend_api_key:
        return
    template = env.get_template("reset_password.html")
    html = template.render(reset_url=reset_url)
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.resend_from_email,
                "to": [email],
                "subject": "Сброс пароля VPN Prime",
                "html": html,
            },
        )


async def send_welcome_email(email: str, name: str):
    if not settings.resend_api_key:
        return
    html = f"""
    <h2>Добро пожаловать в VPN Prime!</h2>
    <p>Здравствуйте, {name}!</p>
    <p>Ваш аккаунт успешно создан. Войдите в <a href="{settings.site_url}/cabinet">личный кабинет</a>.</p>
    """
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.resend_from_email,
                "to": [email],
                "subject": "Добро пожаловать в VPN Prime",
                "html": html,
            },
        )
