import { useEffect, useRef, useState } from "react";
import { queryMedicine, registerRecord, sendFeedback } from "../api.js";
import CandidateCard from "../components/CandidateCard.jsx";

let _id = 0;
const nextId = () => `m${++_id}`;

const SOURCES = [
  { value: "blister", label: "Blíster" },
  { value: "caja", label: "Caja" },
  { value: "receta", label: "Receta" },
  { value: "pastilla", label: "Pastilla" },
];

export default function Chat() {
  const [messages, setMessages] = useState([]);
  const [mode, setMode] = useState("consultar"); // consultar | registrar
  const [sourceType, setSourceType] = useState("blister");
  const [text, setText] = useState("");
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);

  const galleryRef = useRef(null);
  const cameraRef = useRef(null);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  function push(msg) {
    setMessages((m) => [...m, { id: nextId(), ...msg }]);
  }

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
      setMessages((ms) =>
        ms.map((m) => (m.id === msgId ? { ...m, selectedId: recordId } : m))
      );
    } catch (err) {
      push({ role: "info", kind: "info", text: `No se pudo guardar tu elección: ${err.message}` });
    }
  }

  async function onSend() {
    if (busy) return;
    if (!file) {
      push({
        role: "info",
        kind: "info",
        text:
          mode === "consultar"
            ? "Adjunta una foto del medicamento para buscar en tu historial."
            : "Adjunta una foto para registrar el medicamento.",
      });
      return;
    }

    const userPreview = preview;
    const question = text.trim();
    push({ role: "user", kind: "image", text: question, imageUrl: userPreview });

    const sentFile = file;
    const sentMode = mode;
    const sentSource = sourceType;
    setText("");
    setFile(null);
    setPreview(null);
    setBusy(true);

    try {
      if (sentMode === "registrar") {
        const r = await registerRecord(sentFile, sentSource);
        push({
          role: "bot",
          kind: "text",
          text: r.deduplicated
            ? "Esta foto ya estaba registrada en tu memoria."
            : `Registrado ✓ ${r.name ? `· ${r.name}${r.dose ? " " + r.dose : ""}` : ""}`.trim(),
        });
      } else {
        const r = await queryMedicine(sentFile, question);
        if (!r.candidates || r.candidates.length === 0) {
          push({
            role: "bot",
            kind: "text",
            text: "No encontré coincidencias en tu historial todavía. Registra el medicamento para reconocerlo después.",
          });
        } else {
          push({
            role: "bot",
            kind: "candidates",
            queryId: r.query_id,
            bestId: r.best_record_id,
            candidates: r.candidates,
            selectedId: null,
            disclaimer: r.disclaimer,
          });
        }
      }
    } catch (err) {
      push({ role: "bot", kind: "text", text: `Ocurrió un error: ${err.message}` });
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
            <div className="big">💊</div>
            <p>
              Envía una foto de un medicamento y pregunta lo que necesites, o cambia a{" "}
              <strong>Registrar</strong> para guardarlo en tu memoria.
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
                Encontré {m.candidates.length === 1 ? "una posible coincidencia" : "estas posibles coincidencias"}:
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
                <div className="disclaimer">{m.disclaimer}</div>
              </div>
            );
          }
          return (
            <div key={m.id} className={`bubble ${m.role === "user" ? "user" : "bot"}`}>
              {m.text}
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
        <div className="mode-row">
          <div className="toggle">
            <button
              className={mode === "consultar" ? "active" : ""}
              onClick={() => setMode("consultar")}
            >
              Consultar
            </button>
            <button
              className={mode === "registrar" ? "active" : ""}
              onClick={() => setMode("registrar")}
            >
              Registrar
            </button>
          </div>
          {mode === "registrar" && (
            <select
              className="select"
              value={sourceType}
              onChange={(e) => setSourceType(e.target.value)}
              aria-label="Tipo de foto"
            >
              {SOURCES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          )}
        </div>

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
          <input
            ref={galleryRef}
            type="file"
            accept="image/*"
            hidden
            onChange={onPick}
          />
          <input
            ref={cameraRef}
            type="file"
            accept="image/*"
            capture="environment"
            hidden
            onChange={onPick}
          />
          <button
            className="icon-btn"
            title="Subir foto"
            onClick={() => galleryRef.current?.click()}
          >
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
          <button
            className="icon-btn"
            title="Tomar foto"
            onClick={() => cameraRef.current?.click()}
          >
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
            placeholder={
              mode === "consultar"
                ? "¿Cuándo compré esta pastilla? (adjunta la foto)"
                : "Nota opcional sobre el medicamento…"
            }
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
