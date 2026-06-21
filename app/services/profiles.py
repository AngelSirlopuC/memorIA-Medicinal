"""Gestión de usuarios y perfiles.

En el MVP self-hosted (un usuario/familia por despliegue) se provee un perfil por
defecto para que registrar y consultar funcione sin configuración previa.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Profile, User

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
