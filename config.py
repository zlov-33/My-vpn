from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    server_host: str = "0.0.0.0"
    server_port: int = 8080
    secret_key: str = "change_me"
    encryption_key: str = ""  # Fernet key for encrypting server passwords

    admin_email: str = "admin@vpn-prime.ru"
    admin_password: str = "changeme123"

    # VLESS API (main node — fallback for single-node setups)
    vless_api_url: str = "http://127.0.0.1:8100"
    vless_api_user: str = "admin"
    vless_api_pass: str = ""

    # Subscription
    site_url: str = "https://vpn-prime.ru"
    sub_default_format: str = "json"  # json | v2ray | clash

    # Telegram
    telegram_bot_token: str = ""
    telegram_admin_chat_id: str = ""

    # Email (Resend.com)
    resend_api_key: str = ""
    resend_from_email: str = "noreply@vpn-prime.ru"

    # Database
    database_url: str = "sqlite+aiosqlite:///./vpn_panel.db"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
