"""Capa de almacenamiento de imágenes (abstracta)."""

from app.config import get_settings
from app.storage.base import Storage
from app.storage.local import LocalStorage


def get_storage() -> Storage:
    """Devuelve el backend de almacenamiento según la configuración."""
    settings = get_settings()
    backend = settings.storage_backend.lower()
    if backend == "local":
        return LocalStorage(settings.storage_local_dir)
    # supabase / s3 / minio se implementarán en sprints posteriores
    raise NotImplementedError(
        f"Backend de almacenamiento '{backend}' aún no implementado. Usa 'local'."
    )


__all__ = ["Storage", "LocalStorage", "get_storage"]
