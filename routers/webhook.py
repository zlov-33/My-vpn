import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models import Payment, Client, Device
import awg
import telegram
from config import settings
from vless_api import VlessApiClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


async def _activate_client_after_payment(db: AsyncSession, payment: Payment):
    """Activate client subscription after successful payment."""
    c_result = await db.execute(select(Client).where(Client.id == payment.client_id))
    client = c_result.scalar_one_or_none()
    if not client:
        return

    now = datetime.now(timezone.utc)
    if client.expires_at:
        exp = (
            client.expires_at.replace(tzinfo=timezone.utc)
            if client.expires_at.tzinfo is None
            else client.expires_at
        )
        base = max(exp, now)
    else:
        base = now

    client.expires_at = base + timedelta(days=payment.months * 30)
    client.is_active = True

    # Enable VLESS user
    if client.vless_username:
        try:
            vless = VlessApiClient(
                settings.vless_api_url, settings.vless_api_user, settings.vless_api_pass
            )
            await vless.update_user_expire(
                client.vless_username, int(client.expires_at.timestamp())
            )
            await vless.enable_user(client.vless_username)
        except Exception as e:
            logger.warning(f"VLESS API update failed: {e}")

    # Re-add AWG peers
    dev_result = await db.execute(select(Device).where(Device.client_id == client.id))
    devices = dev_result.scalars().all()
    for device in devices:
        awg.add_peer(
            device.public_key, device.preshared_key, device.ip_address, settings.awg_interface
        )

    await telegram.notify_admin(
        f"💰 Оплата получена: клиент {client.name}, метод {payment.method}, "
        f"+{payment.months} мес. (сумма: {payment.amount / 100:.2f} руб.)"
    )


@router.post("/payment")
async def payment_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Handle payment webhooks from YooKassa and Robokassa.

    YooKassa sends JSON with:
      {"type": "notification", "object": {"id": ..., "status": "succeeded", "amount": {...}, "metadata": {...}}}

    Robokassa sends form-encoded with:
      OutSum, InvId, SignatureValue, etc.
    """
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        # YooKassa format
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        event_type = data.get("type")
        obj = data.get("object", {})

        if event_type == "notification" and obj.get("status") == "succeeded":
            external_id = obj.get("id")
            metadata = obj.get("metadata", {})
            client_id = metadata.get("client_id")
            months = int(metadata.get("months", 1))
            amount_value = obj.get("amount", {}).get("value", "0")
            amount_kopecks = int(float(amount_value) * 100)

            # Find existing pending payment or create new
            pay_result = await db.execute(
                select(Payment).where(Payment.external_id == external_id)
            )
            payment = pay_result.scalar_one_or_none()

            if not payment and client_id:
                payment = Payment(
                    client_id=int(client_id),
                    amount=amount_kopecks,
                    method="yookassa",
                    status="success",
                    external_id=external_id,
                    months=months,
                    paid_at=datetime.now(timezone.utc),
                )
                db.add(payment)
                await db.flush()
                await _activate_client_after_payment(db, payment)
            elif payment and payment.status != "success":
                payment.status = "success"
                payment.paid_at = datetime.now(timezone.utc)
                await db.flush()
                await _activate_client_after_payment(db, payment)

            await db.commit()
            return JSONResponse({"status": "ok"})

        elif event_type == "notification" and obj.get("status") in ("canceled", "refunded"):
            external_id = obj.get("id")
            pay_result = await db.execute(
                select(Payment).where(Payment.external_id == external_id)
            )
            payment = pay_result.scalar_one_or_none()
            if payment:
                payment.status = "refunded" if obj.get("status") == "refunded" else "failed"
                await db.commit()
            return JSONResponse({"status": "ok"})

        return JSONResponse({"status": "ignored"})

    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        # Robokassa format
        try:
            form = await request.form()
        except Exception:
            return JSONResponse({"error": "Invalid form"}, status_code=400)

        out_sum = form.get("OutSum", "0")
        inv_id = form.get("InvId", "0")
        signature = form.get("SignatureValue", "")

        # In production, verify signature here
        # For now just log and process
        logger.info(f"Robokassa webhook: InvId={inv_id}, OutSum={out_sum}")

        pay_result = await db.execute(
            select(Payment).where(Payment.external_id == f"robokassa_{inv_id}")
        )
        payment = pay_result.scalar_one_or_none()
        if payment and payment.status != "success":
            payment.status = "success"
            payment.paid_at = datetime.now(timezone.utc)
            await db.flush()
            await _activate_client_after_payment(db, payment)
            await db.commit()

        return JSONResponse({"status": "ok"})

    return JSONResponse({"status": "unknown_content_type"})


@router.post("/telegram")
async def telegram_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle Telegram bot webhook messages for /link command."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": True})

    message = data.get("message", {})
    text = message.get("text", "")
    chat_id = str(message.get("chat", {}).get("id", ""))

    if text.startswith("/link "):
        code = text.split(" ", 1)[1].strip()
        u_result = await db.execute(
            select(__import__("models").User).where(
                __import__("models").User.telegram_link_code == code
            )
        )
        user = u_result.scalar_one_or_none()
        if user:
            user.telegram_id = chat_id
            user.telegram_link_code = None
            await db.commit()
            await telegram.send_message(chat_id, "✅ Telegram успешно привязан к вашему аккаунту VPN Prime!")
        else:
            await telegram.send_message(chat_id, "❌ Код не найден или устарел. Получите новый в личном кабинете.")

    elif text == "/start":
        await telegram.send_message(
            chat_id,
            "Привет! Это бот VPN Prime.\n\n"
            "Для привязки аккаунта используйте команду <code>/link КОД</code>, "
            "где КОД можно получить в личном кабинете.",
        )

    return JSONResponse({"ok": True})
