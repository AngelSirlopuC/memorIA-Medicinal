"""Integración con Telegram (Sprint 2).

Reutiliza el pipeline (registrar/consultar/feedback). El mismo `process_update` sirve
tanto para el webhook como para el script de polling local.

UX:
- El usuario envía una foto → botones [Registrar] [Consultar].
  - Registrar → botones de tipo (receta/caja/blíster/pastilla) → guarda.
  - Consultar → busca en el historial y muestra candidatos con botones de feedback.
- La foto con caption tipo pregunta se interpreta como consulta directa.
"""

from __future__ import annotations

import logging

import httpx

from app.ai.client import AINotConfiguredError
from app.config import get_settings
from app.db.session import SessionLocal
from app.services import pipeline, profiles

log = logging.getLogger("telegram")

_VALID_SOURCES = {"receta", "caja", "blister", "pastilla"}
_SOURCE_LABELS = {"receta": "Receta", "caja": "Caja", "blister": "Blíster", "pastilla": "Pastilla"}

# Estado en memoria (suficiente para un despliegue de un usuario/familia)
_pending_photo: dict[int, str] = {}          # chat_id -> file_id de la última foto
_pending_query: dict[int, dict] = {}         # chat_id -> {"query_id":.., "records":[..]}


# --- Cliente HTTP de Telegram -------------------------------------------------


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"

    async def _post(self, method: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base}/{method}", json=payload)
            return r.json()

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self._post("sendMessage", payload)

    async def answer_callback(self, callback_id: str, text: str | None = None) -> dict:
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        return await self._post("answerCallbackQuery", payload)

    async def send_photo(self, chat_id: int, image: bytes, caption: str = "", reply_markup: dict | None = None) -> dict:
        data = {"chat_id": str(chat_id), "caption": caption, "parse_mode": "Markdown"}
        if reply_markup:
            import json as _json

            data["reply_markup"] = _json.dumps(reply_markup)
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{self.base}/sendPhoto",
                data=data,
                files={"photo": ("foto.jpg", image, "image/jpeg")},
            )
            return r.json()

    async def download_file(self, file_id: str) -> bytes:
        info = await self._post("getFile", {"file_id": file_id})
        file_path = info["result"]["file_path"]
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(f"{self.file_base}/{file_path}")
            r.raise_for_status()
            return r.content

    async def set_webhook(self, url: str, secret: str | None = None) -> dict:
        payload = {"url": url}
        if secret:
            payload["secret_token"] = secret
        return await self._post("setWebhook", payload)


def get_client() -> TelegramClient:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN no configurado.")
    return TelegramClient(settings.telegram_bot_token)


# --- Teclados -----------------------------------------------------------------


def _action_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "📝 Registrar", "callback_data": "act:reg"},
                {"text": "🔍 Consultar", "callback_data": "act:query"},
            ]
        ]
    }


def _source_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "💊 Blíster", "callback_data": "src:blister"},
             {"text": "📦 Caja", "callback_data": "src:caja"}],
            [{"text": "📄 Receta", "callback_data": "src:receta"},
             {"text": "⚪ Pastilla", "callback_data": "src:pastilla"}],
        ]
    }


def _feedback_keyboard(n: int) -> dict:
    row = [{"text": str(i + 1), "callback_data": f"fb:{i}"} for i in range(n)]
    return {"inline_keyboard": [row, [{"text": "Ninguna", "callback_data": "fb:none"}]]}


def _conf_label(c: float | None) -> str:
    if c is None:
        return ""
    if c >= 0.75:
        return "confianza alta"
    if c >= 0.45:
        return "confianza media"
    return "confianza baja"


WELCOME = (
    "👋 *MemorIA Medicinal*\n\n"
    "Tu memoria de medicamentos. Envíame una *foto* de una receta, caja, blíster o "
    "pastilla y elige si quieres *registrarla* o *consultar* a cuál de tus medicamentos "
    "se parece.\n\n"
    "_No identifico medicamentos ni indico si se pueden tomar. Verifica el vencimiento y "
    "consulta con un profesional de salud._"
)


# --- Procesamiento ------------------------------------------------------------


async def _resolve_profile_id(session) -> str:
    prof = await profiles.get_or_create_default_profile(session)
    await session.commit()
    return prof.id


