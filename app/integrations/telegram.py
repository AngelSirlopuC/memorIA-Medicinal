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
from app import agent
from app.db.session import SessionLocal
from app.services import collage, pipeline, profiles  # noqa: F401 (pipeline usado por el agente)
from app.storage import get_storage

log = logging.getLogger("telegram")

_VALID_SOURCES = {"receta", "caja", "blister", "pastilla"}
_SOURCE_LABELS = {"receta": "Receta", "caja": "Caja", "blister": "Blíster", "pastilla": "Pastilla"}

# Estado en memoria (suficiente para un despliegue de un usuario/familia)
_pending_photo: dict[int, str] = {}          # chat_id -> file_id de la última foto
_pending_query: dict[int, dict] = {}         # chat_id -> {"query_id":.., "records":[..]}
_active_profile: dict[int, str] = {}         # chat_id -> profile_id activo
_awaiting_name: set[int] = set()             # chats que están creando un perfil


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
    "Tu memoria de medicamentos. Háblame con naturalidad y envíame *fotos*. Por ejemplo:\n"
    "• _\"Hoy Thiago tuvo cita y le recetaron esto\"_ (adjunta la receta) y luego manda una "
    "foto de cada medicina.\n"
    "• _\"¿Cuándo compré esta pastilla?\"_ con una foto.\n\n"
    "_No identifico medicamentos ni indico si se pueden tomar. Verifica el vencimiento y "
    "consulta con un profesional de salud._\n\n"
    "Usa /perfiles para registrar a varios miembros de la familia."
)


# --- Procesamiento ------------------------------------------------------------


def _profiles_keyboard(profs: list) -> dict:
    rows = [[{"text": f"👤 {p.display_name}", "callback_data": f"prof:{p.id}"}] for p in profs]
    rows.append([{"text": "➕ Nuevo perfil", "callback_data": "prof:new"}])
    return {"inline_keyboard": rows}


async def _send_profiles(tg: TelegramClient, chat_id: int) -> None:
    async with SessionLocal() as session:
        profs = await profiles.list_profiles(session)
    active_id = agent.get_conversation_profile(f"tg:{chat_id}")[0]
    if profs:
        active = next((p.display_name for p in profs if str(p.id) == str(active_id)), "por defecto")
        text = f"👪 Perfil activo: *{active}*.\n\nElige uno o crea otro:"
    else:
        text = "Aún no hay perfiles. Crea el primero:"
    await tg.send_message(chat_id, text, _profiles_keyboard(profs))


async def _send_agent_reply(tg: TelegramClient, chat_id: int, reply, query_image: bytes | None = None) -> None:
    """Envía las respuestas del agente; si hubo consulta, manda collage + feedback."""
    for t in reply.texts:
        await tg.send_message(chat_id, t)
    q = reply.query
    if q and q.candidates:
        _pending_query[chat_id] = {
            "query_id": q.query_id,
            "records": [c.record_id for c in q.candidates],
        }
        keyboard = _feedback_keyboard(len(q.candidates))
        sent = False
        if len(q.candidates) > 1 and query_image:
            try:
                img = collage.collage_for_candidates(get_storage(), query_image, q.candidates)
                await tg.send_photo(chat_id, img, "Elige la opción correcta:", keyboard)
                sent = True
            except Exception:  # noqa: BLE001
                sent = False
        if not sent:
            await tg.send_message(chat_id, "Elige la opción correcta:", keyboard)


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
    """Procesa un update de Telegram (webhook o polling). El agente maneja la conversación."""
    tg = get_client()
    try:
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            cid = f"tg:{chat_id}"

            if "photo" in msg:
                file_id = msg["photo"][-1]["file_id"]  # mayor resolución
                caption = (msg.get("caption") or "").strip()
                image = await tg.download_file(file_id)
                reply = await agent.handle_message(cid, caption or None, image)
                await _send_agent_reply(tg, chat_id, reply, image)
                return

            text = (msg.get("text") or "").strip()
            if chat_id in _awaiting_name:
                _awaiting_name.discard(chat_id)
                name = text[:60] or "Perfil"
                async with SessionLocal() as session:
                    prof = await profiles.create_profile(session, name)
                    await session.commit()
                agent.set_conversation_profile(cid, prof.id, prof.display_name)
                await tg.send_message(chat_id, f"✅ Perfil *{name}* creado y activado.")
            elif text in ("/start", "/help", "start", "help"):
                await tg.send_message(chat_id, WELCOME)
            elif text in ("/perfiles", "/perfil", "perfiles"):
                await _send_profiles(tg, chat_id)
            elif text:
                reply = await agent.handle_message(cid, text, None)
                await _send_agent_reply(tg, chat_id, reply)
            return

        if "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            cid = f"tg:{chat_id}"
            data = cq.get("data", "")
            await tg.answer_callback(cq["id"])

            if data.startswith("fb:"):
                await _handle_feedback(tg, chat_id, data.split(":", 1)[1])
            elif data == "prof:new":
                _awaiting_name.add(chat_id)
                await tg.send_message(chat_id, "Escribe el *nombre* del nuevo perfil (p. ej. _María_).")
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
                    await tg.send_message(chat_id, f"👤 Perfil activo: *{prof.display_name}*.")
                else:
                    await tg.send_message(chat_id, "Ese perfil ya no existe.")
    except Exception:  # noqa: BLE001 — un error no debe tumbar el webhook
        log.exception("Error procesando update de Telegram")
