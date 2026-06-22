import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getHistory, imageUrl } from "../api.js";
import { useProfiles } from "../ProfileContext.jsx";

const SOURCE_LABELS = {
  receta: "Receta",
  caja: "Caja",
  blister: "Blíster",
  pastilla: "Pastilla",
};

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("es-PE", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

export default function History() {
  const { active, activeId } = useProfiles();
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!activeId) {
      setRecords([]);
      return;
    }
    setLoading(true);
    setError(null);
    getHistory(activeId)
      .then(setRecords)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [activeId]);

  return (
    <div className="history-wrap">
      <div className="history-head">
        <div>
          <h2 className="h2">Historial {active ? `· ${active.display_name}` : ""}</h2>
          <p className="sub">Medicamentos registrados en tu memoria.</p>
        </div>
        <Link to="/chat" className="btn btn-primary">
          + Registrar
        </Link>
      </div>

      {loading && <p className="sub">Cargando…</p>}
      {error && <p className="sub">No se pudo cargar: {error}</p>}

      {!loading && !error && records.length === 0 && (
        <div className="empty-state" style={{ marginTop: 40 }}>
          <div className="big">🗂️</div>
          <p>
            Aún no hay medicamentos registrados para este perfil. Ve al{" "}
            <Link to="/chat">chat</Link> y registra el primero.
          </p>
        </div>
      )}

      <div className="history-grid">
        {records.map((r) => (
          <div className="hist-card" key={r.record_id}>
            {imageUrl(r.image_url) ? (
              <img src={imageUrl(r.image_url)} alt="" loading="lazy" />
            ) : (
              <div className="hist-img-empty">💊</div>
            )}
            <div className="hist-info">
              <div className="hist-name">{r.name || "Medicamento"}{r.dose ? ` ${r.dose}` : ""}</div>
              <div className="hist-meta">
                <span className="tag">{SOURCE_LABELS[r.source_type] || r.source_type}</span>
                <span>{formatDate(r.registered_at)}</span>
              </div>
              {r.notes && <div className="hist-notes">{r.notes}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
