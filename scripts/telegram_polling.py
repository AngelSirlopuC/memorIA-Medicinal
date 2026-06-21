"""Polling local de Telegram (para probar sin URL pública / webhook).

Uso:
    python -m scripts.telegram_polling

Requiere TELEGRAM_BOT_TOKEN en .env y la base de datos levantada. No usar en producción
junto con el webhook (elige uno u otro).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import get_settings
from app.integrations import telegram

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("telegram.polling")


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN en .env")

    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    # Asegura que no haya webhook activo (incompatible con getUpdates)
    async with httpx.AsyncClient(timeout=40) as c:
        await c.post(f"{base}/deleteWebhook")

    offset = 0
    log.info("Polling iniciado. Ctrl+C para detener.")
    async with httpx.AsyncClient(timeout=40) as c:
        while True:
            try:
                r = await c.get(
                    f"{base}/getUpdates", params={"timeout": 30, "offset": offset}
                )
                data = r.json()
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    await telegram.process_update(upd)
            except Exception:  # noqa: BLE001
                log.exception("Error en el loop de polling")
                await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
