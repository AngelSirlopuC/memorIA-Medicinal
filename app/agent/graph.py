"""Grafo de estados del agente conversacional (LangGraph + OpenAI tool-calling)."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app import ai
from app.ai.client import AINotConfiguredError, get_client
from app.config import get_settings
from app.db.session import SessionLocal
from app.schemas import QueryResponse
from app.services import pipeline, prescriptions, profiles
from app.storage import get_storage

log = logging.getLogger("agent")

_VALID_SOURCES = {"receta", "caja", "blister", "pastilla"}


# --- Estado de conversación (persistente en memoria) --------------------------


@dataclass
class ConversationState:
    # Persona activa (segmento de búsqueda)
    profile_id: str | None = None
    profile_name: str | None = None
    # Receta que se está cargando (acepta fotos de medicinas)
    open_prescription_id: str | None = None
    prescription_count: int = 0
    # Tema en foco: el sujeto concreto de la conversación
    focus_prescription_id: str | None = None
    focus_prescription_label: str | None = None
    focus_record_id: str | None = None
    focus_record_name: str | None = None
    # Lista numerada pendiente de elección (desambiguación de búsquedas)
    pending_kind: str | None = None         # "prescription" | "record"
    pending_choices: list = field(default_factory=list)  # [{"id","label"}]
    # Memoria mínima
    recent_records: list = field(default_factory=list)  # [{"id","name"}] medicinas del hilo
    history: list = field(default_factory=list)         # [{"role","content"}] pocos mensajes

    def clear_focus(self) -> None:
        """Cierra el tema actual; mantiene la persona activa."""
        self.open_prescription_id = None
        self.prescription_count = 0
        self.focus_prescription_id = None
        self.focus_prescription_label = None
        self.focus_record_id = None
        self.focus_record_name = None
        self.pending_kind = None
        self.pending_choices = []
        self.recent_records.clear()
        self.history.clear()


_MESSAGES_LIMIT = 6   # contexto liviano: pocos mensajes recientes
_RECENT_LIMIT = 6     # cuántas medicinas recientes recordamos


_conversations: dict[str, ConversationState] = {}


def reset_conversation(conversation_id: str) -> None:
    _conversations.pop(conversation_id, None)


def set_conversation_profile(
    conversation_id: str, profile_id: str, name: str | None = None
) -> None:
    """Fija el perfil activo de una conversación (lo usan la web y los canales)."""
    conv = _conversations.setdefault(conversation_id, ConversationState())
    conv.profile_id = str(profile_id)
    if name is not None:
        conv.profile_name = name


def get_conversation_profile(conversation_id: str) -> tuple[str | None, str | None]:
    conv = _conversations.get(conversation_id)
    return (conv.profile_id, conv.profile_name) if conv else (None, None)


# --- Respuesta del agente -----------------------------------------------------


@dataclass
class AgentReply:
    texts: list[str] = field(default_factory=list)
    query: QueryResponse | None = None  # cuando hubo una consulta (para render de candidatos)
    profile_name: str | None = None
    prescription_open: bool = False

    def say(self, text: str) -> None:
        self.texts.append(text)


@dataclass
class AgentContext:
    image_bytes: bytes | None
    mime: str
    conv: ConversationState
    reply: AgentReply
    session: object  # AsyncSession


class AgentStateT(TypedDict, total=False):
    text: str
    has_image: bool
    decisions: list


# --- Definición de herramientas (function calling) ----------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_profile",
            "description": "Cambia el perfil activo (la persona) al que se asocian registros y consultas.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "nombre de la persona"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_prescription",
            "description": (
                "Abre una receta/cita médica para agrupar varias medicinas. Úsalo cuando el "
                "usuario describe una visita médica o una receta (p. ej. 'hoy Thiago tuvo cita, "
                "le recetaron esto'). Si hay una foto adjunta, se guarda como la foto de la receta."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "for_person": {"type": "string", "description": "persona de la receta (opcional)"},
                    "doctor": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_medicine",
            "description": (
                "Registra la foto adjunta como una medicina. Si hay una receta abierta, la agrega "
                "a esa receta. Requiere una foto en el mensaje."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_type": {
                        "type": "string",
                        "enum": ["receta", "caja", "blister", "pastilla"],
                        "description": "tipo de foto (por defecto 'pastilla')",
                    },
                    "for_person": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_medicine",
            "description": (
                "Busca a cuál de los medicamentos ya registrados se parece la foto adjunta "
                "(p. ej. '¿cuándo compré esta pastilla?'). Requiere una foto en el mensaje."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "for_person": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_prescription",
            "description": "Cierra la receta abierta cuando el usuario termina de agregar medicinas.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_last_prescription",
            "description": (
                "Consulta la ÚLTIMA receta de una persona: qué le recetaron (según la foto) y "
                "qué medicinas se registraron. Úsalo para preguntas como '¿qué le recetaron a "
                "Titular en la última receta?' o 'consultar la última receta'. NO requiere foto."
            ),
            "parameters": {
                "type": "object",
                "properties": {"for_person": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_prescription",
            "description": (
                "Busca una receta específica por persona y/o fecha (p. ej. 'la receta de Miguel "
                "del 8 de junio'). Normaliza la fecha a year/month/day numéricos. Si hay varias, "
                "el sistema las lista para elegir. NO requiere foto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "for_person": {"type": "string"},
                    "year": {"type": "integer"},
                    "month": {"type": "integer"},
                    "day": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_medicine",
            "description": (
                "Busca una medicina ya registrada por nombre y/o fecha y/o persona (p. ej. 'la "
                "Amoxicilina de marzo de Thiago'). Normaliza la fecha a year/month/day. Si hay "
                "varias, el sistema las lista. NO requiere foto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "nombre del medicamento"},
                    "for_person": {"type": "string"},
                    "year": {"type": "integer"},
                    "month": {"type": "integer"},
                    "day": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "choose_option",
            "description": (
                "Cuando mostraste una lista numerada (recetas o medicinas) y el usuario elige una "
                "('el 2', 'la segunda', 'la primera', 'número 3'), selecciónala por su posición."
            ),
            "parameters": {
                "type": "object",
                "properties": {"number": {"type": "integer", "description": "posición 1-based en la lista"}},
                "required": ["number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_medicine",
            "description": (
                "Actualiza una medicina YA registrada en esta conversación: fecha de "
                "vencimiento, lote o una nota. Identifica cuál por su nombre (o por el "
                "historial si el usuario dijo 'este/esa'). NO requiere foto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "nombre de la medicina a actualizar; omite si solo hay una"},
                    "expiry": {"type": "string", "description": "vencimiento, p.ej. '08/2026'"},
                    "lot": {"type": "string", "description": "número de lote"},
                    "note": {"type": "string", "description": "nota libre"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_conversation",
            "description": (
                "Cierra el tema actual y limpia el contexto de la conversación cuando el "
                "usuario terminó (dice 'listo', 'gracias', 'nada más', 'cerrar') y NO hay una "
                "receta abierta. Mantiene la persona activa."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reply",
            "description": "Responde con un mensaje de texto cuando ninguna otra acción aplica (saludos, dudas, pedir una foto).",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
]


def _system_prompt(ctx: AgentContext) -> str:
    conv = ctx.conv
    presc = (
        f"sí, abierta para {conv.profile_name or 'la persona activa'} con {conv.prescription_count} medicina(s)"
        if conv.open_prescription_id
        else "no"
    )
    recent = (
        ", ".join(r["name"] for r in conv.recent_records if r.get("name"))
        or "ninguna todavía"
    )
    if conv.focus_record_name:
        foco = f"medicina '{conv.focus_record_name}'"
    elif conv.focus_prescription_label:
        foco = conv.focus_prescription_label
    else:
        foco = "ninguno aún"
    return (
        "Eres el asistente de MemorIA Medicinal, una memoria de medicamentos. Hablas en "
        "español, cálido y MUY breve. Decides UNA o más acciones llamando herramientas.\n\n"
        "Tu objetivo es identificar el SUJETO concreto del que se habla (una medicina, una "
        "receta o una persona) usando nombre, fecha o el titular, y razonar siempre sobre "
        "ese tema en foco, segmentando la búsqueda por la persona activa.\n\n"
        f"Contexto actual:\n"
        f"- Persona activa: {conv.profile_name or 'por defecto'}\n"
        f"- Tema en foco: {foco}\n"
        f"- Receta abierta (acepta fotos de medicinas): {presc}\n"
        f"- Medicinas registradas en esta conversación: {recent}\n"
        f"- ¿Hay una foto adjunta en este mensaje?: {'sí' if ctx.image_bytes else 'no'}\n\n"
        "Reglas:\n"
        "- Si el usuario describe una cita/receta médica, usa start_prescription (incluye la "
        "persona en for_person si la menciona).\n"
        "- Si hay una receta abierta y llega una foto de una medicina, usa add_medicine.\n"
        "- Si pregunta a qué se parece una foto o cuándo la compró, usa query_medicine.\n"
        "- Si llega una foto sin contexto y no hay receta abierta, y no queda claro si es para "
        "registrar o consultar, usa reply para preguntar amablemente.\n"
        "- Si el usuario pregunta por la ÚLTIMA receta (sin foto), usa get_last_prescription.\n"
        "- Si pide una receta o medicina ESPECÍFICA por nombre o fecha (p. ej. 'la receta del 8 "
        "de junio', 'la Amoxicilina de marzo'), usa find_prescription o find_medicine y "
        "normaliza la fecha a year/month/day numéricos.\n"
        "- Si el usuario da un dato sobre una medicina ya registrada (vencimiento, lote, una "
        "nota), usa update_medicine; identifica cuál por el historial y las medicinas recientes. "
        "Si hay varias y no queda claro, pregunta con reply.\n"
        "- Nunca afirmes qué medicamento es ni si se puede tomar.\n"
        "- add_medicine y query_medicine SIEMPRE requieren una foto. get_last_prescription y "
        "update_medicine NO requieren foto.\n"
        "Cierre:\n"
        "- Al terminar una acción o responder una consulta, pregunta brevemente si desea seguir "
        "con el mismo tema/persona o cerrar.\n"
        "- Si el usuario indica que terminó (listo, gracias, nada más, cerrar) y NO hay una "
        "receta abierta, usa end_conversation para limpiar el contexto.\n"
        "- Si hay una receta abierta y dice 'listo', usa close_prescription."
    )


# --- Nodo router (decisión vía LLM) -------------------------------------------


def _user_turn_text(state: AgentStateT) -> str:
    text = (state.get("text") or "").strip()
    if state.get("has_image"):
        return (f"{text}\n[el usuario adjuntó una foto]" if text else "[el usuario adjuntó una foto sin texto]")
    return text or "(mensaje vacío)"


async def _route(state: AgentStateT, ctx: AgentContext) -> list:
    settings = get_settings()
    client = get_client()
    history = ctx.conv.history[-_MESSAGES_LIMIT:]
    messages = [{"role": "system", "content": _system_prompt(ctx)}]
    messages.extend(history)
    messages.append({"role": "user", "content": _user_turn_text(state)})
    resp = await client.chat.completions.create(
        model=settings.vision_model,
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
    )
    msg = resp.choices[0].message
    decisions: list = []
    for tc in msg.tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        decisions.append({"name": tc.function.name, "args": args})
    if not decisions:
        decisions.append({"name": "reply", "args": {"text": msg.content or "¿En qué te ayudo?"}})
    return decisions


async def router_node(state: AgentStateT, config) -> dict:
    ctx: AgentContext = config["configurable"]["ctx"]
    try:
        decisions = await _route(state, ctx)
    except AINotConfiguredError:
        ctx.reply.say("⚠️ La IA no está configurada (falta OPENAI_API_KEY).")
        decisions = []
    return {"decisions": decisions}


# --- Nodo ejecutor ------------------------------------------------------------


async def _resolve_profile(ctx: AgentContext, for_person: str | None = None):
    session = ctx.session
    if for_person:
        prof = await profiles.get_or_create_by_name(session, for_person)
        await session.commit()
        ctx.conv.profile_id, ctx.conv.profile_name = str(prof.id), prof.display_name
        return prof.id, prof.display_name
    if ctx.conv.profile_id:
        prof = await profiles.get_profile(session, uuid.UUID(ctx.conv.profile_id))
        if prof:
            ctx.conv.profile_name = prof.display_name
            return prof.id, prof.display_name
    prof = await profiles.get_or_create_default_profile(session)
    await session.commit()
    ctx.conv.profile_id, ctx.conv.profile_name = str(prof.id), prof.display_name
    return prof.id, prof.display_name


async def _h_set_profile(ctx: AgentContext, args: dict) -> None:
    name = (args.get("name") or "").strip()
    if not name:
        ctx.reply.say("¿Para qué persona?")
        return
    _, pname = await _resolve_profile(ctx, name)
    ctx.reply.say(f"👤 Listo, ahora registro y consulto para *{pname}*.")


async def _h_start_prescription(ctx: AgentContext, args: dict) -> None:
    image_url = None
    doctor = args.get("doctor")
    items: list = []
    patient = None
    if ctx.image_bytes:
        storage = get_storage()
        image_url = storage.save(ctx.image_bytes, f"{uuid.uuid4().hex}.jpg")
        # OCR de la receta: lista de lo recetado + paciente + médico
        try:
            ext = await ai.extract_prescription(ctx.image_bytes, ctx.mime)
            items = [it.model_dump() for it in ext.items]
            doctor = doctor or ext.doctor
            patient = ext.patient
        except AINotConfiguredError:
            pass
        except Exception:  # noqa: BLE001 — si el OCR falla, seguimos sin lista
            log.exception("Error extrayendo la receta")

    # Segmentación por persona: prioriza lo que dijo el usuario; si no, el paciente del OCR
    person = args.get("for_person") or patient
    pid, pname = await _resolve_profile(ctx, person)
    auto = bool(patient) and not args.get("for_person")

    pres = await prescriptions.create_prescription(
        ctx.session, pid, doctor=doctor, notes=args.get("notes"), image_url=image_url, items=items
    )
    await ctx.session.commit()
    ctx.conv.open_prescription_id = str(pres.id)
    ctx.conv.prescription_count = 0
    ctx.conv.focus_prescription_id = str(pres.id)
    ctx.conv.focus_prescription_label = f"receta de {pname}"
    ctx.conv.focus_record_id = None
    ctx.conv.focus_record_name = None

    extra = f" con el Dr. {doctor}" if doctor else ""
    if auto:
        ctx.reply.say(f"🧾 Detecté que la receta es para *{pname}*; la registro en su perfil.")
    ctx.reply.say(
        f"📋 Abrí la receta de *{pname}*{extra}. Mándame una foto de cada medicina y te las "
        "voy agregando. Cuando termines, dime *listo*."
    )
    if items:
        listed = "\n".join(
            f"• {it.get('name') or 'medicamento'}"
            + (f" {it['dose']}" if it.get("dose") else "")
            + (f" — {it['instructions']}" if it.get("instructions") else "")
            for it in items
        )
        ctx.reply.say(f"📝 Leí esto en la receta:\n{listed}\n\nSi algo está mal, dímelo.")


async def _h_add_medicine(ctx: AgentContext, args: dict) -> None:
    if not ctx.image_bytes:
        ctx.reply.say("Mándame la *foto* de la medicina y la agrego. 📷")
        return
    pid, pname = await _resolve_profile(ctx, args.get("for_person"))
    source = args.get("source_type")
    source = source if source in _VALID_SOURCES else "pastilla"
    pres_id = uuid.UUID(ctx.conv.open_prescription_id) if ctx.conv.open_prescription_id else None
    try:
        r = await pipeline.register_record(
            ctx.session, pid, ctx.image_bytes, source, ctx.mime, prescription_id=pres_id
        )
    except AINotConfiguredError:
        ctx.reply.say("⚠️ La IA no está configurada (falta OPENAI_API_KEY).")
        return
    name = (f"*{r.name}*" + (f" {r.dose}" if r.dose else "")) if r.name else "la medicina"
    if r.deduplicated:
        ctx.reply.say("Esa foto ya estaba registrada. ✓")
        return
    # Recuerda la medicina para resolver referencias posteriores ("este vence...")
    ctx.conv.recent_records.append({"id": str(r.record_id), "name": r.name or "medicina"})
    if len(ctx.conv.recent_records) > _RECENT_LIMIT:
        ctx.conv.recent_records[:] = ctx.conv.recent_records[-_RECENT_LIMIT:]
    # La medicina recién registrada pasa a ser el tema en foco
    ctx.conv.focus_record_id = str(r.record_id)
    ctx.conv.focus_record_name = r.name
    if ctx.conv.open_prescription_id:
        ctx.conv.prescription_count += 1
        ctx.reply.say(
            f"✅ Agregué {name} a la receta de *{pname}*. Van {ctx.conv.prescription_count} "
            "medicina(s). ¿Otra más o digo *listo*?"
        )
    else:
        ctx.reply.say(f"✅ Registré {name} para *{pname}*.")


async def _h_query(ctx: AgentContext, args: dict) -> None:
    if not ctx.image_bytes:
        ctx.reply.say("Mándame la *foto* del medicamento que quieres consultar. 📷")
        return
    pid, pname = await _resolve_profile(ctx, args.get("for_person"))
    try:
        r = await pipeline.query_medicine(
            ctx.session, pid, ctx.image_bytes, args.get("question"), ctx.mime
        )
    except AINotConfiguredError:
        ctx.reply.say("⚠️ La IA no está configurada (falta OPENAI_API_KEY).")
        return
    if not r.candidates:
        ctx.reply.say(
            f"No encontré coincidencias en el historial de *{pname}* todavía. "
            "Regístralo para reconocerlo después."
        )
        return
    ctx.reply.query = r
    best = r.candidates[0]
    ctx.conv.focus_record_id = str(best.record_id)
    ctx.conv.focus_record_name = best.name
    ctx.reply.say(f"🔎 Encontré {len(r.candidates)} posible(s) coincidencia(s) para *{pname}*:")


async def _h_close_prescription(ctx: AgentContext, args: dict) -> None:
    if not ctx.conv.open_prescription_id:
        ctx.reply.say("No hay una receta abierta ahora mismo.")
        return
    n = await prescriptions.count_medicines(ctx.session, uuid.UUID(ctx.conv.open_prescription_id))
    await prescriptions.close_prescription(ctx.session, uuid.UUID(ctx.conv.open_prescription_id))
    pname = ctx.conv.profile_name or "la persona"
    ctx.conv.open_prescription_id = None
    ctx.conv.prescription_count = 0
    ctx.reply.say(
        f"📋 Cerré la receta de *{pname}* con {n} medicina(s). ¿Algo más sobre {pname} o "
        "cerramos? (di *listo* para cerrar)"
    )


async def _h_choose_option(ctx: AgentContext, args: dict) -> None:
    n = _int(args.get("number"))
    choices = ctx.conv.pending_choices
    if not choices:
        ctx.reply.say("No tengo una lista activa para elegir. ¿Buscamos de nuevo?")
        return
    if not n or n < 1 or n > len(choices):
        ctx.reply.say(f"Elige un número entre 1 y {len(choices)}.")
        return
    choice = choices[n - 1]
    pname = ctx.conv.profile_name or "la persona"
    if ctx.conv.pending_kind == "prescription":
        pres = await prescriptions.get_prescription(ctx.session, uuid.UUID(choice["id"]))
        if pres is None:
            ctx.reply.say("Esa receta ya no existe.")
            return
        await _say_prescription(ctx, pres, pname)
    else:  # record
        rec, med = await pipeline.get_record(ctx.session, uuid.UUID(choice["id"]))
        if rec is None:
            ctx.reply.say("Ese registro ya no existe.")
            return
        _say_record(ctx, rec, med)
    ctx.conv.pending_kind = None
    ctx.conv.pending_choices = []


async def _h_end_conversation(ctx: AgentContext, args: dict) -> None:
    ctx.conv.clear_focus()
    ctx.reply.say("🧹 Listo, cerré el tema. Cuando quieras, empezamos de nuevo. 👋")


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _pres_date(pres) -> str:
    d = pres.prescribed_at or (pres.created_at.date() if pres.created_at else None)
    return d.strftime("%d/%m/%Y") if d else ""


async def _say_prescription(ctx: AgentContext, pres, pname: str, title: str = "Receta") -> None:
    """Muestra una receta (recetado + medicinas registradas) y la deja en foco."""
    when = _pres_date(pres)
    ctx.conv.focus_prescription_id = str(pres.id)
    ctx.conv.focus_prescription_label = f"receta de {pname}" + (f" ({when})" if when else "")
    ctx.conv.focus_record_id = None
    ctx.conv.focus_record_name = None

    header = f"📋 {title} de *{pname}*"
    if pres.doctor:
        header += f" (Dr. {pres.doctor})"
    if when:
        header += f" — {when}"
    ctx.reply.say(header + ":")

    if pres.items:
        listed = "\n".join(
            f"• {it.get('name') or 'medicamento'}"
            + (f" {it['dose']}" if it.get("dose") else "")
            + (f" — {it['instructions']}" if it.get("instructions") else "")
            for it in pres.items
        )
        ctx.reply.say(f"*Recetado:*\n{listed}")
    else:
        ctx.reply.say("_No tengo la lista de lo recetado (la foto no se pudo leer o no se cargó)._")

    meds = await prescriptions.list_medicines(ctx.session, pres.id)
    if meds:
        reg = "\n".join(
            f"• {(n or 'medicina').title()}" + (f" {d}" if d else "") for n, d in meds
        )
        ctx.reply.say(f"*Medicinas que registraste:*\n{reg}")
    else:
        ctx.reply.say("_Aún no registraste medicinas en esta receta._")
    ctx.reply.say("¿Quieres seguir con esta receta o cerramos? (di *listo* para cerrar)")


def _say_record(ctx: AgentContext, rec, med) -> None:
    """Muestra una medicina registrada y la deja en foco."""
    name = (med.name_normalized.title() if med and med.name_normalized else "Medicina")
    ctx.conv.focus_record_id = str(rec.id)
    ctx.conv.focus_record_name = name
    when = rec.registered_at.strftime("%d/%m/%Y") if rec.registered_at else ""
    line = f"💊 *{name}*" + (f" {med.dose}" if med and med.dose else "")
    detalles = []
    if when:
        detalles.append(f"registrada {when}")
    if rec.expiry:
        detalles.append(f"vence {rec.expiry}")
    if rec.lot_number:
        detalles.append(f"lote {rec.lot_number}")
    if detalles:
        line += " — " + ", ".join(detalles)
    ctx.reply.say(line)
    if rec.notes:
        ctx.reply.say(f"_Nota: {rec.notes}_")
    ctx.reply.say("¿Algo más sobre esta medicina o cerramos? (di *listo* para cerrar)")


async def _h_get_last_prescription(ctx: AgentContext, args: dict) -> None:
    pid, pname = await _resolve_profile(ctx, args.get("for_person"))
    pres = await prescriptions.get_last_prescription(ctx.session, pid)
    if pres is None:
        ctx.reply.say(f"No encontré recetas registradas para *{pname}* todavía.")
        return
    await _say_prescription(ctx, pres, pname, title="Última receta")


async def _h_find_prescription(ctx: AgentContext, args: dict) -> None:
    pid, pname = await _resolve_profile(ctx, args.get("for_person"))
    found = await prescriptions.search_prescriptions(
        ctx.session, pid, _int(args.get("year")), _int(args.get("month")), _int(args.get("day"))
    )
    if not found:
        ctx.reply.say(f"No encontré recetas de *{pname}* con esos datos.")
        return
    if len(found) == 1:
        ctx.conv.pending_kind = None
        ctx.conv.pending_choices = []
        await _say_prescription(ctx, found[0], pname)
        return
    ctx.conv.pending_kind = "prescription"
    ctx.conv.pending_choices = [{"id": str(p.id), "label": _pres_date(p)} for p in found]
    lines = [f"Encontré {len(found)} recetas de *{pname}*:"]
    for i, p in enumerate(found, 1):
        doc = f" · Dr. {p.doctor}" if p.doctor else ""
        lines.append(f"{i}. {_pres_date(p)}{doc} — {len(p.items or [])} medicamento(s)")
    lines.append("¿Cuál? Dime el *número*, la fecha o el médico.")
    ctx.reply.say("\n".join(lines))


async def _h_find_medicine(ctx: AgentContext, args: dict) -> None:
    pid, pname = await _resolve_profile(ctx, args.get("for_person"))
    rows = await pipeline.search_records(
        ctx.session, pid, args.get("name"),
        _int(args.get("year")), _int(args.get("month")), _int(args.get("day")),
    )
    if not rows:
        ctx.reply.say(f"No encontré medicinas de *{pname}* con esos datos.")
        return
    if len(rows) == 1:
        ctx.conv.pending_kind = None
        ctx.conv.pending_choices = []
        rec, med = rows[0]
        _say_record(ctx, rec, med)
        return
    ctx.conv.pending_kind = "record"
    ctx.conv.pending_choices = []
    lines = [f"Encontré {len(rows)} medicinas de *{pname}*:"]
    for i, (rec, med) in enumerate(rows, 1):
        nm = (med.name_normalized.title() if med and med.name_normalized else "Medicina")
        when = rec.registered_at.strftime("%d/%m/%Y") if rec.registered_at else ""
        ctx.conv.pending_choices.append({"id": str(rec.id), "label": nm})
        lines.append(f"{i}. {nm}" + (f" {med.dose}" if med and med.dose else "") + (f" — {when}" if when else ""))
    lines.append("¿Cuál? Dime el *número*, el nombre o la fecha.")
    ctx.reply.say("\n".join(lines))


def _match_recent(recent: list, name: str | None) -> dict | None:
    """Resuelve a qué medicina reciente se refiere el usuario."""
    if not recent:
        return None
    if name:
        low = name.strip().lower()
        for r in reversed(recent):  # la más reciente primero
            rn = (r.get("name") or "").lower()
            if rn and (low in rn or rn in low):
                return r
        return None
    if len(recent) == 1:
        return recent[0]
    return None  # ambiguo: hay varias y no dijo cuál


async def _h_update_medicine(ctx: AgentContext, args: dict) -> None:
    recent = ctx.conv.recent_records
    if not recent:
        ctx.reply.say("No tengo una medicina reciente en esta conversación para actualizar. ¿Cuál es?")
        return
    target = _match_recent(recent, args.get("name"))
    if target is None:
        nombres = ", ".join(r["name"] for r in recent if r.get("name"))
        ctx.reply.say(f"¿A cuál de estas te refieres?: {nombres}")
        return
    ok = await pipeline.update_record(
        ctx.session,
        uuid.UUID(target["id"]),
        expiry=args.get("expiry"),
        lot_number=args.get("lot"),
        note=args.get("note"),
    )
    if not ok:
        ctx.reply.say("No pude encontrar ese registro para actualizarlo.")
        return
    detalles = []
    if args.get("expiry"):
        detalles.append(f"vence {args['expiry']}")
    if args.get("lot"):
        detalles.append(f"lote {args['lot']}")
    if args.get("note"):
        detalles.append(f"nota: {args['note']}")
    extra = (" (" + ", ".join(detalles) + ")") if detalles else ""
    ctx.reply.say(f"✅ Actualicé *{target['name']}*{extra}.")


_HANDLERS = {
    "set_profile": _h_set_profile,
    "start_prescription": _h_start_prescription,
    "add_medicine": _h_add_medicine,
    "query_medicine": _h_query,
    "close_prescription": _h_close_prescription,
    "get_last_prescription": _h_get_last_prescription,
    "find_prescription": _h_find_prescription,
    "find_medicine": _h_find_medicine,
    "choose_option": _h_choose_option,
    "update_medicine": _h_update_medicine,
    "end_conversation": _h_end_conversation,
}


async def execute_node(state: AgentStateT, config) -> dict:
    ctx: AgentContext = config["configurable"]["ctx"]
    for decision in state.get("decisions", []):
        name, args = decision.get("name"), decision.get("args", {})
        if name == "reply":
            ctx.reply.say(args.get("text") or "¿En qué te ayudo?")
            continue
        handler = _HANDLERS.get(name)
        if handler:
            try:
                await handler(ctx, args)
            except Exception:  # noqa: BLE001
                log.exception("Error ejecutando %s", name)
                ctx.reply.say("Ocurrió un error procesando eso. Intenta de nuevo.")
    ctx.reply.profile_name = ctx.conv.profile_name
    ctx.reply.prescription_open = ctx.conv.open_prescription_id is not None
    return {}


# --- Construcción del grafo ---------------------------------------------------


def _build_graph():
    g = StateGraph(AgentStateT)
    g.add_node("router", router_node)
    g.add_node("execute", execute_node)
    g.add_edge(START, "router")
    g.add_edge("router", "execute")
    g.add_edge("execute", END)
    return g.compile()


_graph = _build_graph()


# --- Runner -------------------------------------------------------------------


async def handle_message(
    conversation_id: str,
    text: str | None,
    image_bytes: bytes | None = None,
    mime: str = "image/jpeg",
    profile_id: str | None = None,
) -> AgentReply:
    """Procesa un turno de conversación y devuelve la respuesta del agente."""
    conv = _conversations.setdefault(conversation_id, ConversationState())
    if profile_id:
        conv.profile_id = str(profile_id)
    reply = AgentReply()
    state = {"text": text or "", "has_image": image_bytes is not None}
    async with SessionLocal() as session:
        ctx = AgentContext(image_bytes=image_bytes, mime=mime, conv=conv, reply=reply, session=session)
        await _graph.ainvoke(state, config={"configurable": {"ctx": ctx}})

    # Actualiza la memoria de conversación (turno del usuario + respuestas del agente)
    conv.history.append({"role": "user", "content": _user_turn_text(state)})
    for t in reply.texts:
        conv.history.append({"role": "assistant", "content": t})
    if len(conv.history) > _MESSAGES_LIMIT:
        conv.history[:] = conv.history[-_MESSAGES_LIMIT:]
    return reply
