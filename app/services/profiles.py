"""Gestión de usuarios y perfiles.

En el MVP self-hosted (un usuario/familia por despliegue) se provee un perfil por
defecto para que registrar y consultar funcione sin configuración previa.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Medicine, Profile, Record, RecordImage, User
from app.schemas import RecordOut

DEFAULT_PROFILE_NAME = "Titular"


async def get_or_create_default_profile(session: AsyncSession) -> Profile:
    """Devuelve el primer perfil; si no existe ninguno, crea usuario + perfil."""
    profile = (await session.execute(select(Profile).limit(1))).scalar_one_or_none()
    if profile is not None:
        return profile

    user = (await session.execute(select(User).limit(1))).scalar_one_or_none()
    if user is None:
        user = User()
        session.add(user)
        await session.flush()

    profile = Profile(
        owner_user_id=user.id, display_name=DEFAULT_PROFILE_NAME, relation="titular"
    )
    session.add(profile)
    await session.flush()
    return profile


async def create_profile(
    session: AsyncSession, display_name: str, relation: str | None = None
) -> Profile:
    user = (await session.execute(select(User).limit(1))).scalar_one_or_none()
    if user is None:
        user = User()
        session.add(user)
        await session.flush()
    profile = Profile(owner_user_id=user.id, display_name=display_name, relation=relation)
    session.add(profile)
    await session.flush()
    return profile


async def list_profiles(session: AsyncSession) -> list[Profile]:
    res = await session.execute(select(Profile).order_by(Profile.created_at))
    return list(res.scalars().all())


async def get_profile(session: AsyncSession, profile_id: uuid.UUID) -> Profile | None:
    return await session.get(Profile, profile_id)


async def get_or_create_by_name(session: AsyncSession, name: str) -> Profile:
    """Busca un perfil por nombre (case-insensitive) o lo crea."""
    clean = name.strip()
    res = await session.execute(select(Profile))
    for p in res.scalars():
        if p.display_name.lower() == clean.lower():
            return p
    return await create_profile(session, clean)


async def list_records(
    session: AsyncSession, profile_id: uuid.UUID, limit: int = 50, offset: int = 0
) -> list[RecordOut]:
    """Historial de un perfil: registros con nombre, dosis, tipo, imagen y fecha."""
    rows = (
        await session.execute(
            select(Record, Medicine)
            .outerjoin(Medicine, Medicine.id == Record.medicine_id)
            .where(Record.profile_id == profile_id)
            .order_by(Record.registered_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    if not rows:
        return []

    record_ids = [rec.id for rec, _ in rows]
    img_rows = (
        await session.execute(
            select(RecordImage.record_id, RecordImage.storage_url).where(
                RecordImage.record_id.in_(record_ids)
            )
        )
    ).all()
    first_image: dict = {}
    for rid, url in img_rows:
        first_image.setdefault(rid, url)

    out: list[RecordOut] = []
    for rec, med in rows:
        name = med.name_normalized.title() if med and med.name_normalized else None
        out.append(
            RecordOut(
                record_id=rec.id,
                name=name,
                dose=med.dose if med else None,
                source_type=rec.source_type,
                image_url=first_image.get(rec.id),
                notes=rec.notes,
                registered_at=rec.registered_at,
            )
        )
    return out
