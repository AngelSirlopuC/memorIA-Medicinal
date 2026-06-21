"""Webhook de WhatsApp Cloud API.

- GET  /whatsapp/webhook : verificación de Meta (hub.challenge).
- POST /whatsapp/webhook : recepción de mensajes.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.config import get_settings
from app.integrations import whatsapp

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.get("/webhook")
async def verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    settings = get_settings()
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return PlainTextResponse(content=hub_challenge or "")
    raise HTTPException(status_code=403, detail="Verificación fallida.")


@router.post("/webhook")
async def receive(request: Request):
    settings = get_settings()
    if not settings.whatsapp_enabled:
        raise HTTPException(status_code=503, detail="WhatsApp no está configurado.")
    update = await request.json()
    # Responder 200 de inmediato y procesar en segundo plano (Meta reintenta si tarda)
    asyncio.create_task(whatsapp.process_update(update))
    return {"ok": True}
