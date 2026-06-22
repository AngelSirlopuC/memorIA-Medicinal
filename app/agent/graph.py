"""Grafo de estados del agente conversacional (LangGraph + OpenAI tool-calling)."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

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
    profile_id: str | None = None
    profile_name: str | None = None
    open_prescription_id: str | None = None
    prescription_count: int = 0


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
    return (
        "Eres el asistente de MemorIA Medicinal, una memoria de medicamentos. Hablas en "
        "español, cálido y breve. Decides UNA o más acciones llamando herramientas.\n\n"
        f"Contexto actual:\n"
        f"- Perfil activo: {conv.profile_name or 'por defecto'}\n"
        f"- Receta abierta: {presc}\n"
        f"- ¿Hay una foto adjunta en este mensaje?: {'sí' if ctx.image_bytes else 'no'}\n\n"
        "Reglas:\n"
        "- Si el usuario describe una cita/receta médica, usa start_prescription (incluye la "
        "persona en for_person si la menciona).\n"
        "- Si hay una receta abierta y llega una foto de una medicina, usa add_medicine.\n"
        "- Si pregunta a qué se parece una foto o cuándo la compró, usa query_medicine.\n"
        "- Si llega una foto sin contexto y no hay receta abierta, y no queda claro si es para "
        "registrar o consultar, usa reply para preguntar amablemente.\n"
        "- Nunca afirmes qué medicamento es ni si se puede tomar.\n"
        "- No inventes registros: para registrar o consultar SIEMPRE debe haber una foto."
    )


# --- Nodo router (decisión vía LLM) -------------------------------------------


async def _route(state: AgentStateT, ctx: AgentContext) -> list:
    settings = get_settings()
    client = get_client()
    user_text = state.get("text") or ("(envió una foto sin texto)" if state.get("has_image") else "")
    resp = await client.chat.completions.create(
        model=settings.vision_model,
        messages=[
            {"role": "system", "content": _system_prompt(ctx)},
            {"role": "user", "content": user_text or "(mensaje vacío)"},
        ],
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
    pid, pname = await _resolve_profile(ctx, args.get("for_person"))
    image_url = None
    if ctx.image_bytes:
        storage = get_storage()
        image_url = storage.save(ctx.image_bytes, f"{uuid.uuid4().hex}.jpg")
    pres = await prescriptions.create_prescription(
        ctx.session, pid, doctor=args.get("doctor"), notes=args.get("notes"), image_url=image_url
    )
    await ctx.session.commit()
    ctx.conv.open_prescription_id = str(pres.id)
    ctx.conv.prescription_count = 0
    extra = f" con el Dr. {args['doctor']}" if args.get("doctor") else ""
    ctx.reply.say(
        f"📋 Abrí la receta de *{pname}*{extra}. Mándame una foto de cada medicina y te las "
        "voy agregando. Cuando termines, dime *listo*."
    )


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
    ctx.reply.say(f"📋 Cerré la receta de *{pname}* con {n} medicina(s). ¡Listo!")


_HANDLERS = {
    "set_profile": _h_set_profile,
    "start_prescription": _h_start_prescription,
    "add_medicine": _h_add_medicine,
    "query_medicine": _h_query,
    "close_prescription": _h_close_prescription,
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
    async with SessionLocal() as session:
        ctx = AgentContext(image_bytes=image_bytes, mime=mime, conv=conv, reply=reply, session=session)
        await _graph.ainvoke(
            {"text": text or "", "has_image": image_bytes is not None},
            config={"configurable": {"ctx": ctx}},
        )
    return reply
