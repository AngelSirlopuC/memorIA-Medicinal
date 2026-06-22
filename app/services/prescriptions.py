"""Servicio de recetas/citas (Sprint 10): agrupan varias medicinas."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Prescription, Record


async def create_prescription(
    session: AsyncSession,
    profile_id: uuid.UUID,
    doctor: str | None = None,
    prescribed_at: date | None = None,
    notes: str | None = None,
    image_url: str | None = None,
) -> Prescription:
    pres = Prescription(
        profile_id=profile_id,
        doctor=doctor,
        prescribed_at=prescribed_at,
        notes=notes,
        image_url=image_url,
    )
    session.add(pres)
    await session.flush()
    return pres


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
