"""Collage inteligente (Sprint 8).

Cuando una consulta devuelve varias coincidencias, se genera UNA sola imagen con las
opciones numeradas (y la foto de consulta), resaltando la mejor coincidencia. Mejora la
experiencia en WhatsApp/Telegram: menos mensajes y comparación visual inmediata.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont, ImageOps

CELL = 300
LABEL_H = 40
PAD = 12
BG = (243, 248, 251)
LABEL_BG = (8, 145, 178)        # teal
LABEL_BG_BEST = (22, 163, 110)  # verde (mejor coincidencia)
LABEL_FG = (255, 255, 255)
PLACEHOLDER = (230, 238, 242)

_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
)


def _font(size: int) -> ImageFont.ImageFont:
    for p in _FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001
            continue
    try:
        return ImageFont.load_default(size)  # Pillow >= 10.1
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def _cell_image(data: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(data))
        return ImageOps.fit(im.convert("RGB"), (CELL, CELL), method=Image.LANCZOS)
    except Exception:  # noqa: BLE001 — imagen inválida o faltante
        return Image.new("RGB", (CELL, CELL), PLACEHOLDER)


def render_collage(items: list[tuple[str, bytes, bool]]) -> bytes:
    """items: lista de (etiqueta, bytes_de_imagen, es_mejor). Devuelve JPEG."""
    n = max(1, len(items))
    cols = 2 if n <= 4 else 3
    rows = (n + cols - 1) // cols
    width = cols * CELL + (cols + 1) * PAD
    height = rows * (CELL + LABEL_H) + (rows + 1) * PAD

    canvas = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(canvas)
    font = _font(22)

    for idx, (label, data, best) in enumerate(items):
        r, c = divmod(idx, cols)
        x = PAD + c * (CELL + PAD)
        y = PAD + r * (CELL + LABEL_H + PAD)

        draw.rectangle([x, y, x + CELL, y + LABEL_H], fill=LABEL_BG_BEST if best else LABEL_BG)
        try:
            tw = draw.textlength(label, font=font)
        except Exception:  # noqa: BLE001
            tw = len(label) * 10
        draw.text((x + (CELL - tw) / 2, y + 9), label, fill=LABEL_FG, font=font)

        canvas.paste(_cell_image(data), (x, y + LABEL_H))
        if best:
            draw.rectangle(
                [x, y, x + CELL, y + LABEL_H + CELL], outline=LABEL_BG_BEST, width=5
            )

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=85)
    return out.getvalue()


def collage_for_candidates(storage, query_bytes: bytes | None, candidates: list) -> bytes:
    """Construye el collage desde objetos QueryCandidate cargando sus imágenes del storage."""
    best_id, best_val = None, -1.0
    for c in candidates:
        v = c.vision_confidence if c.vision_confidence is not None else (c.vector_score or 0.0)
        if v is not None and v > best_val:
            best_val, best_id = v, c.record_id

    items: list[tuple[str, bytes, bool]] = []
    for i, c in enumerate(candidates):
        try:
            data = storage.load(c.image_url) if c.image_url else b""
        except Exception:  # noqa: BLE001
            data = b""
        items.append((f"Opción {i + 1}", data, c.record_id == best_id))

    if query_bytes is not None:
        items.append(("Tu consulta", query_bytes, False))
    return render_collage(items)
