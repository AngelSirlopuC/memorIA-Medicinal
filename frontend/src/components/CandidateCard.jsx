import { imageUrl } from "../api.js";

function confLabel(c) {
  if (c == null) return null;
  if (c >= 0.75) return { txt: "Confianza alta", cls: "conf-alta" };
  if (c >= 0.45) return { txt: "Confianza media", cls: "conf-media" };
  return { txt: "Confianza baja", cls: "conf-baja" };
}

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("es-PE", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

export default function CandidateCard({ candidate, isBest, onSelect, selected }) {
  const conf = candidate.vision_confidence ?? candidate.vector_score;
  const label = confLabel(conf);
  const pct = conf != null ? Math.round(conf * 100) : 0;
  const img = imageUrl(candidate.image_url);

  return (
    <div className={`cand${isBest ? " best" : ""}`}>
      {img ? <img src={img} alt="" loading="lazy" /> : <div className="img" />}
      <div className="info">
        <div className="title-row">
          <span className="name">{candidate.name || `Registro #${candidate.rank}`}</span>
          <span className="date">{formatDate(candidate.registered_at)}</span>
        </div>
        {candidate.reason && <div className="reason">{candidate.reason}</div>}
        {label && (
          <>
            <div className="confbar">
              <span style={{ width: `${pct}%` }} />
            </div>
            <span className={`conf-label ${label.cls}`}>
              {label.txt} · {pct}%
            </span>
          </>
        )}
        {selected ? (
          <div className="feedback-done">✓ Marcada como tu elección</div>
        ) : (
          <div className="actions">
            <button className="chip" onClick={() => onSelect(candidate.record_id)}>
              Es esta
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
