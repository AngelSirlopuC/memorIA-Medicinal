"""Interfaz de almacenamiento. Permite cambiar local <-> nube sin tocar la lógica."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod


class Storage(ABC):
    """Contrato para guardar y recuperar imágenes."""

    @abstractmethod
    def save(self, data: bytes, filename: str) -> str:
        """Guarda los bytes y devuelve una URL/ruta de acceso."""

    @abstractmethod
    def load(self, ref: str) -> bytes:
        """Recupera los bytes a partir de la referencia devuelta por save()."""

    @abstractmethod
    def exists(self, ref: str) -> bool:
        """Indica si la referencia existe."""

    @staticmethod
    def sha256(data: bytes) -> str:
        """Hash para dedup de imágenes idénticas."""
        return hashlib.sha256(data).hexdigest()
