"""Visión vía OpenAI: extracción de datos y re-rank visual del Top-K.

No identifica el medicamento de forma global; describe la foto de manera estructurada y
canónica para (a) generar un buen embedding de texto y (b) comparar contra las imágenes
ya registradas del usuario.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from app.ai.client import get_client, image_to_data_url
from app.config import get_settings

# --- Esquemas de salida -------------------------------------------------------


class MedicineExtraction(BaseModel):
    """Campos extraídos de una foto de medicamento/receta."""

    name: str | None = Field(None, description="nombre detectado")
    dose: str | None = Field(None, description="dosis, p.ej. '500mg'")
    lab: str | None = Field(None, description="laboratorio")
    presentation: str | None = Field(None, description="tableta, jarabe, cápsula...")
    form: str | None = Field(None, description="forma física")
    color: str | None = Field(None, description="color predominante")
    visible_text: str | None = Field(None, description="todo el texto legible (OCR)")
    description: str | None = Field(
        None, description="descripción visual canónica: empaque, blíster, distribución"
    )

    def to_descriptor(self) -> str:
        """Texto canónico para embeber. Concatena campos en orden estable."""
        parts = [
            f"nombre: {self.name}" if self.name else "",
            f"dosis: {self.dose}" if self.dose else "",
            f"laboratorio: {self.lab}" if self.lab else "",
            f"presentación: {self.presentation}" if self.presentation else "",
            f"forma: {self.form}" if self.form else "",
            f"color: {self.color}" if self.color else "",
            f"texto: {self.visible_text}" if self.visible_text else "",
            f"descripción: {self.description}" if self.description else "",
        ]
        return " | ".join(p for p in parts if p)


class PrescribedItem(BaseModel):
    name: str | None = Field(None, description="nombre del medicamento recetado")
    dose: str | None = Field(None, description="dosis, p.ej. '500mg'")
    instructions: str | None = Field(None, description="indicación, p.ej. 'cada 8h por 7 días'")


class PrescriptionExtraction(BaseModel):
    patient: str | None = Field(None, description="nombre del paciente a quien va dirigida la receta")
    doctor: str | None = Field(None, description="médico, si es legible")
    items: list[PrescribedItem] = Field(default_factory=list)


class RerankCandidate(BaseModel):
    rank: int
    record_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class RerankResult(BaseModel):
    best_record_id: str | None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    candidates: list[RerankCandidate] = Field(default_factory=list)


# --- Prompts ------------------------------------------------------------------

_EXTRACT_SYSTEM = (
    "Eres un asistente que describe fotos de medicamentos, blísteres, cajas y recetas. "
    "NO afirmes qué medicamento es ni si se puede tomar. Solo describe lo que ves de "
    "forma objetiva y estructurada. Responde SOLO con un objeto JSON con las claves: "
    "name, dose, lab, presentation, form, color, visible_text, description. "
    "Usa null cuando un dato no sea visible. En 'description' detalla empaque, patrón del "
    "blíster, distribución y cantidad de cavidades/pastillas, y colores."
)

_PRESCRIPTION_SYSTEM = (
    "Eres un asistente que lee recetas médicas. Extrae la LISTA de medicamentos recetados "
    "tal como aparecen en el papel. NO inventes: si la receta no es legible o no es una "
    "receta, devuelve items vacío. NO indiques si se pueden tomar. Extrae también el nombre "
    "del PACIENTE a quien va dirigida la receta, si es legible. Responde SOLO con un objeto "
    "JSON: {\"patient\": str|null, \"doctor\": str|null, \"items\": [{\"name\": str|null, "
    "\"dose\": str|null, \"instructions\": str|null}]}."
)

_RERANK_SYSTEM = (
    "Eres un asistente que compara una foto de consulta contra varias fotos candidatas "
    "ya registradas por el usuario. Determina a cuál se parece más por empaque, color, "
    "forma y texto visible. NO afirmes identidad ni aptitud de consumo. Responde SOLO con "
    "un objeto JSON: {\"best_record_id\": str|null, \"confidence\": 0..1, "
    "\"candidates\": [{\"rank\": int, \"record_id\": str, \"confidence\": 0..1, "
    "\"reason\": str}]}."
)


# --- API ----------------------------------------------------------------------


async def extract_medicine_info(image: bytes, mime: str = "image/jpeg") -> MedicineExtraction:
    """Extrae campos estructurados de una foto. 1 llamada a Vision."""
    settings = get_settings()
    client = get_client()
    resp = await client.chat.completions.create(
        model=settings.vision_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe esta imagen."},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_to_data_url(image, mime)},
                    },
                ],
            },
        ],
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    return MedicineExtraction.model_validate(data)


async def extract_prescription(image: bytes, mime: str = "image/jpeg") -> PrescriptionExtraction:
    """Extrae la lista de medicamentos recetados de una foto de receta. 1 llamada a Vision."""
    settings = get_settings()
    client = get_client()
    resp = await client.chat.completions.create(
        model=settings.vision_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _PRESCRIPTION_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extrae los medicamentos recetados de esta receta."},
                    {"type": "image_url", "image_url": {"url": image_to_data_url(image, mime)}},
                ],
            },
        ],
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    return PrescriptionExtraction.model_validate(data)


async def compare_candidates(
    query_image: bytes,
    candidates: list[tuple[str, bytes]],
    mime: str = "image/jpeg",
) -> RerankResult:
    """Re-rank visual: compara la foto de consulta contra las candidatas del Top-K.

    candidates: lista de (record_id, image_bytes). 1 llamada a Vision.
    """
    client = get_client()
    settings = get_settings()

    content: list[dict] = [
        {"type": "text", "text": "FOTO DE CONSULTA:"},
        {"type": "image_url", "image_url": {"url": image_to_data_url(query_image, mime)}},
    ]
    for record_id, img in candidates:
        content.append({"type": "text", "text": f"CANDIDATA record_id={record_id}:"})
        content.append(
            {"type": "image_url", "image_url": {"url": image_to_data_url(img, mime)}}
        )

    resp = await client.chat.completions.create(
        model=settings.vision_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _RERANK_SYSTEM},
            {"role": "user", "content": content},
        ],
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    return RerankResult.model_validate(data)
