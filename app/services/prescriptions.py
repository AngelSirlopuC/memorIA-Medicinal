"""Servicio de recetas/citas (Sprint 10): agrupan varias medicinas."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Medicine, Prescription, Record


async def create_prescription(
    session: AsyncSession,
    profile_id: uuid.UUID,
    doctor: str | None = None,
    prescribed_at: date | None = None,
    notes: str | None = None,
    image_url: str | None = None,
    items: list | None = None,
) -> Prescription:
    pres = Prescription(
        profile_id=profile_id,
        doctor=doctor,
        prescribed_at=prescribed_at,
        notes=notes,
        image_url=image_url,
        items=items or [],
    )
    session.add(pres)
    await session.flush()
    return pres


async def get_last_prescription(
    session: AsyncSession, profile_id: uuid.UUID
) -> Prescription | None:
    return (
        await session.execute(
            select(Prescription)
            .where(Prescription.profile_id == profile_id)
            .order_by(Prescription.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def search_prescriptions(
    session: AsyncSession,
    profile_id: uuid.UUID | None = None,
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
    limit: int = 20,
) -> list[Prescription]:
    """Busca recetas por persona y/o fecha (la fecha del papel o, si no hay, la de registro)."""
    stmt = select(Prescription)
    if profile_id:
        stmt = stmt.where(Prescription.profile_id == profile_id)
    stmt = stmt.order_by(Prescription.created_at.desc()).limit(100)
    rows = (await session.execute(stmt)).scalars().all()

    out: list[Prescription] = []
    for p in rows:
        d = p.prescribed_at or p.created_at.date()
        if year and d.year != year:
            continue
        if month and d.month != month:
            continue
        if day and d.day != day:
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


async def list_medicines(session: AsyncSession, pres_id: uuid.UUID) -> list[tuple]:
    """Devuelve [(nombre, dosis), ...] de las medicinas registradas en la receta."""
    rows = (
        await session.execute(
            select(Medicine.name_normalized, Medicine.dose)
            .select_from(Record)
            .outerjoin(Medicine, Medicine.id == Record.medicine_id)
            .where(Record.prescription_id == pres_id)
            .order_by(Record.registered_at)
        )
    ).all()
    return [(n, d) for n, d in rows]


async def get_prescription(session: AsyncSession, pres_id: uuid.UUID) -> Prescription | None:
    return await session.get(Prescription, pres_id)


async def close_prescription(session: AsyncSession, pres_id: uuid.UUID) -> bool:
    pres = await session.get(Prescription, pres_id)
    if pres is None:
        return False
    pres.closed = True
    await session.commit()
    return True


async def count_medicines(session: AsyncSession, pres_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count(Record.id)).where(Record.prescription_id == pres_id)
        )
    ).scalar_one()
