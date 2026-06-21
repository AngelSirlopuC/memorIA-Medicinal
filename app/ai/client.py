"""Cliente OpenAI compartido y utilidades de imagen."""

from __future__ import annotations

import base64
from functools import lru_cache

from openai import AsyncOpenAI

from app.config import get_settings


class AINotConfiguredError(RuntimeError):
    """Se lanza cuando se intenta usar IA sin OPENAI_API_KEY configurada."""


@lru_cache
def get_client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise AINotConfiguredError(
            "OPENAI_API_KEY no está configurada. Defínela en .env para habilitar la IA."
        )
    return AsyncOpenAI(api_key=settings.openai_api_key)


def image_to_data_url(data: bytes, mime: str = "image/jpeg") -> str:
    """Convierte bytes de imagen a un data URL para la API de Vision."""
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"
