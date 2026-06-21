# MemorIA Medicinal — Documento de Diseño Técnico
**Versión:** 1.0 · **Fecha:** 2026-06-20

> Este documento acompaña a `PLAN.md` y detalla las decisiones técnicas del MVP,
> con foco en el rediseño del motor de similitud y el modelo de datos.

---

## 1. Principios de diseño

1. **Self-hosted y open source.** Cualquiera debe poder clonar el repo y levantar su
   propio agente con `docker compose up`. Sin dependencias obligatorias de servicios
   de pago.
2. **OpenAI-first, despliegue simple.** Para evitar GPU, RAM alta y descargas de
   modelos, la IA se hace vía la API de OpenAI: el despliegue se reduce a *FastAPI +
   Postgres + una API key*. El almacenamiento es local por defecto (bucket opcional).
   Un modo 100% local con CLIP queda reservado como opción futura.
3. **Datos del usuario, del usuario.** Cada despliegue es de una persona/familia. No
   hay centralización ni multi-tenant en la nube. Esto simplifica radicalmente la
   privacidad: los datos viven en el Postgres del propio usuario.
4. **Reencuadre del problema.** No respondemos "¿qué medicamento es?" (global,
   difícil), sino "¿a cuál de TUS medicamentos registrados se parece más?" (acotado,
   preciso).
5. **Seguridad médica.** Nunca afirmar identidad, aptitud de consumo o estado. Siempre
   "posible coincidencia", recordar verificar vencimiento y consultar a un profesional.

---

## 2. El problema del embedding (y su corrección)

### Qué proponía el plan original
`Foto → OpenAI Vision (descripción) → OpenAI Embeddings (sobre el texto) → pgvector`.

### Por qué falla
- **OpenAI no ofrece embeddings de imagen** (solo texto, `text-embedding-3`). Embeber
  *la descripción de texto* de una foto pierde la señal visual: dos amoxicilinas 500mg
  distintas producen descripciones casi idénticas → vectores casi iguales → el Top-5 no
  discrimina.
- La señal discriminante real (color exacto, forma, empaque, distribución) vive en los
  píxeles, no en una frase.

### Corrección: motor OpenAI-first (despliegue simple)

Para minimizar la complejidad de despliegue (sin modelos locales, sin GPU, sin descargas
de pesos), todo el cómputo de IA se hace vía la **API de OpenAI**. El despliegue se
reduce a *FastAPI + Postgres + una API key*. Como OpenAI no ofrece embeddings de imagen,
la similitud se construye así:

**Paso 1 — Descripción canónica con Vision (GPT).** Al registrar, GPT Vision extrae
campos estructurados (nombre, dosis, laboratorio, presentación, forma, color, texto
visible) y una descripción visual detallada (empaque, patrón del blíster, distribución).
Estos campos se concatenan en un **descriptor canónico** en orden estable.

**Paso 2 — Embedding de texto.** El descriptor se embebe con `text-embedding-3-small`
(1536 dims) → `record_embeddings.text_embedding`. Es la señal indexable para búsqueda.

**Paso 3 — Búsqueda en pgvector.** En consulta, se describe la foto nueva igual que en
registro, se embebe y se recupera el **Top-K** por similitud coseno.

**Paso 4 — Re-rank visual con Vision.** GPT Vision compara la **foto de consulta contra
las imágenes reales** del Top-K y devuelve mejor coincidencia, confianza y explicación.
Este paso recupera la precisión visual que el embedding de texto pierde — justo donde más
hace falta — sin necesidad de CLIP local.

> **Por qué funciona pese a embeber texto:** el espacio de búsqueda es el historial
> pequeño del propio usuario (no un catálogo global), y el re-rank de Vision sobre las
> imágenes reales corrige los empates entre productos parecidos.

### Compromiso aceptado
A cambio de la simplicidad, cada consulta hace 1 llamada de Vision (describir) + 1
embedding + 1 llamada de Vision (re-rank). Sigue siendo de centavos con GPT-5 mini (ver
§5). El modo local con CLIP queda **reservado** como opción futura (la columna
`image_embedding` ya existe para ello), pero no es parte del MVP.

### Nota sobre blísteres cortados
El patrón de corte se trata como señal **secundaria/bonus**, nunca primaria: el mismo
blíster cambia físicamente entre el registro (lleno) y la consulta (cortado), así que
usarlo como eje principal *penalizaría* coincidencias correctas. La identidad la dan
texto + empaque (estables en el tiempo), y el re-rank de Vision los pondera.

---

## 3. Flujos

### Registro
```
Foto → [GPT Vision: campos + descripción + texto visible (OCR incluido)]
     → descriptor canónico → [text-embedding-3-small → text_embedding]
     → guardar imagen (storage) → guardar record + embedding
```

### Consulta
```
Foto nueva → [GPT Vision: descriptor] → [embedding] → pgvector Top-K
          → [GPT Vision: re-rank foto vs imágenes candidatas] → Top-3 + confianza
          → registrar query + query_results (feedback del usuario)
```

---

## 4. Modelo de datos

