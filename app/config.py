"""Configuración central, cargada desde variables de entorno / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    # Orígenes permitidos para CORS (separados por coma). El frontend Vite usa 5173.
    cors_origins: str = "http://localhost:5173,http://localhost:4173,http://localhost"

    # Base de datos
    postgres_user: str = "memoria"
    postgres_password: str = "memoria"
    postgres_db: str = "memoria"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Almacenamiento
    storage_backend: str = "local"
    storage_local_dir: str = "/data/images"

    # IA — modo OpenAI-first (despliegue simple: solo se necesita la API key)
    openai_api_key: str | None = None
    vision_model: str = "gpt-5-mini"            # extracción + re-rank visual
    embed_model: str = "text-embedding-3-small"  # 1536 dims
    vision_rerank_topk: int = 5                  # candidatos que pasan al re-rank

    # Canales
    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None  # valida X-Telegram-Bot-Api-Secret-Token
    whatsapp_token: str | None = None
    whatsapp_phone_id: str | None = None
    whatsapp_verify_token: str | None = None    # valida el GET de verificación del webhook
    whatsapp_api_version: str = "v21.0"

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.whatsapp_token and self.whatsapp_phone_id)

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def ai_enabled(self) -> bool:
        """True si hay API key de OpenAI; si no, modo 100% local."""
        return bool(self.openai_api_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
