// Cliente de la API de MemorIA Medicinal.
const API_BASE = import.meta.env.VITE_API_BASE || "/api";

async function handle(res) {
  if (!res.ok) {
    let detail = `Error ${res.status}`;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {
      /* sin cuerpo JSON */
    }
    throw new Error(detail);
  }
  return res;
}

// Convierte la ruta de almacenamiento del backend en una URL servible.
export function imageUrl(storagePath) {
  if (!storagePath) return null;
  const name = storagePath.replace(/\\/g, "/").split("/").pop();
  return `${API_BASE}/images/${name}`;
}

export async function registerRecord(file, sourceType, { profileId } = {}) {
  const fd = new FormData();
  fd.append("image", file);
  fd.append("source_type", sourceType);
  if (profileId) fd.append("profile_id", profileId);
  const res = await handle(await fetch(`${API_BASE}/records`, { method: "POST", body: fd }));
  return res.json();
}

export async function queryMedicine(file, question, { profileId } = {}) {
  const fd = new FormData();
  fd.append("image", file);
  if (question) fd.append("question", question);
  if (profileId) fd.append("profile_id", profileId);
  const res = await handle(await fetch(`${API_BASE}/query`, { method: "POST", body: fd }));
  return res.json();
}

export async function sendFeedback(queryId, selectedRecordId) {
  await handle(
    await fetch(`${API_BASE}/query/${queryId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected_record_id: selectedRecordId }),
    })
  );
}

export async function health() {
  const res = await handle(await fetch(`${API_BASE}/health`));
  return res.json();
}
