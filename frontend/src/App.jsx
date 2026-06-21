import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

function Logo() {
  return (
    <span className="logo" aria-hidden>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
        <path
          d="M12 21s-7-4.35-9.33-9.06C1.3 9.27 2.36 6 5.5 6c1.9 0 3.1 1.06 3.9 2.2C10.2 7.06 11.4 6 13.3 6c.5 0 .96.08 1.38.23"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M15 12h2.5l1.5-2.2L21 15h2"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

export default function App() {
  const { pathname } = useLocation();
  return (
    <div className="app">
      <header className="header">
        <Link to="/" className="brand">
          <Logo />
          MemorIA Medicinal
        </Link>
        <nav className="nav">
          {pathname !== "/chat" ? (
            <NavLink to="/chat" className="btn btn-primary">
              Abrir asistente
            </NavLink>
          ) : (
            <NavLink to="/" className="btn btn-ghost">
              ← Inicio
            </NavLink>
          )}
        </nav>
      </header>
      <Outlet />
    </div>
  );
}
