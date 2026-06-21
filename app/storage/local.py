"""Almacenamiento en el sistema de archivos local (backend por defecto)."""

from __future__ import annotations

from pathlib import Path

from app.storage.base import Storage


class LocalStorage(Storage):
    def __init__(self, base_dir: str) -> None:
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, ref: str) -> Path:
        # Evita escapes del directorio base (path traversal)
        name = Path(ref).name
        return self.base / name

    def save(self, data: bytes, filename: str) -> str:
        path = self._path(filename)
        path.write_bytes(data)
        return str(path)

    def load(self, ref: str) -> bytes:
        return self._path(ref).read_bytes()

    def exists(self, ref: str) -> bool:
        return self._path(ref).exists()
