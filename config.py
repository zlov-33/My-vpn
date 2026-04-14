from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    server_host: str = "0.0.0.0"
    server_port: int = 8080
    secret_key: str = "change_me"

    admin_email: str = "admin@vpn-prime.ru"
    admin_password: str = "changeme123"

    awg_interface: str = "awg0"
    awg_config_path: str = "/etc/amnezia/amneziawg/awg0.conf"
    awg_server_endpoint: str = ""
    awg_server_port: int = 48336
    awg_subnet: str = "10.8.0.0/24"
    awg_dns: str = "1.1.1.1"

    vless_api_url: str = "http://127.0.0.1:8100"
    vless_api_user: str = "admin"
    vless_api_pass: str = ""

    telegram_bot_token: str = ""
    telegram_admin_chat_id: str = ""

    resend_api_key: str = ""
    resend_from_email: str = "noreply@vpn-prime.ru"

    database_url: str = "sqlite+aiosqlite:///./vpn_panel.db"
    site_url: str = "https://vpn-prime.ru"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
