import { useEffect, useRef, useState } from "react";
import { sendAgentMessage, sendFeedback } from "../api.js";
import CandidateCard from "../components/CandidateCard.jsx";
import { useProfiles } from "../ProfileContext.jsx";

let _id = 0;
const nextId = () => `m${++_id}`;
const CONV_KEY = "memoria.convId";

function convId() {
  let id = localStorage.getItem(CONV_KEY);
  if (!id) {
    id = "web:" + (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36));
    localStorage.setItem(CONV_KEY, id);
  }
  return id;
}

// Renderiza *negritas* simples del texto del agente.
function rich(text) {
  const parts = String(text).split(/(\*[^*]+\*)/g);
  return parts.map((p, i) =>
    p.startsWith("*") && p.endsWith("*") ? <strong key={i}>{p.slice(1, -1)}</strong> : p
  );
}

export default function Chat() {
  const { activeId } = useProfiles();
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [prescriptionOpen, setPrescriptionOpen] = useState(false);

  const galleryRef = useRef(null);
  const cameraRef = useRef(null);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const push = (msg) => setMessages((m) => [...m, { id: nextId(), ...msg }]);

  function onPick(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f);
    setPreview(URL.createObjectURL(f));
    e.target.value = "";
  }

  function clearAttach() {
    setFile(null);
    if (preview) URL.revokeObjectURL(preview);
    setPreview(null);
  }

  async function handleFeedback(queryId, recordId, msgId) {
    try {
      await sendFeedback(queryId, recordId);
      setMessages((ms) => ms.map((m) => (m.id === msgId ? { ...m, selectedId: recordId } : m)));
    } catch (err) {
      push({ kind: "info", text: `No se pudo guardar tu elección: ${err.message}` });
    }
  }

  async function onSend() {
    if (busy) return;
    const question = text.trim();
    if (!question && !file) return;

    push({ kind: "user", text: question, imageUrl: preview });
    const sentFile = file;
    setText("");
    setFile(null);
    setPreview(null);
    setBusy(true);

    try {
      const r = await sendAgentMessage(convId(), question, sentFile, { profileId: activeId });
      setPrescriptionOpen(!!r.prescription_open);
      (r.replies || []).forEach((t) => push({ kind: "bot", text: t }));
      if (r.query && r.query.candidates && r.query.candidates.length) {
        push({
          kind: "candidates",
          queryId: r.query.query_id,
          bestId: r.query.best_record_id,
          candidates: r.query.candidates,
          disclaimer: r.query.disclaimer,
          selectedId: null,
        });
      }
    } catch (err) {
      push({ kind: "bot", text: `Ocurrió un error: ${err.message}` });
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  }

  return (
    <div className="chat-wrap">
      <div className="messages">
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="big">💬</div>
            <p>
              Cuéntame en lenguaje natural. Por ejemplo: <em>"Hoy Thiago tuvo cita y le
              recetaron esto"</em> y adjunta la foto de la receta; luego envía una foto de
              cada medicina. O pregunta <em>"¿cuándo compré esta pastilla?"</em> con una foto.
            </p>
          </div>
        )}

        {messages.map((m) => {
          if (m.kind === "info") {
            return (
              <div key={m.id} className="bubble info">
                {m.text}
              </div>
            );
          }
          if (m.kind === "candidates") {
            return (
              <div key={m.id} className="bubble bot">
                <div className="candidates">
                  {m.candidates.map((c) => (
                    <CandidateCard
                      key={c.record_id}
                      candidate={c}
                      isBest={c.record_id === m.bestId}
                      selected={m.selectedId === c.record_id}
                      onSelect={(rid) => handleFeedback(m.queryId, rid, m.id)}
                    />
                  ))}
                </div>
                {!m.selectedId && (
                  <div className="actions" style={{ marginTop: 8 }}>
                    <button className="chip" onClick={() => handleFeedback(m.queryId, null, m.id)}>
                      Ninguna
                    </button>
                  </div>
                )}
                {m.disclaimer && <div className="disclaimer">{m.disclaimer}</div>}
              </div>
            );
          }
          return (
            <div key={m.id} className={`bubble ${m.kind === "user" ? "user" : "bot"}`}>
              {rich(m.text)}
              {m.imageUrl && <img className="thumb" src={m.imageUrl} alt="" />}
            </div>
          );
        })}

        {busy && (
          <div className="bubble bot">
            <span className="typing">
              <span />
              <span />
              <span />
            </span>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="composer">
        {prescriptionOpen && (
          <div className="attach-preview" style={{ background: "#e7f6ee", color: "#15a36e" }}>
            📋 Receta abierta · envía las fotos de cada medicina o escribe "listo"
          </div>
        )}
        {preview && (
          <div className="attach-preview">
            <img src={preview} alt="" />
            foto adjunta
            <button onClick={clearAttach} aria-label="Quitar foto">
              ✕
            </button>
          </div>
        )}

        <div className="input-row">
          <input ref={galleryRef} type="file" accept="image/*" hidden onChange={onPick} />
          <input
            ref={cameraRef}
            type="file"
            accept="image/*"
            capture="environment"
            hidden
            onChange={onPick}
          />
          <button className="icon-btn" title="Subir foto" onClick={() => galleryRef.current?.click()}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path
                d="M4 16l4-4 4 4 3-3 5 5M4 8h.01M3 6a2 2 0 012-2h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V6z"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
          <button className="icon-btn" title="Tomar foto" onClick={() => cameraRef.current?.click()}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path
                d="M3 8a2 2 0 012-2h2l1.2-1.6A1 1 0 019 4h6a1 1 0 01.8.4L17 6h2a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V8z"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinejoin="round"
              />
              <circle cx="12" cy="12.5" r="3.2" stroke="currentColor" strokeWidth="1.7" />
            </svg>
          </button>
          <textarea
            rows={1}
            placeholder="Escribe un mensaje o adjunta una foto…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="send" onClick={onSend} disabled={busy} aria-label="Enviar">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path
                d="M4 12l16-8-6 16-2.5-6.5L4 12z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
