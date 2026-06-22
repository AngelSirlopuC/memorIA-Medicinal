"""Agente conversacional (Sprint 10).

Un grafo de estados (LangGraph) interpreta cada mensaje (texto + foto opcional) y decide
una acción mediante tool-calling de OpenAI: abrir una receta, agregar una medicina a la
receta abierta, consultar el historial, cambiar de perfil o simplemente responder.

Mantiene estado de conversación por canal/usuario (perfil activo y receta abierta).
"""

from app.agent.graph import (
    AgentReply,
    get_conversation_profile,
    handle_message,
    reset_conversation,
    set_conversation_profile,
)

__all__ = [
    "AgentReply",
    "handle_message",
    "reset_conversation",
    "set_conversation_profile",
    "get_conversation_profile",
]
