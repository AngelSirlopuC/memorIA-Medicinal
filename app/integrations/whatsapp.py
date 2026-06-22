"""Integración con WhatsApp Cloud API (Sprint 7).

Reutiliza el pipeline (registrar/consultar/feedback), igual que Telegram. El mismo
`process_update` procesa el payload del webhook de Meta.

UX (mensajes interactivos de WhatsApp):
- Foto → botones [Registrar] [Consultar].
  - Registrar → lista de tipo (receta/caja/blíster/pastilla) → guarda.
  - Consultar → coincidencias + lista de feedback (1/2/3/Ninguna).
- Foto con caption-pregunta = consulta directa.
"""

from __future__ import annotations

import logging

import httpx

from app.ai.client import AINotConfiguredError
from app.config import get_settings
from app import agent
from app.db.session import SessionLocal
from app.services import collage, pipeline, profiles  # noqa: F401 (pipeline usado por el agente)
from app.storage import get_storage

log = logging.getLogger("whatsapp")

_VALID_SOURCES = {"receta", "caja", "blister", "pastilla"}
_SOURCE_LABELS = {"receta": "Receta", "caja": "Caja", "blister": "Blíster", "pastilla": "Pastilla"}

_pending_photo: dict[str, str] = {}   # wa_id -> media_id de la última foto
_pending_query: dict[str, dict] = {}  # wa_id -> {"query_id":.., "records":[..]}
_active_profile: dict[str, str] = {}  # wa_id -> profile_id activo
_awaiting_name: set[str] = set()      # chats creando un perfil

WELCOME = (
    "👋 *MemorIA Medicinal*\n\n"
    "Tu memoria de medicamentos. Háblame con naturalidad y envíame *fotos*. Por ejemplo: "
    "_\"Hoy Thiago tuvo cita y le recetaron esto\"_ (adjunta la receta) y luego una foto de "
    "cada medicina; o _\"¿cuándo compré esta pastilla?\"_ con una foto.\n\n"
    "_No identifico medicamentos ni indico si se pueden tomar. Verifica el vencimiento y "
    "consulta con un profesional de salud._\n\n"
    "Escribe *perfiles* para registrar a varios miembros de la familia."
)


# --- Cliente Cloud API --------------------------------------------------------


class WhatsAppClient:
    def __init__(self, token: str, phone_id: str, version: str) -> None:
        self.token = token
        self.base = f"https://graph.facebook.com/{version}"
        self.messages_url = f"{self.base}/{phone_id}/messages"
        self.media_url = f"{self.base}/{phone_id}/media"

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    async def _send(self, payload: dict) -> dict:
        payload = {"messaging_product": "whatsapp", **payload}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(self.messages_url, headers=self._headers, json=payload)
            return r.json()

    async def send_text(self, to: str, text: str) -> dict:
        return await self._send({"to": to, "type": "text", "text": {"body": text}})

    async def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]]) -> dict:
        """buttons: lista de (id, título). Máx 3."""
        return await self._send({
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": bid, "title": title[:20]}}
                        for bid, title in buttons[:3]
                    ]
                },
            },
        })

    async def send_list(
        self,
        to: str,
        body: str,
        button: str,
        rows: list[tuple[str, str]],
        header_image_id: str | None = None,
    ) -> dict:
        """rows: lista de (id, título). Hasta 10 filas. Header de imagen opcional."""
        interactive: dict = {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button[:20],
                "sections": [
                    {
                        "title": "Opciones",
                        "rows": [{"id": rid, "title": title[:24]} for rid, title in rows[:10]],
                    }
                ],
            },
        }
        if header_image_id:
            interactive["header"] = {"type": "image", "image": {"id": header_image_id}}
        return await self._send({"to": to, "type": "interactive", "interactive": interactive})

    async def upload_media(self, image: bytes, mime: str = "image/jpeg") -> str:
        """Sube una imagen y devuelve su media_id (para usarla como header)."""
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                self.media_url,
                headers=self._headers,
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": ("collage.jpg", image, mime)},
            )
            return r.json()["id"]

    async def download_media(self, media_id: str) -> bytes:
        async with httpx.AsyncClient(timeout=60) as c:
            info = (await c.get(f"{self.base}/{media_id}", headers=self._headers)).json()
            url = info["url"]
            r = await c.get(url, headers=self._headers)
            r.raise_for_status()
            return r.content


def get_client() -> WhatsAppClient:
    settings = get_settings()
    if not settings.whatsapp_enabled:
        raise RuntimeError("WhatsApp no está configurado (token/phone_id).")
    return WhatsAppClient(
        settings.whatsapp_token, settings.whatsapp_phone_id, settings.whatsapp_api_version
    )


# --- Helpers ------------------------------------------------------------------


def _conf_label(c: float | None) -> str:
    if c is None:
        return ""
    if c >= 0.75:
        return "confianza alta"
    if c >= 0.45:
        return "confianza media"
    return "confianza baja"


