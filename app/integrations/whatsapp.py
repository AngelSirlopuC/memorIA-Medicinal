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
from app.db.session import SessionLocal
from app.services import collage, pipeline, profiles
from app.storage import get_storage

log = logging.getLogger("whatsapp")

_VALID_SOURCES = {"receta", "caja", "blister", "pastilla"}
_SOURCE_LABELS = {"receta": "Receta", "caja": "Caja", "blister": "Blíster", "pastilla": "Pastilla"}

_pending_photo: dict[str, str] = {}   # wa_id -> media_id de la última foto
_pending_query: dict[str, dict] = {}  # wa_id -> {"query_id":.., "records":[..]}

WELCOME = (
    "👋 *MemorIA Medicinal*\n\n"
    "Tu memoria de medicamentos. Envíame una *foto* de una receta, caja, blíster o "
    "pastilla y elige si quieres *registrarla* o *consultar* a cuál de tus medicamentos "
    "se parece.\n\n"
    "_No identifico medicamentos ni indico si se pueden tomar. Verifica el vencimiento y "
    "consulta con un profesional de salud._"
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


async def _resolve_profile_id(session) -> str:
    prof = await profiles.get_or_create_default_profile(session)
    await session.commit()
    return prof.id


async def _do_register(wa: WhatsAppClient, to: str, media_id: str, source: str) -> None:
    image = await wa.download_media(media_id)
    async with SessionLocal() as session:
        pid = await _resolve_profile_id(session)
        try:
            r = await pipeline.register_record(session, pid, image, source, "image/jpeg")
        except AINotConfiguredError:
            await wa.send_text(to, "⚠️ La IA no está configurada (falta OPENAI_API_KEY).")
            return
    if r.deduplicated:
        await wa.send_text(to, "Esta foto ya estaba registrada en tu memoria. ✓")
    else:
        name = (f"*{r.name}*" + (f" {r.dose}" if r.dose else "")) if r.name else "el medicamento"
        await wa.send_text(to, f"✅ Registré {name} como _{_SOURCE_LABELS.get(source, source)}_.")
    _pending_photo.pop(to, None)


async def _do_query(wa: WhatsAppClient, to: str, media_id: str, question: str | None) -> None:
    image = await wa.download_media(media_id)
    async with SessionLocal() as session:
        pid = await _resolve_profile_id(session)
        try:
            r = await pipeline.query_medicine(session, pid, image, question, "image/jpeg")
        except AINotConfiguredError:
            await wa.send_text(to, "⚠️ La IA no está configurada (falta OPENAI_API_KEY).")
            return

    if not r.candidates:
        await wa.send_text(
            to, "No encontré coincidencias en tu historial todavía. Regístralo para reconocerlo después."
        )
        _pending_photo.pop(to, None)
        return

    lines = ["🔎 *Posibles coincidencias:*", ""]
    for i, c in enumerate(r.candidates, start=1):
        name = c.name or f"Registro #{c.rank}"
        date = c.registered_at.strftime("%d/%m/%Y") if c.registered_at else "fecha desconocida"
        conf = _conf_label(c.vision_confidence if c.vision_confidence is not None else c.vector_score)
        lines.append(f"*{i}.* {name} — registrado {date}" + (f" · {conf}" if conf else ""))
    lines.append("")
    lines.append("_Posible coincidencia. Verifica el vencimiento y consulta con un profesional._")
    body = "\n".join(lines)

    _pending_query[to] = {"query_id": r.query_id, "records": [c.record_id for c in r.candidates]}
    rows = [(f"fb:{i}", f"Opción {i + 1}") for i in range(len(r.candidates))]
    rows.append(("fb:none", "Ninguna"))

    # Collage: una imagen con las opciones numeradas como header de la lista
    header_id = None
    if len(r.candidates) > 1:
        try:
            img = collage.collage_for_candidates(get_storage(), image, r.candidates)
            header_id = await wa.upload_media(img)
        except Exception:  # noqa: BLE001 — si falla, enviamos la lista sin header
            header_id = None
    await wa.send_list(to, body, "Responder", rows, header_image_id=header_id)
    _pending_photo.pop(to, None)


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
    mtype = msg.get("type")

    if mtype == "image":
        media_id = msg["image"]["id"]
        _pending_photo[to] = media_id
        caption = (msg["image"].get("caption") or "").strip()
        low = caption.lower()
        if low.startswith(("registrar", "guardar")):
            await wa.send_list(
                to, "¿Qué tipo de foto es?", "Elegir tipo",
                [("src:blister", "Blíster"), ("src:caja", "Caja"),
                 ("src:receta", "Receta"), ("src:pastilla", "Pastilla")],
            )
        elif caption:
            await _do_query(wa, to, media_id, caption)
        else:
            await wa.send_buttons(
                to, "¿Qué quieres hacer con esta foto?",
                [("act:reg", "📝 Registrar"), ("act:query", "🔍 Consultar")],
            )
        return

    if mtype == "interactive":
        data = _interactive_id(msg)
        if not data:
            return
        if data == "act:reg":
            await wa.send_list(
                to, "¿Qué tipo de foto es?", "Elegir tipo",
                [("src:blister", "Blíster"), ("src:caja", "Caja"),
                 ("src:receta", "Receta"), ("src:pastilla", "Pastilla")],
            )
        elif data == "act:query":
            mid = _pending_photo.get(to)
            await (_do_query(wa, to, mid, None) if mid else wa.send_text(to, "Envíame primero una foto. 📷"))
        elif data.startswith("src:"):
            src = data.split(":", 1)[1]
            mid = _pending_photo.get(to)
            if src in _VALID_SOURCES and mid:
                await _do_register(wa, to, mid, src)
            else:
                await wa.send_text(to, "Envíame primero una foto. 📷")
        elif data.startswith("fb:"):
            await _handle_feedback(wa, to, data.split(":", 1)[1])
        return

    if mtype == "text":
        body = (msg["text"].get("body") or "").strip().lower()
        if body in ("hola", "/start", "start", "ayuda", "help", "/help"):
            await wa.send_text(to, WELCOME)
        else:
            await wa.send_text(to, "Envíame una *foto* de un medicamento para empezar. 📷")


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
