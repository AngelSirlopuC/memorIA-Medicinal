"""Embeddings de texto vía OpenAI (text-embedding-3-small, 1536 dims)."""

from __future__ import annotations

from app.ai.client import get_client
from app.config import get_settings


async def embed_text(text: str) -> list[float]:
    """Devuelve el vector de embedding del texto dado.

    El texto es el descriptor canónico de la foto (campos + descripción visual),
    no una frase libre, para maximizar la señal discriminante.
    """
    settings = get_settings()
    client = get_client()
    # text-embedding-3 no admite cadena vacía
    cleaned = text.strip() or "medicamento sin descripción"
    resp = await client.embeddings.create(model=settings.embed_model, input=cleaned)
    return resp.data[0].embedding
