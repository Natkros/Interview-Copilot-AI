"use client";

import { useRouter } from "next/navigation";
import { ThemeProvider } from "next-themes";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import type { Preferences, User } from "@/types/api";

interface AuthState {
  user: User | null;
  loading: boolean;
  refresh: () => Promise<void>;
  setUser: (u: User | null) => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <Providers>");
  return ctx;
}

/** Applies accessibility preferences (font size, contrast, motion) to <html>. */
function applyPreferences(p: Preferences | undefined) {
  const root = document.documentElement;
  root.style.setProperty("--app-font-scale", String(p?.font_scale ?? 1));
  root.classList.toggle("hc", Boolean(p?.high_contrast));
  root.classList.toggle("reduce-motion", Boolean(p?.reduced_motion));
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const refresh = useCallback(async () => {
    try {
      setUser(await api<User>("/auth/me", { allowUnauthorized: true }));
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    api<User>("/auth/me", { allowUnauthorized: true })
      .then((u) => alive && setUser(u))
      .catch(() => alive && setUser(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    applyPreferences(user?.preferences);
  }, [user?.preferences]);

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST", allowUnauthorized: true });
    } finally {
      setUser(null);
      router.replace("/login");
    }
  }, [router]);

  const value = useMemo(() => ({ user, loading, refresh, setUser, logout }), [user, loading, refresh, logout]);

  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <TooltipProvider>
        <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
        <Toaster position="bottom-right" richColors closeButton />
      </TooltipProvider>
    </ThemeProvider>
  );
}
