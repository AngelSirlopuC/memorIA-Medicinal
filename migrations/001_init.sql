-- MemorIA Medicinal — Migración inicial (Sprint 1)
-- PostgreSQL 16 + pgvector
-- Esquema canónico. Los modelos SQLAlchemy en app/db/models.py reflejan esto.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Dimensiones de embeddings (modo OpenAI-first):
--   text_embedding : 1536 (text-embedding-3-small) — señal de similitud principal
--   image_embedding: 768  RESERVADO/nullable para un modo local opcional (CLIP). No se
--                    usa en el modo OpenAI; la precisión visual la aporta el re-rank de
--                    Vision sobre las imágenes reales del Top-K.

-- =========================================================
-- Usuarios y perfiles
-- =========================================================

CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    locale      TEXT NOT NULL DEFAULT 'es',
    settings    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Perfiles: soporta familia desde el día 1 (evita migración futura)
CREATE TABLE profiles (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name  TEXT NOT NULL,
    relation      TEXT,                       -- 'titular','hijo','madre',...
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_profiles_owner ON profiles(owner_user_id);

-- Vinculación de canales de mensajería
CREATE TABLE channels (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel_type TEXT NOT NULL CHECK (channel_type IN ('telegram','whatsapp')),
    external_id  TEXT NOT NULL,               -- chat_id / phone number
    verified     BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (channel_type, external_id)
);
CREATE INDEX idx_channels_user ON channels(user_id);

-- =========================================================
-- Catálogo de productos (canónico, para dedup y agregación)
-- =========================================================

CREATE TABLE medicines (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name_normalized TEXT NOT NULL,
    dose            TEXT,
    lab             TEXT,
    presentation    TEXT,                     -- 'tableta','jarabe','cápsula',...
    form            TEXT,                     -- forma física
    color           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_medicines_name ON medicines(name_normalized);

-- =========================================================
-- Registros de medicamentos (cada foto registrada)
-- =========================================================

CREATE TABLE records (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    profile_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    medicine_id   UUID REFERENCES medicines(id) ON DELETE SET NULL,
    source_type   TEXT NOT NULL CHECK (source_type IN ('receta','caja','blister','pastilla')),
    visible_text  TEXT,                       -- texto OCR
    ai_description TEXT,                       -- descripción generada por Vision LLM
    notes         TEXT,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_records_profile ON records(profile_id);
CREATE INDEX idx_records_medicine ON records(medicine_id);

-- Varias imágenes por registro, con hash para dedup
CREATE TABLE record_images (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    record_id   UUID NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    storage_url TEXT NOT NULL,                -- ruta local o URL del bucket
    sha256      TEXT NOT NULL,                -- dedup de imágenes idénticas
    width       INTEGER,
    height      INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (record_id, sha256)
);
CREATE INDEX idx_record_images_record ON record_images(record_id);

-- Vectores separados (permite re-embeber sin perder datos)
CREATE TABLE record_embeddings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    record_image_id UUID NOT NULL REFERENCES record_images(id) ON DELETE CASCADE,
    image_embedding vector(768),              -- RESERVADO (modo local opcional)
    text_embedding  vector(1536),             -- text-embedding-3-small
    model_version   TEXT NOT NULL,            -- p.ej. 'gpt-5-mini+te3small@1'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_record_emb_image ON record_embeddings(record_image_id);
-- Índice ANN sobre el embedding de texto (señal principal en modo OpenAI).
-- Coseno por defecto. Ajustar 'lists' según volumen.
CREATE INDEX idx_record_emb_text_vec ON record_embeddings
    USING ivfflat (text_embedding vector_cosine_ops) WITH (lists = 100);
-- (Índice de image_embedding omitido: reservado para el modo local opcional.)

-- =========================================================
-- Compras (compra ≠ registro)
-- =========================================================

CREATE TABLE purchases (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    profile_id   UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    medicine_id  UUID REFERENCES medicines(id) ON DELETE SET NULL,
    purchased_at DATE,
    quantity     NUMERIC,
    unit         TEXT,                        -- 'tabletas','ml',...
    price        NUMERIC,
    pharmacy     TEXT,
    expiry_date  DATE,                        -- vencimiento
    lot_number   TEXT,                        -- lote
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_purchases_profile ON purchases(profile_id);
CREATE INDEX idx_purchases_medicine ON purchases(medicine_id);

-- =========================================================
-- Consultas y feedback (evaluación: precision@1 / recall@5)
-- =========================================================

CREATE TABLE queries (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    profile_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    query_image_url TEXT,
    query_embedding vector(1536),             -- mismo espacio que text_embedding
    question_text   TEXT,
    asked_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_queries_profile ON queries(profile_id);

CREATE TABLE query_results (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    query_id         UUID NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    record_id        UUID REFERENCES records(id) ON DELETE SET NULL,
    rank             INTEGER NOT NULL,
    vector_score     DOUBLE PRECISION,
    vision_confidence DOUBLE PRECISION,
    was_selected     BOOLEAN NOT NULL DEFAULT false,  -- qué eligió el usuario
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_query_results_query ON query_results(query_id);
