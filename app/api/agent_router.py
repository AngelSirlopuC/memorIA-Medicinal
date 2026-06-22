"""Endpoint del agente conversacional (Sprint 10)."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.agent import handle_message
from app.schemas import AgentMessageResponse

router = APIRouter(prefix="/agent", tags=["agent"])

_MAX_BYTES = 15 * 1024 * 1024


@router.post("/message", response_model=AgentMessageResponse)
async def agent_message(
    conversation_id: str = Form(...),
    text: str | None = Form(None),
    profile_id: str | None = Form(None),
    image: UploadFile | None = File(None),
):
    image_bytes: bytes | None = None
    mime = "image/jpeg"
    if image is not None:
        image_bytes = await image.read()
        if len(image_bytes) > _MAX_BYTES:
            raise HTTPException(status_code=413, detail="Imagen demasiado grande (máx 15 MB).")
        mime = image.content_type or "image/jpeg"
        if not mime.startswith("image/"):
            raise HTTPException(status_code=400, detail="El archivo debe ser una imagen.")

    if not text and image_bytes is None:
        raise HTTPException(status_code=400, detail="Envía texto o una imagen.")

    reply = await handle_message(conversation_id, text, image_bytes, mime, profile_id)
    return AgentMessageResponse(
        replies=reply.texts,
        query=reply.query,
        profile_name=reply.profile_name,
        prescription_open=reply.prescription_open,
    )
