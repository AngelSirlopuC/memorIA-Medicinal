"""Webhook de Telegram."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import get_settings
from app.integrations import telegram

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    settings = get_settings()
    if not settings.telegram_enabled:
        raise HTTPException(status_code=503, detail="Telegram no está configurado.")

    secret = settings.telegram_webhook_secret
    if secret and x_telegram_bot_api_secret_token != secret:
        raise HTTPException(status_code=403, detail="Secret token inválido.")

    update = await request.json()
    # Responder 200 de inmediato y procesar en segundo plano (evita timeouts de Telegram)
    asyncio.create_task(telegram.process_update(update))
    return {"ok": True}
