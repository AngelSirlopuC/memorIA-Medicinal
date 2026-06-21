# MemorIA Medicinal
## Plan Maestro MVP v2.0

### Eslogan

**"Tu memoria inteligente para medicamentos y recetas."**

> **v2.0 (2026-06-20):** rediseño del motor de similitud (embeddings visuales locales
> en vez de texto-de-imagen), enfoque **self-hosted / open source** desplegable en
> Docker, modelo de datos mejorado y roadmap ajustado. Detalle técnico en
> [`ARQUITECTURA.md`](./ARQUITECTURA.md).

---

# 1. Problema

En Perú y otros países de Latinoamérica es común que:

- Las farmacias vendan medicamentos por unidad.
- Los blísteres sean cortados según la cantidad requerida.
- El usuario deseche la caja original.
- Se pierda la receta médica.
- Se olvide cuándo fue comprado un medicamento.
- Existan medicamentos almacenados durante meses o años sin identificación clara.

Como consecuencia:

- Se acumulan medicamentos sin contexto.
- Se desconoce la antigüedad del medicamento.
- Existe riesgo de consumir medicamentos antiguos.
- Se pierde el historial de tratamientos.

---

# 2. Visión

**MemorIA Medicinal** es un asistente inteligente que permite registrar recetas, medicamentos y compras mediante fotografías, construyendo una memoria visual e histórica que ayuda a identificar medicamentos previamente adquiridos y conocer cuándo fueron comprados o recetados.

---

# 3. Objetivo del MVP

Permitir que un usuario pueda:

1. Registrar medicamentos mediante fotografías.
2. Consultar posteriormente una fotografía de una pastilla o blíster.
3. Obtener la compra o registro más probable asociado a esa imagen.
4. Conocer cuándo fue registrado o comprado aproximadamente.

Ejemplo:

> "Esta imagen coincide con un medicamento registrado el 08/06/2026 con una confianza alta."

---

# 4. Arquitectura General

```text
Telegram / WhatsApp
      ↓
    FastAPI
      ↓
  Pipeline (funciones async)
      ↓
 ┌────────────────────────────────────────────────┐
 │ OpenAI API                                       │
 │  · GPT Vision  → extracción + OCR + re-rank      │
 │  · text-embedding-3-small → embedding (1536)     │
 └────────────────────────────────────────────────┘
      ↓
PostgreSQL + pgvector  (text_embedding)
      ↓
 Almacenamiento local (default) / Supabase / S3 / MinIO
```

> **Nota:** LangGraph se pospone. Los flujos del MVP son lineales; se introducirá
> cuando exista comportamiento agéntico con ramificación real.

---

# 5. Stack Tecnológico

## Backend

- Python 3.12
- FastAPI

## Orquestación

- LangGraph
- LangChain

## Orquestación

- Funciones async simples (MVP)
- LangGraph (futuro, cuando haya ramificación)

## Inteligencia Artificial (modo OpenAI-first)

- **Vision (GPT-5 mini):** extracción de campos + OCR (`visible_text`) + re-rank visual.
- **Embeddings (text-embedding-3-small, 1536):** sobre el descriptor canónico de la foto.
- **Búsqueda:** pgvector (coseno) sobre `text_embedding`.

> OpenAI **no** ofrece embeddings de imagen. La similitud se hace describiendo la foto
> con Vision → embedding de ese texto → pgvector → **re-rank de Vision sobre las imágenes
> reales** del Top-K (recupera la precisión visual). Sin modelos locales: el despliegue
> es solo FastAPI + Postgres + API key. Detalle en `ARQUITECTURA.md` §2.

## Base de Datos

- PostgreSQL 16
- pgvector (vectores visual + texto)

## Almacenamiento

- Filesystem local (por defecto, sin costo)

Alternativas opcionales (configurables):

- Supabase Storage
- Amazon S3
- MinIO

## Canales

- WhatsApp Cloud API
- Telegram Bot API

---

# 6. Principio de Diseño

El sistema NO intentará responder:

> "¿Qué medicamento es?"

El sistema intentará responder:

> "¿A cuál de mis medicamentos registrados se parece más esta fotografía?"

Esto reduce enormemente la complejidad y aumenta la precisión.

---

# 7. Flujo de Registro

## Entrada

Usuario envía:

