"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { fetchMe, signOut as apiSignOut } from "@/lib/api";
import type { User } from "@/lib/types";

/**
 * Who is signed in.
 *
 * The session itself is an HttpOnly cookie the page cannot read, so "am I
 * signed in?" is a question only the server can answer. That is why this
 * starts in a loading state rather than assuming signed-out: rendering a
 * sign-in form to someone who is already signed in, for the moment it takes to
 * find out, is worse than rendering nothing.
 */

type SessionValue = {
  user: User | null;
  loading: boolean;
  /** Called after sign-in or registration, which return the user directly. */
  setUser: (user: User | null) => void;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
};

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setUser(await fetchMe());
    } catch {
      // A network failure is not a signed-out user, but there is nothing this
      // layer can do about it and the screens all read as signed out anyway.
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signOut = useCallback(async () => {
    try {
      await apiSignOut();
    } finally {
      // Clear locally whatever the server said. A failed sign-out that leaves
      // the screen looking signed in is the worse of the two outcomes.
      setUser(null);
    }
  }, []);

  const value = useMemo<SessionValue>(
    () => ({ user, loading, setUser, signOut, refresh }),
    [user, loading, signOut, refresh],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside a SessionProvider");
  return value;
}
