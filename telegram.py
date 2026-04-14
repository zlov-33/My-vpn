import httpx
from config import settings

BOT_URL = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


async def send_message(chat_id: str, text: str):
    if not settings.telegram_bot_token or not chat_id:
        return
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{BOT_URL}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
        except Exception:
            pass


async def notify_admin(message: str):
    await send_message(settings.telegram_admin_chat_id, message)


async def notify_user(telegram_id: str, message: str):
    await send_message(telegram_id, message)


async def send_awg_config(telegram_id: str, config_text: str, device_name: str):
    if not settings.telegram_bot_token or not telegram_id:
        return
    file_bytes = config_text.encode()
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{BOT_URL}/sendDocument",
                data={"chat_id": telegram_id, "caption": f"Конфиг для {device_name}"},
                files={"document": (f"{device_name}.conf", file_bytes, "text/plain")},
            )
        except Exception:
            pass


async def send_awg_qr(telegram_id: str, qr_bytes: bytes, device_name: str):
    if not settings.telegram_bot_token or not telegram_id:
        return
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{BOT_URL}/sendPhoto",
                data={"chat_id": telegram_id, "caption": f"QR-код для {device_name}"},
                files={"photo": (f"{device_name}_qr.png", qr_bytes, "image/png")},
            )
        except Exception:
            pass


async def send_vless_link(telegram_id: str, sub_url: str):
    await send_message(
        telegram_id,
        f"Ваша VLESS ссылка-подписка:\n<code>{sub_url}</code>",
    )
