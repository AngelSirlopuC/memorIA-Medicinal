-- MemorIA Medicinal — Migración 004
-- Datos editables por conversación sobre una medicina ya registrada.

ALTER TABLE records ADD COLUMN IF NOT EXISTS expiry TEXT;       -- vencimiento, p.ej. "08/2026"
ALTER TABLE records ADD COLUMN IF NOT EXISTS lot_number TEXT;   -- lote