Mejoras sobre el esquema original: **perfiles** desde el día 1 (familia), separación
**compra ≠ registro**, campos de **vencimiento/lote**, varias imágenes por registro con
dedup por hash, vectores **visual + texto** separados con versión de modelo, y registro
del **feedback del usuario** para medir calidad.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- Dueño del despliegue
users (id, created_at, locale, settings jsonb)

-- Perfiles: soporta familia desde el inicio (evita migración futura)
profiles (id, owner_user_id→users, display_name, relation, created_at)

-- Vinculación de canales de mensajería
channels (id, user_id→users, channel_type ['telegram'|'whatsapp'],
          external_id, verified, created_at)

-- Producto canónico (dedup + agregación "¿cuántas veces compré X?")
medicines (id, name_normalized, dose, lab, presentation, form, color, created_at)

-- Cada evento de registro de foto
records (id, profile_id→profiles, medicine_id→medicines NULL,
         source_type ['receta'|'caja'|'blister'|'pastilla'],
         visible_text, ai_description, registered_at, notes)

-- Varias imágenes por registro, con hash para dedup
record_images (id, record_id→records, storage_url, sha256, width, height, created_at)

-- Vectores separados (permite re-embeber sin perder datos)
record_embeddings (id, record_image_id→record_images,
                   image_embedding vector(768),   -- CLIP/SigLIP
                   text_embedding  vector(1024),  -- e5 / openai
                   model_version, created_at)

-- Compra ≠ registro: "cuándo lo compré", vencimiento, precio, cantidad
purchases (id, profile_id→profiles, medicine_id→medicines,
           purchased_at, quantity, unit, price, pharmacy,
           expiry_date, lot_number, created_at)

-- Log de consultas (también para evaluación)
queries (id, profile_id→profiles, query_image_url,
         query_embedding vector(768), question_text, asked_at)

-- Candidatos mostrados + qué eligió el usuario  ← clave para medir calidad
query_results (id, query_id→queries, record_id→records, rank,
               vector_score, vision_confidence, was_selected, created_at)
```

`query_results` permite calcular **precision@1** y **recall@5** reales: cada vez que el
usuario elige "1/2/3/ninguna" alimenta el dataset de evaluación.

---

## 5. Costos

Modo OpenAI-first. Precios OpenAI a junio 2026; imágenes de celular ≈ 1.000 tokens a
alto detalle. Embedding `text-embedding-3-small` ≈ $0.00002 por descriptor (despreciable).

| Operación | Llamadas | GPT-5 mini | GPT-4o |
|---|---|---|---|
| Registro | 1 Vision + 1 embed | ~$0.0005 | ~$0.007 |
| Consulta completa | 2 Vision (describir + re-rank) + 1 embed | ~$0.0017 | ~$0.027 |

Con **GPT-5 mini**: registrar ~2.000 medicamentos ≈ **$1 USD**; ~1.000 consultas
completas ≈ **$1.70 USD** (≈ S/6.4). Sigue siendo de centavos a escala personal/familiar,
a cambio de un despliegue mucho más simple (sin GPU ni modelos locales).

**Recomendación MVP:** `gpt-5-mini` para Vision + `text-embedding-3-small` para embeddings
(ambos por variable de entorno). El modo local con CLIP queda reservado para el futuro.

---

## 6. Stack tecnológico

| Capa | Elección | Notas |
|---|---|---|
| Lenguaje | Python 3.12 | |
| API | FastAPI + Uvicorn | async |
| ORM / DB | SQLAlchemy 2.0 async + asyncpg | |
| Base de datos | PostgreSQL 16 + pgvector | imagen `pgvector/pgvector:pg16` |
| Vision (extracción + re-rank) | OpenAI GPT-5 mini | configurable vía `VISION_MODEL` |
| Embedding de texto | OpenAI text-embedding-3-small (1536) | configurable vía `EMBED_MODEL` |
| OCR | Incluido en GPT Vision (`visible_text`) | sin librería extra |
| Embedding visual (CLIP) | Reservado / no usado en MVP | columna `image_embedding` lista |
| Almacenamiento | Filesystem local (default) · Supabase/S3/MinIO (opcional) | abstraído |
| Canales | Telegram Bot API (primero) · WhatsApp Cloud API (después) | |
| Orquestación | Funciones async simples al inicio; LangGraph cuando haya ramificación real | |
| Despliegue | Docker Compose | |

**Sobre LangGraph:** se pospone. Los flujos del MVP son lineales; LangGraph añade
complejidad de grafos/estado que aún no se necesita. Se introducirá cuando haya
comportamiento agéntico con ramificación.

---

## 7. Métricas de éxito (cuantitativas)

- **precision@1** ≥ objetivo sobre set de prueba etiquetado.
- **recall@5** ≥ objetivo (el medicamento correcto aparece en el Top-K antes del re-rank).
- Latencia de consulta completa (2 Vision + embed) en rango aceptable para WhatsApp.
- Tasa de "ninguna" del usuario (proxy de fallos del matching).
- Costo promedio por consulta (monitoreo del gasto de API).

El set de prueba se construye con `queries` + `query_results` reales del propio uso.
