"""Pipeline de registro y consulta de medicamentos (modo OpenAI-first).

Registro:  foto → Vision (extracción) → embedding → storage → BD
Consulta:  foto → Vision (descriptor) → embedding → pgvector Top-K →
           Vision (re-rank visual) → respuesta + persistencia del feedback
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai
from app.ai.vision import MedicineExtraction
from app.config import get_settings
from app.db.models import (
    Medicine,
    Query,
    QueryResult,
    Record,
    RecordEmbedding,
    RecordImage,
)
from app.schemas import QueryCandidate, QueryResponse, RegisterResponse
from app.services import collage as collage_svc
from app.storage import get_storage

_EXT_BY_MIME = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp"}


def _filename(mime: str) -> str:
    return f"{uuid.uuid4().hex}.{_EXT_BY_MIME.get(mime, 'jpg')}"


async def _get_or_create_medicine(
    session: AsyncSession, ext: MedicineExtraction
) -> Medicine | None:
    if not ext.name:
        return None
    name_norm = ext.name.strip().lower()
    stmt = select(Medicine).where(Medicine.name_normalized == name_norm)
    if ext.dose:
        stmt = stmt.where(Medicine.dose == ext.dose)
    med = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if med is None:
        med = Medicine(
            name_normalized=name_norm,
            dose=ext.dose,
            lab=ext.lab,
            presentation=ext.presentation,
            form=ext.form,
            color=ext.color,
        )
        session.add(med)
        await session.flush()
    return med


# --- Registro -----------------------------------------------------------------


async def register_record(
    session: AsyncSession,
    profile_id: uuid.UUID,
    image: bytes,
    source_type: str,
    mime: str = "image/jpeg",
    notes: str | None = None,
) -> RegisterResponse:
    storage = get_storage()
    sha = storage.sha256(image)

    # Dedup: misma imagen ya registrada para este perfil → no se vuelve a llamar IA
    existing = (
        await session.execute(
            select(Record, RecordImage)
            .join(RecordImage, RecordImage.record_id == Record.id)
            .where(Record.profile_id == profile_id, RecordImage.sha256 == sha)
            .limit(1)
        )
    ).first()
    if existing is not None:
        rec, img = existing
        return RegisterResponse(
            record_id=rec.id,
            profile_id=rec.profile_id,
            source_type=rec.source_type,
            medicine_id=rec.medicine_id,
            visible_text=rec.visible_text,
            ai_description=rec.ai_description,
            image_url=img.storage_url,
            deduplicated=True,
            registered_at=rec.registered_at,
        )

    ext = await ai.extract_medicine_info(image, mime)
    medicine = await _get_or_create_medicine(session, ext)

    url = storage.save(image, _filename(mime))

    record = Record(
        profile_id=profile_id,
        medicine_id=medicine.id if medicine else None,
        source_type=source_type,
        visible_text=ext.visible_text,
        ai_description=ext.description,
        notes=notes,
    )
    session.add(record)
    await session.flush()

    rec_img = RecordImage(record_id=record.id, storage_url=url, sha256=sha)
    session.add(rec_img)
    await session.flush()

    vec = await ai.embed_text(ext.to_descriptor())
    session.add(
        RecordEmbedding(
            record_image_id=rec_img.id,
            text_embedding=vec,
            model_version=ai.MODEL_VERSION,
        )
    )
    await session.commit()

    return RegisterResponse(
        record_id=record.id,
        profile_id=profile_id,
        source_type=source_type,
        medicine_id=record.medicine_id,
        name=ext.name,
        dose=ext.dose,
        visible_text=ext.visible_text,
        ai_description=ext.description,
        image_url=url,
        deduplicated=False,
        registered_at=record.registered_at,
    )


# --- Consulta -----------------------------------------------------------------


async def query_medicine(
    session: AsyncSession,
    profile_id: uuid.UUID,
    image: bytes,
    question: str | None = None,
    mime: str = "image/jpeg",
) -> QueryResponse:
    settings = get_settings()
    storage = get_storage()

    ext = await ai.extract_medicine_info(image, mime)
    qvec = await ai.embed_text(ext.to_descriptor())

    query_url = storage.save(image, _filename(mime))
    query_row = Query(
        profile_id=profile_id,
        query_image_url=query_url,
        query_embedding=qvec,
        question_text=question,
    )
    session.add(query_row)
    await session.flush()

    # Top-K por similitud coseno sobre text_embedding del propio historial
    dist = RecordEmbedding.text_embedding.cosine_distance(qvec).label("dist")
    rows = (
        await session.execute(
            select(Record, RecordImage, dist)
            .join(RecordImage, RecordImage.record_id == Record.id)
            .join(RecordEmbedding, RecordEmbedding.record_image_id == RecordImage.id)
            .where(
                Record.profile_id == profile_id,
                RecordEmbedding.text_embedding.isnot(None),
            )
            .order_by(dist)
            .limit(settings.vision_rerank_topk)
        )
    ).all()

    if not rows:
        await session.commit()
        return QueryResponse(query_id=query_row.id, candidates=[])

    # Re-rank visual sobre las imágenes reales del Top-K
    candidate_imgs: list[tuple[str, bytes]] = []
    for rec, img, _d in rows:
        try:
            candidate_imgs.append((str(rec.id), storage.load(img.storage_url)))
        except Exception:  # noqa: BLE001 — imagen faltante no debe romper la consulta
            continue

    rerank = await ai.compare_candidates(image, candidate_imgs, mime)
    conf_by_id = {c.record_id: c for c in rerank.candidates}

    candidates: list[QueryCandidate] = []
    for rank, (rec, img, d) in enumerate(rows, start=1):
        rr = conf_by_id.get(str(rec.id))
        vision_conf = rr.confidence if rr else None
        session.add(
            QueryResult(
                query_id=query_row.id,
                record_id=rec.id,
                rank=rank,
                vector_score=float(1.0 - d) if d is not None else None,
                vision_confidence=vision_conf,
                was_selected=False,
            )
        )
        candidates.append(
            QueryCandidate(
                record_id=rec.id,
                rank=rank,
                image_url=img.storage_url,
                registered_at=rec.registered_at,
                vector_score=float(1.0 - d) if d is not None else None,
                vision_confidence=vision_conf,
                reason=rr.reason if rr else None,
            )
        )

    await session.commit()

    best_id = None
    if rerank.best_record_id:
        try:
            best_id = uuid.UUID(rerank.best_record_id)
        except ValueError:
            best_id = None

    # Ordena por confianza de Vision si está disponible
    candidates.sort(key=lambda c: (c.vision_confidence or 0.0), reverse=True)
    return QueryResponse(
        query_id=query_row.id,
        best_record_id=best_id,
        confidence=rerank.confidence,
        candidates=candidates,
    )


# --- Collage ------------------------------------------------------------------


async def build_query_collage(session: AsyncSession, query_id: uuid.UUID) -> bytes | None:
    """Reconstruye el collage de una consulta guardada (para el endpoint web)."""
    storage = get_storage()
    q = await session.get(Query, query_id)
    if q is None:
        return None

    query_bytes = None
    if q.query_image_url:
        try:
            query_bytes = storage.load(q.query_image_url)
        except Exception:  # noqa: BLE001
            query_bytes = None

    rows = (
        await session.execute(
            select(
                QueryResult.record_id,
                QueryResult.vision_confidence,
                QueryResult.vector_score,
                RecordImage.storage_url,
            )
            .join(RecordImage, RecordImage.record_id == QueryResult.record_id)
            .where(QueryResult.query_id == query_id)
            .order_by(QueryResult.rank)
        )
    ).all()
    if not rows:
        return None

    # Una imagen por record (la primera), preservando el orden por rank
    seen: set = set()
    cands: list = []
    for record_id, vconf, vscore, storage_url in rows:
        if record_id in seen:
            continue
        seen.add(record_id)
        cands.append(
            type(
                "C",
                (),
                {
                    "record_id": record_id,
                    "vision_confidence": vconf,
                    "vector_score": vscore,
                    "image_url": storage_url,
                },
            )()
        )
    return collage_svc.collage_for_candidates(storage, query_bytes, cands)


# --- Feedback -----------------------------------------------------------------


async def submit_feedback(
    session: AsyncSession, query_id: uuid.UUID, selected_record_id: uuid.UUID | None
) -> bool:
    """Marca la selección del usuario (was_selected) para evaluación. None = 'ninguna'."""
    results = (
        await session.execute(
            select(QueryResult).where(QueryResult.query_id == query_id)
        )
    ).scalars().all()
    if not results:
        return False
    for r in results:
        r.was_selected = selected_record_id is not None and r.record_id == selected_record_id
    await session.commit()
    return True
