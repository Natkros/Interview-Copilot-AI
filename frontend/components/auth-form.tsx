"use client";

import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Logo } from "@/components/app-shell";
import { useAuth } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { User } from "@/types/api";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const params = useSearchParams();
  const { user, setUser } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(params.get("expired") ? "Your session expired - please sign in again." : null);
  const [busy, setBusy] = useState(false);
  const next = params.get("next") || (mode === "register" ? "/resume" : "/dashboard");
  const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";

  useEffect(() => {
    if (user) router.replace(safeNext);
  }, [user, router, safeNext]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const u = await api<User>(`/auth/${mode}`, { json: { email, password }, allowUnauthorized: true });
      setUser(u);
      router.replace(safeNext);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <Link href="/" className="mb-2"><Logo /></Link>
          <CardTitle>{mode === "login" ? "Sign in" : "Create your account"}</CardTitle>
          <CardDescription>{mode === "login" ? "Welcome back." : "Start with your resume - everything else builds on it."}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-describedby={mode === "register" ? "pw-hint" : undefined}
              />
              {mode === "register" ? <p id="pw-hint" className="text-xs text-muted-foreground">At least 8 characters with letters and numbers.</p> : null}
            </div>
            {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
            <Button type="submit" className="w-full" disabled={busy || !email || !password}>
              {busy ? <Loader2 className="animate-spin" /> : null} {mode === "login" ? "Sign in" : "Create account"}
            </Button>
          </form>
          <p className="mt-4 text-center text-sm text-muted-foreground">
            {mode === "login" ? (
              <>No account? <Link className="underline" href="/register">Create one</Link></>
            ) : (
              <>Already registered? <Link className="underline" href="/login">Sign in</Link></>
            )}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
