"""MemorIA Medicinal — punto de entrada de la API (Sprint 1)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import __version__
from app.api.agent_router import router as agent_router
from app.api.router import router as api_router
from app.api.telegram_router import router as telegram_router
from app.api.whatsapp_router import router as whatsapp_router
from app.config import get_settings
from app.db.session import engine

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Aquí, en sprints siguientes, se cargarán los modelos locales (CLIP/OCR).
    yield
    await engine.dispose()


app = FastAPI(
    title="MemorIA Medicinal",
    version=__version__,
    description="Tu memoria inteligente para medicamentos y recetas.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(agent_router)
app.include_router(telegram_router)
app.include_router(whatsapp_router)


@app.get("/")
async def root() -> dict:
    return {
        "name": "MemorIA Medicinal",
        "version": __version__,
        "ai_mode": "openai" if settings.ai_enabled else "local",
    }


@app.get("/health")
async def health() -> dict:
    """Verifica la app y la conexión a la base de datos (incluida pgvector)."""
    db_ok = False
    pgvector_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            db_ok = True
            res = await conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            )
            pgvector_ok = res.first() is not None
    except Exception:  # noqa: BLE001 — health no debe romper
        db_ok = False

    status = "ok" if (db_ok and pgvector_ok) else "degraded"
    return {
        "status": status,
        "database": db_ok,
        "pgvector": pgvector_ok,
        "ai_mode": "openai" if settings.ai_enabled else "local",
    }
