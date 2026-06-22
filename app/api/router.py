"""Endpoints del pipeline: registro, consulta y feedback."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AINotConfiguredError
from app.db.session import get_session
from app.schemas import (
    FeedbackRequest,
    ProfileCreate,
    ProfileOut,
    QueryResponse,
    RecordOut,
    RegisterResponse,
)
from app.services import pipeline, profiles
from app.storage import get_storage

router = APIRouter()

_VALID_SOURCES = {"receta", "caja", "blister", "pastilla"}
_MAX_BYTES = 15 * 1024 * 1024  # 15 MB
_MIME_BY_EXT = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


async def _read_image(file: UploadFile) -> tuple[bytes, str]:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Imagen vacía.")
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="Imagen demasiado grande (máx 15 MB).")
    mime = file.content_type or "image/jpeg"
    if not mime.startswith("image/"):
        raise HTTPException(status_code=400, detail="El archivo debe ser una imagen.")
    return data, mime


async def _resolve_profile(
    session: AsyncSession, profile_id: uuid.UUID | None
) -> uuid.UUID:
    if profile_id is not None:
        return profile_id
    prof = await profiles.get_or_create_default_profile(session)
    return prof.id


# --- Perfiles -----------------------------------------------------------------


@router.get("/profiles", response_model=list[ProfileOut])
async def get_profiles(session: AsyncSession = Depends(get_session)):
    return await profiles.list_profiles(session)


@router.post("/profiles", response_model=ProfileOut, status_code=201)
async def post_profile(
    body: ProfileCreate, session: AsyncSession = Depends(get_session)
):
    prof = await profiles.create_profile(session, body.display_name, body.relation)
    await session.commit()
    return prof


@router.get("/profiles/{profile_id}/records", response_model=list[RecordOut])
async def get_profile_records(
    profile_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    if await profiles.get_profile(session, profile_id) is None:
        raise HTTPException(status_code=404, detail="Perfil no encontrado.")
    return await profiles.list_records(session, profile_id, min(limit, 200), offset)


# --- Registro -----------------------------------------------------------------


@router.post("/records", response_model=RegisterResponse, status_code=201)
async def post_record(
    image: UploadFile = File(...),
    source_type: str = Form(...),
    profile_id: uuid.UUID | None = Form(None),
    notes: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    if source_type not in _VALID_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"source_type inválido. Use uno de: {sorted(_VALID_SOURCES)}",
        )
    data, mime = await _read_image(image)
    pid = await _resolve_profile(session, profile_id)
    try:
        return await pipeline.register_record(session, pid, data, source_type, mime, notes)
    except AINotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


# --- Consulta -----------------------------------------------------------------


@router.post("/query", response_model=QueryResponse)
async def post_query(
    image: UploadFile = File(...),
    question: str | None = Form(None),
    profile_id: uuid.UUID | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    data, mime = await _read_image(image)
    pid = await _resolve_profile(session, profile_id)
    try:
        return await pipeline.query_medicine(session, pid, data, question, mime)
    except AINotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/query/{query_id}/feedback", status_code=204)
async def post_feedback(
    query_id: uuid.UUID,
    body: FeedbackRequest,
    session: AsyncSession = Depends(get_session),
):
    ok = await pipeline.submit_feedback(session, query_id, body.selected_record_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Consulta no encontrada.")


# --- Imágenes -----------------------------------------------------------------


@router.get("/query/{query_id}/collage")
async def get_query_collage(
    query_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    """Devuelve el collage (JPEG) de las coincidencias de una consulta."""
    data = await pipeline.build_query_collage(session, query_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Consulta sin coincidencias.")
    return Response(content=data, media_type="image/jpeg")


@router.get("/images/{name}")
async def get_image(name: str):
    """Sirve una imagen almacenada (por nombre de archivo) para el frontend."""
    storage = get_storage()
    # name puede venir como ruta completa; nos quedamos con el basename
    fname = name.replace("\\", "/").split("/")[-1]
    if not storage.exists(fname):
        raise HTTPException(status_code=404, detail="Imagen no encontrada.")
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "jpg"
    return Response(content=storage.load(fname), media_type=_MIME_BY_EXT.get(ext, "image/jpeg"))
