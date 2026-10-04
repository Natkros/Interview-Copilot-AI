"use client";

import { Download, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/app-shell";
import { useAuth } from "@/components/providers";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";
import type { Preferences, SystemStatus } from "@/types/api";

function Row({ label, description, children, htmlFor }: { label: string; description?: string; children: React.ReactNode; htmlFor?: string }) {
  return (
    <div className="flex items-center justify-between gap-6 py-3">
      <div>
        <Label htmlFor={htmlFor}>{label}</Label>
        {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
      </div>
      {children}
    </div>
  );
}

export default function SettingsPage() {
  const { user, setUser } = useAuth();
  const { theme, setTheme } = useTheme();
  const router = useRouter();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [confirmEmail, setConfirmEmail] = useState("");
  const prefs: Preferences = user?.preferences ?? {};

  useEffect(() => {
    api<SystemStatus>("/system/status").then(setStatus).catch(() => undefined);
  }, []);

  const update = async (patch: Preferences) => {
    if (!user) return;
    try {
      const r = await api<{ preferences: Preferences }>("/settings", { method: "PATCH", json: patch });
      setUser({ ...user, preferences: r.preferences });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader title="Settings" />
      <Card>
        <CardHeader>
          <CardTitle>Answers</CardTitle>
        </CardHeader>
        <CardContent className="divide-y">
          <Row label="Default answer length" htmlFor="len" description="Approximate speaking time of suggested answers.">
            <select id="len" className="h-8 rounded-lg border bg-transparent px-2 text-sm" value={prefs.answer_length ?? "45s"} onChange={(e) => void update({ answer_length: e.target.value as Preferences["answer_length"] })}>
              <option value="20s">20 seconds</option>
              <option value="45s">45 seconds</option>
              <option value="90s">90 seconds</option>
              <option value="detailed">Detailed</option>
            </select>
          </Row>
          <Row label="Read mock-interview questions aloud" htmlFor="voice">
            <Switch id="voice" checked={prefs.interviewer_voice !== false} onCheckedChange={(v) => void update({ interviewer_voice: Boolean(v) })} />
          </Row>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Accessibility & appearance</CardTitle>
        </CardHeader>
        <CardContent className="divide-y">
          <Row label="Theme" htmlFor="theme">
            <select id="theme" className="h-8 rounded-lg border bg-transparent px-2 text-sm" value={theme ?? "system"} onChange={(e) => setTheme(e.target.value)}>
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </Row>
          <Row label="Text size" htmlFor="font" description={`${Math.round((prefs.font_scale ?? 1) * 100)}%`}>
            <input
              id="font"
              type="range"
              min={0.85}
              max={1.5}
              step={0.05}
              value={prefs.font_scale ?? 1}
              onChange={(e) => {
                document.documentElement.style.setProperty("--app-font-scale", e.target.value);
              }}
              onMouseUp={(e) => void update({ font_scale: Number((e.target as HTMLInputElement).value) })}
              onKeyUp={(e) => void update({ font_scale: Number((e.target as HTMLInputElement).value) })}
              className="w-40"
            />
          </Row>
          <Row label="High contrast" htmlFor="hc" description="Stronger borders and text contrast.">
            <Switch id="hc" checked={Boolean(prefs.high_contrast)} onCheckedChange={(v) => void update({ high_contrast: Boolean(v) })} />
          </Row>
          <Row label="Reduce motion" htmlFor="rm" description="Disables animations (also follows your OS setting).">
            <Switch id="rm" checked={Boolean(prefs.reduced_motion)} onCheckedChange={(v) => void update({ reduced_motion: Boolean(v) })} />
          </Row>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Privacy</CardTitle>
          <CardDescription>
            Raw audio is {status?.store_raw_audio ? "stored only when you enable recording" : "never stored"}. Transcripts, resumes and reports are yours to export or delete.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Row label="Store interview recordings" htmlFor="rec" description={status?.store_raw_audio ? "Off by default." : "Recording storage is disabled on this server."}>
            <Switch id="rec" checked={Boolean(prefs.store_recordings)} disabled={!status?.store_raw_audio} onCheckedChange={(v) => void update({ store_recordings: Boolean(v) })} />
          </Row>
          <div className="flex flex-wrap gap-2 pt-2">
            <Button variant="outline" render={<a href="/api/privacy/export" download />}><Download /> Export my data</Button>
            <Dialog>
              <DialogTrigger render={<Button variant="destructive" />}><Trash2 /> Delete account</DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Delete your account?</DialogTitle>
                  <DialogDescription>
                    This permanently deletes your profile, resumes, documents, job descriptions, interviews, transcripts, reports, practice history and search index. It cannot be undone.
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-1">
                  <Label htmlFor="confirm">Type your email to confirm</Label>
                  <Input id="confirm" value={confirmEmail} onChange={(e) => setConfirmEmail(e.target.value)} autoComplete="off" />
                </div>
                <DialogFooter>
                  <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
                  <Button
                    variant="destructive"
                    disabled={confirmEmail.trim().toLowerCase() !== user?.email}
                    onClick={async () => {
                      try {
                        await api("/account", { method: "DELETE" });
                        setUser(null);
                        router.replace("/");
                      } catch (e) {
                        toast.error((e as Error).message);
                      }
                    }}
                  >
                    Permanently delete
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>
          <p className="text-xs text-muted-foreground">Delete individual items from the Resume, Jobs and Interviews pages (resume, transcript, interview, documents).</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>System status</CardTitle>
          <CardDescription>Which providers this server is configured with.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2 text-sm sm:grid-cols-2">
          {status ? (
            <>
              <div className="flex justify-between"><span className="text-muted-foreground">Answer generation</span><span>{status.llm.provider === "offline" ? <Badge variant="outline">Offline extractive</Badge> : status.llm.model}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Speech recognition</span><span>{status.stt.provider}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Embeddings</span><span>{status.embeddings.provider}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Vector search</span><span>{status.vector_store.available ? "available" : "keyword fallback"}</span></div>
            </>
          ) : (
            <span className="text-muted-foreground">Loading…</span>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
