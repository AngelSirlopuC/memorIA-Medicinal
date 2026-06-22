-- MemorIA Medicinal — Migración 002 (Sprint 10)
-- Recetas/citas que agrupan varias medicinas, y vínculo desde records.

CREATE TABLE prescriptions (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    profile_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    doctor        TEXT,
    prescribed_at DATE,
    notes         TEXT,
    image_url     TEXT,                 -- foto de la receta (opcional)
    closed        BOOLEAN NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_prescriptions_profile ON prescriptions(profile_id);

-- Cada registro de medicina puede pertenecer a una receta
ALTER TABLE records
    ADD COLUMN prescription_id UUID REFERENCES prescriptions(id) ON DELETE SET NULL;
CREATE INDEX idx_records_prescription ON records(prescription_id);
