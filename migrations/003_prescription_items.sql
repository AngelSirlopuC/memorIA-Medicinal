-- MemorIA Medicinal — Migración 003
-- Lista de medicamentos recetados (OCR) extraída de la foto de la receta.
-- Cada item: {"name": ..., "dose": ..., "instructions": ...}

ALTER TABLE prescriptions
    ADD COLUMN IF NOT EXISTS items JSONB NOT NULL DEFAULT '[]'::jsonb;