- receta médica
- caja
- blíster
- medicamento

## Proceso

```text
Foto
 ↓
OpenAI Vision
 ↓
Extracción de información
 ↓
Generación de descripción
 ↓
Generación de embedding
 ↓
Guardar imagen
 ↓
Guardar historial
```

## Información almacenada

- nombre detectado
- dosis
- presentación
- laboratorio
- texto visible
- forma
- color
- descripción generada por IA
- embedding
- fecha de registro
- URL de imagen

---

# 8. Flujo de Consulta

## Entrada

Usuario envía una foto y pregunta:

> "¿Cuándo compré esta pastilla?"

## Proceso

```text
Foto nueva
 ↓
OpenAI Vision
 ↓
Descripción estructurada
 ↓
Embedding textual
 ↓
pgvector
 ↓
Top 5 candidatos
 ↓
OpenAI Vision
 ↓
Comparación visual
 ↓
Top 3 resultados
 ↓
Respuesta al usuario
```

---

# 9. Estrategia de Similitud

> Modo **OpenAI-first**. La precisión visual la aporta el re-rank de Vision sobre las
> imágenes reales del Top-K. Ver `ARQUITECTURA.md` §2.

## Capa 1 — Descripción canónica (GPT Vision)

GPT Vision extrae campos (nombre, dosis, laboratorio, presentación, forma, color), el
texto visible (OCR incluido) y una descripción visual del empaque/blíster. Se concatena
en un descriptor canónico.

---

## Capa 2 — Embedding de texto (OpenAI)

`text-embedding-3-small` (1536) convierte el descriptor en vector. Señal indexable.

---

## Capa 3 — pgvector

Busca el Top-K por similitud coseno sobre `text_embedding`.

---

## Capa 4 — Re-rank visual (GPT Vision)

Compara la foto de consulta contra las **imágenes reales** del Top-K y devuelve mejor
coincidencia, porcentaje de confianza y explicación.

---

# 10. Manejo de Blísteres Cortados

El sistema debe considerar:

- forma del corte
- distribución de cavidades
- cantidad de pastillas visibles
- espacios vacíos
- texto visible parcial
- forma general del blíster

Ejemplo:

> "Coincide el patrón de corte en forma de L."

Este es uno de los principales diferenciadores del proyecto.

---

# 11. Experiencia de Usuario

## Caso 1 - Confianza Alta

```text
Posible coincidencia encontrada.

Medicamento: Amoxicilina 500mg
Fecha registrada: 08/06/2026
Confianza: Alta

¿Deseas ver la imagen original?
```

---

## Caso 2 - Confianza Media

Mostrar máximo 3 opciones.

```text
Encontré varias coincidencias.

1. Amoxicilina 500mg
2. Ampicilina 500mg
3. Dicloxacilina

Responde:
1
2
3
o ninguna
```

---

# 12. Comparación Visual para el Usuario

Cuando existan varias coincidencias:

## Generar collage automático

```text
┌─────────┬─────────┐
│ Opción1 │ Opción2 │
├─────────┼─────────┤
│ Opción3 │ Consulta│
└─────────┴─────────┘
```

Enviar:

- una sola imagen
- opciones numeradas

Beneficios:

- menos mensajes
- comparación visual rápida
- mejor experiencia en WhatsApp

---

# 13. Seguridad

El sistema nunca debe afirmar:

- "Esta pastilla es..."
- "Puedes tomarla."
- "Está en buen estado."

Siempre debe responder:

- "Posible coincidencia."
- "Verifica la fecha de vencimiento."
- "Consulta con un profesional de salud."

---

# 14. Modelo de Datos

Esquema completo en SQL: [`migrations/001_init.sql`](./migrations/001_init.sql).
Tablas principales:

- **users** — dueño del despliegue.
- **profiles** — perfiles (familia) desde el día 1.
- **channels** — vinculación Telegram / WhatsApp.
- **medicines** — producto canónico (dedup y agregación).
- **records** — cada evento de registro de foto.
- **record_images** — varias imágenes por registro, con hash para dedup.
- **record_embeddings** — vectores `image_embedding` (CLIP) + `text_embedding`,
  con versión de modelo para re-embeber.
- **purchases** — compra ≠ registro: incluye `expiry_date`, `lot_number`, precio,
  cantidad y farmacia.
