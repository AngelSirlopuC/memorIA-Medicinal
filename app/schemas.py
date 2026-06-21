"""Schemas de request/response de la API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

DISCLAIMER = (
    "Posible coincidencia. Verifica la fecha de vencimiento y consulta con un "
    "profesional de salud. Este sistema no identifica medicamentos ni indica si se "
    "pueden tomar."
)


# --- Perfiles -----------------------------------------------------------------


class ProfileCreate(BaseModel):
    display_name: str
    relation: str | None = None


class ProfileOut(BaseModel):
    id: uuid.UUID
    display_name: str
    relation: str | None = None
    created_at: datetime


# --- Registro -----------------------------------------------------------------


class RegisterResponse(BaseModel):
    record_id: uuid.UUID
    profile_id: uuid.UUID
    source_type: str
    medicine_id: uuid.UUID | None = None
    name: str | None = None
    dose: str | None = None
    visible_text: str | None = None
    ai_description: str | None = None
    image_url: str
    deduplicated: bool = Field(
        False, description="True si la imagen ya existía (mismo hash) en el registro"
    )
    registered_at: datetime


# --- Consulta -----------------------------------------------------------------


class QueryCandidate(BaseModel):
    record_id: uuid.UUID
    rank: int
    name: str | None = None
    image_url: str | None = None
    registered_at: datetime | None = None
    vector_score: float | None = None
    vision_confidence: float | None = None
    reason: str | None = None


class QueryResponse(BaseModel):
    query_id: uuid.UUID
    best_record_id: uuid.UUID | None = None
    confidence: float = 0.0
    candidates: list[QueryCandidate] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


class FeedbackRequest(BaseModel):
    selected_record_id: uuid.UUID | None = Field(
        None, description="Record elegido por el usuario; null = 'ninguna'"
    )