async def _do_register(tg: TelegramClient, chat_id: int, file_id: str, source: str) -> None:
    image = await tg.download_file(file_id)
    async with SessionLocal() as session:
        pid = await _resolve_profile_id(session)
        try:
            r = await pipeline.register_record(session, pid, image, source, "image/jpeg")
        except AINotConfiguredError:
            await tg.send_message(chat_id, "⚠️ La IA no está configurada (falta OPENAI_API_KEY).")
            return
    if r.deduplicated:
        await tg.send_message(chat_id, "Esta foto ya estaba registrada en tu memoria. ✓")
    else:
        name = f"*{r.name}*" + (f" {r.dose}" if r.dose else "") if r.name else "el medicamento"
        await tg.send_message(chat_id, f"✅ Registré {name} como _{_SOURCE_LABELS.get(source, source)}_.")
    _pending_photo.pop(chat_id, None)


async def _do_query(tg: TelegramClient, chat_id: int, file_id: str, question: str | None) -> None:
    image = await tg.download_file(file_id)
    async with SessionLocal() as session:
        pid = await _resolve_profile_id(session)
        try:
            r = await pipeline.query_medicine(session, pid, image, question, "image/jpeg")
        except AINotConfiguredError:
            await tg.send_message(chat_id, "⚠️ La IA no está configurada (falta OPENAI_API_KEY).")
            return

    if not r.candidates:
        await tg.send_message(
            chat_id,
            "No encontré coincidencias en tu historial todavía. Regístralo para reconocerlo después.",
        )
        _pending_photo.pop(chat_id, None)
        return

    lines = ["🔎 *Posibles coincidencias:*", ""]
    for i, c in enumerate(r.candidates, start=1):
        name = c.name or f"Registro #{c.rank}"
        date = c.registered_at.strftime("%d/%m/%Y") if c.registered_at else "fecha desconocida"
        conf = _conf_label(c.vision_confidence if c.vision_confidence is not None else c.vector_score)
        lines.append(f"*{i}.* {name} — registrado {date}" + (f" · {conf}" if conf else ""))
    lines.append("")
    lines.append("_Posible coincidencia. Verifica el vencimiento y consulta con un profesional._")

    _pending_query[chat_id] = {
        "query_id": r.query_id,
        "records": [c.record_id for c in r.candidates],
    }
    await tg.send_message(chat_id, "\n".join(lines), _feedback_keyboard(len(r.candidates)))
    _pending_photo.pop(chat_id, None)


async def _handle_feedback(tg: TelegramClient, chat_id: int, token: str) -> None:
    pending = _pending_query.get(chat_id)
    if not pending:
        await tg.send_message(chat_id, "No tengo una consulta reciente para registrar tu respuesta.")
        return
    selected = None
    if token != "none":
        try:
            selected = pending["records"][int(token)]
        except (ValueError, IndexError):
            selected = None
    async with SessionLocal() as session:
        await pipeline.submit_feedback(session, pending["query_id"], selected)
    _pending_query.pop(chat_id, None)
    await tg.send_message(chat_id, "¡Gracias! Tu respuesta me ayuda a mejorar. 🙌")


async def process_update(update: dict) -> None:
    """Procesa un update de Telegram (webhook o polling)."""
    tg = get_client()
    try:
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]

            if "photo" in msg:
                file_id = msg["photo"][-1]["file_id"]  # mayor resolución
                _pending_photo[chat_id] = file_id
                caption = (msg.get("caption") or "").strip()
                low = caption.lower()
                if low.startswith(("registrar", "guardar", "/registrar")):
                    await tg.send_message(chat_id, "¿Qué tipo de foto es?", _source_keyboard())
                elif caption:
                    await _do_query(tg, chat_id, file_id, caption)
                else:
                    await tg.send_message(chat_id, "¿Qué quieres hacer con esta foto?", _action_keyboard())
                return

            text = (msg.get("text") or "").strip()
            if text in ("/start", "/help", "start", "help"):
                await tg.send_message(chat_id, WELCOME)
            else:
                await tg.send_message(chat_id, "Envíame una *foto* de un medicamento para empezar. 📷")
            return

        if "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            data = cq.get("data", "")
            await tg.answer_callback(cq["id"])

            if data == "act:reg":
                await tg.send_message(chat_id, "¿Qué tipo de foto es?", _source_keyboard())
            elif data == "act:query":
                fid = _pending_photo.get(chat_id)
                if fid:
                    await _do_query(tg, chat_id, fid, None)
                else:
                    await tg.send_message(chat_id, "Envíame primero una foto. 📷")
            elif data.startswith("src:"):
                src = data.split(":", 1)[1]
                fid = _pending_photo.get(chat_id)
                if src in _VALID_SOURCES and fid:
                    await _do_register(tg, chat_id, fid, src)
                else:
                    await tg.send_message(chat_id, "Envíame primero una foto. 📷")
            elif data.startswith("fb:"):
                await _handle_feedback(tg, chat_id, data.split(":", 1)[1])
    except Exception:  # noqa: BLE001 — un error no debe tumbar el webhook
        log.exception("Error procesando update de Telegram")