- **queries** / **query_results** — log de consultas y feedback del usuario
  ("1/2/3/ninguna"), base para medir **precision@1** y **recall@5**.

Mejoras vs. v1.0: perfiles desde el inicio, separación compra/registro, vencimiento y
lote, dedup de imágenes, vectores visual+texto separados y captura de feedback.

---

# 15. Roadmap

## Sprint 1 - Infraestructura ✅ (en curso)

- Estructura del repo (FastAPI, config por env vars)
- Docker Compose: Postgres 16 + pgvector
- Modelos SQLAlchemy + migración SQL del esquema
- Capa de almacenamiento abstracta (local por defecto)
- Endpoint de salud (`/health`)

## Pipeline (núcleo) ✅

- módulo de IA OpenAI (Vision + embeddings)
- endpoints `/records`, `/query`, feedback y perfiles
- (cubre lo planeado para Vision, Embeddings, Similitud y Comparación Visual)

## Sprint 2 - Telegram ✅

- recepción de imágenes
- registro de medicamentos (botones de tipo)
- consulta con coincidencias y feedback (1/2/3/ninguna)
- webhook + script de polling para pruebas locales

## Sprint 2.5 - Interfaz Web 🆕 (en curso)

Objetivo: una experiencia amigable orientada al ámbito médico, más vistosa que Telegram,
que permita hacer el mismo trabajo (preguntar, capturar o enviar fotos) desde el navegador.

- **Landing** de presentación del proyecto (qué es, cómo funciona, aviso médico).
- **Chat web** con:
  - composición de mensajes con texto,
  - captura de foto desde la cámara (móvil) o subida de archivo,
  - modo **Consultar** (`/query`) y modo **Registrar** (`/records`),
  - tarjetas de candidatos con imagen, confianza, fecha y botones de feedback (1/2/3/ninguna).
- Stack: **React + Vite** (app separada), tema **clínico claro azul/teal**.
- Backend: endpoint para servir imágenes + CORS.
- Despliegue: build estático servido por nginx, con proxy `/api` al backend (servicio en
  `docker-compose`).

## Sprint 3 - OpenAI Vision ✅ (incluido en el pipeline)

- extracción de datos
- generación de descripciones

## Sprint 4 - Embeddings ✅ (incluido en el pipeline)

- text-embedding-3-small
- almacenamiento vectorial (pgvector)

## Sprint 5 - Similitud ✅ (incluido en el pipeline)

- búsqueda Top-K
- ranking por pgvector

## Sprint 6 - Comparación Visual ✅ (incluido en el pipeline)

- comparación de candidatos (re-rank de Vision)
- selección automática

## Sprint 7 - WhatsApp ✅

- webhook (verificación hub.challenge + recepción)
- descarga de imágenes (media con token)
- botones/listas interactivas, registro, consulta y feedback

## Sprint 8 - Collage Inteligente ✅

- generación de collage (Pillow): grilla numerada con la mejor coincidencia resaltada
- incluido en Telegram (foto + botones) y WhatsApp (lista con header de imagen)
- endpoint `GET /query/{id}/collage`
- selección asistida por usuario (feedback)

## Sprint 9 - Historial Familiar

- perfiles
- múltiples personas

---

# 16. Arquitectura Final MVP

```text
Telegram / WhatsApp
      ↓
    FastAPI  (Docker, self-hosted)
      ↓
  Pipeline async
   └─ OpenAI: GPT Vision (extracción + re-rank) + text-embedding-3-small
      ↓
PostgreSQL 16 + pgvector
      ↓
 Almacenamiento local (default) / Supabase / S3 / MinIO
```

---

# 17. Métrica de Éxito

El MVP será exitoso si puede:

- Registrar medicamentos mediante fotografías.
- Recuperar coincidencias históricas.
- Identificar correctamente la compra más probable.
- Mostrar evidencia visual al usuario.
- Reducir la incertidumbre sobre medicamentos almacenados.

---

# 18. Diferenciador Principal

La mayoría de soluciones intentan identificar medicamentos a nivel global.

**MemorIA Medicinal** se enfoca en algo más realista y útil:

> Recordar y reconocer medicamentos dentro del propio historial del usuario.

Esto permite obtener mejores resultados con menor complejidad y una experiencia mucho más útil para la vida diaria.