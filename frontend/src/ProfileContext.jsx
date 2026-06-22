import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { createProfile as apiCreate, listProfiles } from "./api.js";

const ProfileContext = createContext(null);
const LS_KEY = "memoria.activeProfileId";

export function ProfileProvider({ children }) {
  const [profiles, setProfiles] = useState([]);
  const [activeId, setActiveIdState] = useState(() => localStorage.getItem(LS_KEY) || null);

  const setActiveId = useCallback((id) => {
    setActiveIdState(id);
    if (id) localStorage.setItem(LS_KEY, id);
    else localStorage.removeItem(LS_KEY);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const data = await listProfiles();
      setProfiles(data);
      setActiveIdState((cur) => {
        if (cur && data.some((p) => p.id === cur)) return cur;
        const first = data[0]?.id || null;
        if (first) localStorage.setItem(LS_KEY, first);
        return first;
      });
      return data;
    } catch {
      return [];
    }
  }, []);

  const createProfile = useCallback(
    async (name, relation) => {
      const p = await apiCreate(name, relation);
      await refresh();
      setActiveId(p.id);
      return p;
    },
    [refresh, setActiveId]
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  const active = profiles.find((p) => p.id === activeId) || null;

  return (
    <ProfileContext.Provider
      value={{ profiles, activeId, active, setActiveId, refresh, createProfile }}
    >
      {children}
    </ProfileContext.Provider>
  );
}

export function useProfiles() {
  const ctx = useContext(ProfileContext);
  if (!ctx) throw new Error("useProfiles debe usarse dentro de ProfileProvider");
  return ctx;
}
