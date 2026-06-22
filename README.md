# MemorIA Medicinal

**Tu memoria inteligente para medicamentos y recetas.**

Asistente self-hosted que construye una memoria visual de tus medicamentos a partir de
fotos. No intenta identificar globalmente qué medicamento es, sino reconocer **a cuál de
tus medicamentos registrados se parece** una foto, y cuándo lo compraste o registraste.

Open source y desplegable por cualquiera con Docker. Modo **OpenAI-first**: el despliegue
se reduce a FastAPI + Postgres + tu API key de OpenAI (sin GPU ni modelos locales).

- Plan maestro: [`PLAN.md`](./PLAN.md)
- Diseño técnico: [`ARQUITECTURA.md`](./ARQUITECTURA.md)

## Stack

Python 3.12 · FastAPI · PostgreSQL 16 + pgvector · OpenAI (GPT Vision + text-embedding-3-small) · React + Vite (frontend) · Docker.

## Interfaz web

Una app **React + Vite** (`frontend/`) con una **landing** de presentación y un **chat**
para hacer todo desde el navegador: enviar/capturar fotos, registrar medicamentos y
consultar coincidencias con tarjetas de candidatos (imagen, fecha, confianza) y feedback.

Con `docker compose up` queda en **http://localhost:8080** (nginx sirve la app y hace
proxy de `/api` al backend).

Desarrollo del frontend por separado:

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173 (proxy /api -> :8000)
```

## Cómo levantar (Sprint 1)

```bash
cp .env.example .env        # pon tu OPENAI_API_KEY y credenciales de BD
docker compose up --build
```

Esto levanta:
- **db**: Postgres 16 con pgvector; la migración `migrations/001_init.sql` se ejecuta
  automáticamente al crear la base.
- **app**: API FastAPI en http://localhost:8000

Verifica:

```bash
curl http://localhost:8000/health
# {"status":"ok","database":true,"pgvector":true,"ai_mode":"openai"}
```

Documentación interactiva de la API: http://localhost:8000/docs

## Endpoints del pipeline

Requieren `OPENAI_API_KEY` configurada (si falta, devuelven `503`).

**Registrar un medicamento** (foto + tipo de fuente):

```bash
curl -X POST http://localhost:8000/records \
  -F "image=@blister.jpg" \
  -F "source_type=blister"        # receta | caja | blister | pastilla
# -> { record_id, name, dose, image_url, registered_at, ... }
```

**Consultar** ("¿a cuál de mis medicamentos se parece?"):

```bash
curl -X POST http://localhost:8000/query \
  -F "image=@foto_nueva.jpg" \
  -F "question=¿cuándo compré esta pastilla?"
# -> { query_id, best_record_id, confidence, candidates:[...], disclaimer }
```

**Feedback** (qué eligió el usuario — alimenta precision@1 / recall@5):

```bash
curl -X POST http://localhost:8000/query/<query_id>/feedback \
  -H "Content-Type: application/json" \
  -d '{"selected_record_id": "<record_id>"}'   # null = "ninguna"
```

**Perfiles** (opcional; si no se indica `profile_id` se usa el perfil por defecto):
`GET /profiles`, `POST /profiles`.

## Estado

- [x] **Sprint 1 — Infraestructura**: FastAPI, Docker, Postgres+pgvector, esquema,
  almacenamiento local, `/health`.
- [x] **Pipeline (núcleo)**: módulo de IA OpenAI + endpoints `/records`, `/query`,
  feedback y perfiles (extracción → embedding → pgvector Top-K → re-rank visual).
- [x] **Sprint 2 — Telegram**: bot con registro y consulta (webhook + polling, botones y feedback).
- [x] **Sprint 2.5 — Interfaz Web**: landing + chat (React/Vite), servir imágenes + CORS.
- [x] Sprint 3 — Vision (extracción de datos) *(incluido en el pipeline)*
- [x] Sprint 4 — Embeddings *(text-embedding-3-small)*
- [x] Sprint 5 — Similitud (Top-K) *(pgvector)*
- [x] Sprint 6 — Comparación visual / desempate *(re-rank de Vision)*
- [x] **Sprint 7 — WhatsApp**: Cloud API (webhook + verificación, botones/listas, registro, consulta y feedback).
- [x] **Sprint 8 — Collage inteligente**: imagen única con opciones numeradas (Telegram, WhatsApp, endpoint).
- [x] **Sprint 9 — Historial familiar**: perfiles (web + canales) e historial por persona.
- [x] **Sprint 10 — Agente conversacional + recetas**: LangGraph + recetas agrupadas, en web, Telegram y WhatsApp (`POST /agent/message`).
- [ ] Sprint 11 — Speech-to-text (web + Telegram + WhatsApp)

## Agente conversacional (Sprint 10)

En vez de botones, hablas con naturalidad y el agente (un grafo de estados con
**LangGraph** + tool-calling de OpenAI) interpreta la intención y agrupa medicinas en
**recetas**. Ejemplo:

> — Hoy Thiago tuvo cita y le recetaron esto _(adjuntas la foto de la receta)_
> — 📋 Abrí la receta de Thiago. Mándame una foto de cada medicina…
> — _(envías foto de un blíster)_ → ✅ Agregué Amoxicilina 500mg. Van 1. ¿Otra o digo listo?
> — listo → 📋 Cerré la receta con N medicinas.

Disponible en la web (`/chat`), Telegram y WhatsApp. La web usa `POST /agent/message`.

## Telegram (Sprint 2)

El mismo pipeline está disponible desde un bot de Telegram.

1. Crea un bot con [@BotFather](https://t.me/BotFather) y copia el token.
2. Pon `TELEGRAM_BOT_TOKEN` (y opcionalmente `TELEGRAM_WEBHOOK_SECRET`) en `.env`.
3. Elige un modo:

**Polling (local, sin URL pública):**

```bash
python -m scripts.telegram_polling
```

**Webhook (producción, requiere HTTPS público):**

```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://TU_DOMINIO/telegram/webhook" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

Uso en el chat: envía una foto → botones **Registrar** / **Consultar**. Al registrar,
eliges el tipo (receta/caja/blíster/pastilla); al consultar, recibes las coincidencias con
botones de feedback (1/2/3/Ninguna).

## WhatsApp (Sprint 7)

Usa la **WhatsApp Cloud API** de Meta.

1. En [Meta for Developers](https://developers.facebook.com/) crea una app con el
   producto *WhatsApp* y obtén el **token de acceso** y el **Phone Number ID**.
2. Define en `.env`: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`
   (una cadena que inventas tú) y opcionalmente `WHATSAPP_API_VERSION`.
3. Configura el webhook en el panel de Meta apuntando a tu URL pública:
   - URL de callback: `https://TU_DOMINIO/whatsapp/webhook`
   - Token de verificación: el mismo `WHATSAPP_VERIFY_TOKEN`
   - Suscríbete al campo **messages**.

Meta llamará a `GET /whatsapp/webhook` para verificar (se responde el `hub.challenge`) y
enviará los mensajes a `POST /whatsapp/webhook`.

Uso: el usuario envía una foto → botones **Registrar** / **Consultar**; al registrar
elige el tipo (lista), al consultar recibe las coincidencias y una lista de feedback.

## Aviso médico

El sistema nunca afirma identidad, aptitud de consumo ni estado de un medicamento.
Siempre responde "posible coincidencia", recuerda verificar la fecha de vencimiento y
consultar a un profesional de salud.
