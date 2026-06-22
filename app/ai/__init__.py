"""Módulo de IA (modo OpenAI-first).

Tres capacidades, todas vía la API de OpenAI (sin modelos locales):
- vision.extract_medicine_info(): describe y extrae campos de una foto.
- vision.compare_candidates(): re-rank visual del Top-K sobre las imágenes reales.
- embeddings.embed_text(): vector 1536 (text-embedding-3-small) para pgvector.
"""

from app.ai.embeddings import embed_text
from app.ai.vision import (
    MedicineExtraction,
    PrescriptionExtraction,
    RerankResult,
    compare_candidates,
    extract_medicine_info,
    extract_prescription,
)

MODEL_VERSION = "gpt-5-mini+te3small@1"

__all__ = [
    "embed_text",
    "extract_medicine_info",
    "extract_prescription",
    "compare_candidates",
    "MedicineExtraction",
    "PrescriptionExtraction",
    "RerankResult",
    "MODEL_VERSION",
]