async def _send_profiles(wa: WhatsAppClient, to: str) -> None:
    async with SessionLocal() as session:
        profs = await profiles.list_profiles(session)
    active_id = agent.get_conversation_profile(f"wa:{to}")[0]
    rows = [(f"prof:{p.id}", p.display_name) for p in profs]
    rows.append(("prof:new", "➕ Nuevo perfil"))
    active = next((p.display_name for p in profs if str(p.id) == str(active_id)), "por defecto")
    await wa.send_list(to, f"👪 Perfil activo: *{active}*. Elige o crea otro:", "Perfiles", rows)


async def _send_agent_reply(wa: WhatsAppClient, to: str, reply, query_image: bytes | None = None) -> None:
    """Envía las respuestas del agente; si hubo consulta, manda lista con collage + feedback."""
    for t in reply.texts:
        await wa.send_text(to, t)
    q = reply.query
    if q and q.candidates:
        _pending_query[to] = {"query_id": q.query_id, "records": [c.record_id for c in q.candidates]}
        rows = [(f"fb:{i}", f"Opción {i + 1}") for i in range(len(q.candidates))]
        rows.append(("fb:none", "Ninguna"))
        header_id = None
        if len(q.candidates) > 1 and query_image:
            try:
                img = collage.collage_for_candidates(get_storage(), query_image, q.candidates)
                header_id = await wa.upload_media(img)
            except Exception:  # noqa: BLE001
                header_id = None
        await wa.send_list(to, "¿Cuál es la correcta?", "Responder", rows, header_image_id=header_id)


async def _handle_feedback(wa: WhatsAppClient, to: str, token: str) -> None:
    pending = _pending_query.get(to)
    if not pending:
        await wa.send_text(to, "No tengo una consulta reciente para registrar tu respuesta.")
        return
    selected = None
    if token != "none":
        try:
            selected = pending["records"][int(token)]
        except (ValueError, IndexError):
            selected = None
    async with SessionLocal() as session:
        await pipeline.submit_feedback(session, pending["query_id"], selected)
    _pending_query.pop(to, None)
    await wa.send_text(to, "¡Gracias! Tu respuesta me ayuda a mejorar. 🙌")


def _interactive_id(message: dict) -> str | None:
    inter = message.get("interactive", {})
    if inter.get("type") == "button_reply":
        return inter["button_reply"]["id"]
    if inter.get("type") == "list_reply":
        return inter["list_reply"]["id"]
    return None


# --- Procesamiento ------------------------------------------------------------


async def _handle_message(wa: WhatsAppClient, msg: dict) -> None:
    to = msg["from"]
    cid = f"wa:{to}"
    mtype = msg.get("type")

    if mtype == "image":
        media_id = msg["image"]["id"]
        caption = (msg["image"].get("caption") or "").strip()
        image = await wa.download_media(media_id)
        reply = await agent.handle_message(cid, caption or None, image)
        await _send_agent_reply(wa, to, reply, image)
        return

    if mtype == "interactive":
        data = _interactive_id(msg)
        if not data:
            return
        if data.startswith("fb:"):
            await _handle_feedback(wa, to, data.split(":", 1)[1])
        elif data == "prof:new":
            _awaiting_name.add(to)
            await wa.send_text(to, "Escribe el *nombre* del nuevo perfil (p. ej. María).")
        elif data.startswith("prof:"):
            import uuid as _uuid

            pid = data.split(":", 1)[1]
            async with SessionLocal() as session:
                try:
                    prof = await profiles.get_profile(session, _uuid.UUID(pid))
                except ValueError:
                    prof = None
            if prof:
                agent.set_conversation_profile(cid, prof.id, prof.display_name)
                await wa.send_text(to, f"👤 Perfil activo: *{prof.display_name}*.")
            else:
                await wa.send_text(to, "Ese perfil ya no existe.")
        return

    if mtype == "text":
        raw = (msg["text"].get("body") or "").strip()
        body = raw.lower()
        if to in _awaiting_name:
            _awaiting_name.discard(to)
            name = raw[:60] or "Perfil"
            async with SessionLocal() as session:
                prof = await profiles.create_profile(session, name)
                await session.commit()
            agent.set_conversation_profile(cid, prof.id, prof.display_name)
            await wa.send_text(to, f"✅ Perfil *{name}* creado y activado.")
        elif body in ("hola", "/start", "start", "ayuda", "help", "/help"):
            await wa.send_text(to, WELCOME)
        elif body in ("perfiles", "/perfiles", "perfil", "/perfil"):
            await _send_profiles(wa, to)
        elif raw:
            reply = await agent.handle_message(cid, raw, None)
            await _send_agent_reply(wa, to, reply)


async def process_update(update: dict) -> None:
    """Procesa el payload del webhook de WhatsApp Cloud API."""
    wa = get_client()
    try:
        for entry in update.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                # Ignora 'statuses' (recibos de entrega/lectura)
                for msg in value.get("messages", []):
                    await _handle_message(wa, msg)
    except Exception:  # noqa: BLE001 — un error no debe tumbar el webhook
        log.exception("Error procesando update de WhatsApp")
