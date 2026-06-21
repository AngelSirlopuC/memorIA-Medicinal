import { Link } from "react-router-dom";

function Icon({ path }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
      <path
        d={path}
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const FEATURES = [
  {
    icon: "M4 7h16M4 12h16M4 17h10",
    title: "Tu propio historial",
    text: "No intenta adivinar qué medicamento es en el mundo: reconoce a cuál de los tuyos, ya registrados, se parece la foto.",
  },
  {
    icon: "M3 7l9-4 9 4-9 4-9-4zm0 5l9 4 9-4M3 17l9 4 9-4",
    title: "Memoria con fechas",
    text: "Sabe cuándo registraste o compraste cada medicamento, para que no vuelvas a dudar de algo guardado hace meses.",
  },
  {
    icon: "M12 3a9 9 0 100 18 9 9 0 000-18zm0 5v4l3 2",
    title: "Respuestas en segundos",
    text: "Envía una foto desde el chat y obtén las coincidencias más probables, con su nivel de confianza.",
  },
];

const STEPS = [
  { h: "Registra", p: "Toma una foto de la receta, caja, blíster o pastilla. Se guarda en tu memoria." },
  { h: "Pregunta", p: "Más tarde, envía una foto y pregunta: ¿cuándo compré esta pastilla?" },
  { h: "Compara", p: "El asistente te muestra las coincidencias con imagen, fecha y confianza." },
];

export default function Landing() {
  return (
    <>
      <section className="hero">
        <span className="pill">Asistente de medicamentos · Privado y self-hosted</span>
        <h1>
          Tu <span className="grad">memoria inteligente</span> para medicamentos y recetas
        </h1>
        <p className="lead">
          ¿Guardaste una pastilla sin caja y ya no recuerdas qué era ni cuándo la
          compraste? MemorIA Medicinal recuerda por ti: registra tus medicamentos con una
          foto y luego reconoce a cuál de los tuyos se parece cualquier imagen.
        </p>
        <div className="cta-row">
          <Link to="/chat" className="btn btn-primary">
            Probar el asistente
          </Link>
          <a className="btn" href="#como-funciona">
            Cómo funciona
          </a>
        </div>

        <div className="channels">
          <span className="channels-label">Úsalo desde</span>
          <span className="channel-chip">💬 Chat web</span>
          <span className="channel-chip">✈️ Telegram</span>
          <span className="channel-chip">🟢 WhatsApp</span>
        </div>
      </section>

      <section className="section">
        <div className="cards">
          {FEATURES.map((f) => (
            <div className="card" key={f.title}>
              <div className="ico">
                <Icon path={f.icon} />
              </div>
              <h3>{f.title}</h3>
              <p>{f.text}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="section" id="como-funciona">
        <h2 className="h2">Cómo funciona</h2>
        <p className="sub">Tres pasos, desde el chat web, Telegram o WhatsApp.</p>
        <div className="steps">
          {STEPS.map((s) => (
            <div className="step" key={s.h}>
              <span className="num" />
              <div>
                <h4>{s.h}</h4>
                <p>{s.p}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="notice">
          <strong>Aviso médico.</strong> Este asistente no identifica medicamentos ni
          indica si pueden consumirse. Solo te ayuda a recordar tu propio historial.
          Verifica siempre la fecha de vencimiento y consulta con un profesional de salud.
        </div>
      </section>

      <footer className="footer">
        MemorIA Medicinal · Proyecto open source · Tus datos viven en tu propio servidor.
      </footer>
    </>
  );
}
