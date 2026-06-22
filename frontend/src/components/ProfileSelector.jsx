import { useProfiles } from "../ProfileContext.jsx";

export default function ProfileSelector() {
  const { profiles, activeId, setActiveId, createProfile } = useProfiles();

  async function onChange(e) {
    const v = e.target.value;
    if (v === "__new__") {
      const name = window.prompt("Nombre del nuevo perfil (p. ej. María):");
      if (name && name.trim()) {
        try {
          await createProfile(name.trim());
        } catch (err) {
          alert("No se pudo crear el perfil: " + err.message);
        }
      }
      return;
    }
    setActiveId(v);
  }

  return (
    <select
      className="profile-select"
      value={activeId || ""}
      onChange={onChange}
      aria-label="Perfil activo"
      title="Perfil activo"
    >
      {profiles.length === 0 && <option value="">Perfil por defecto</option>}
      {profiles.map((p) => (
        <option key={p.id} value={p.id}>
          👤 {p.display_name}
        </option>
      ))}
      <option value="__new__">➕ Nuevo perfil…</option>
    </select>
  );
}
