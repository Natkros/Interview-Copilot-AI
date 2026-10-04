"use client";

import { ArrowRight, Loader2, Mic, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/app-shell";
import { useAuth } from "@/components/providers";
import { ScoreValue } from "@/components/live/score";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AnswerLength, InterviewMode, InterviewSummary, Job } from "@/types/api";

const MODES: { mode: InterviewMode; title: string; description: string }[] = [
  { mode: "mock", title: "Mock Interview", description: "The AI interviews you across your profile and adapts to your answers." },
  { mode: "resume_drill", title: "Resume Drill", description: "Probing follow-ups that test every claim on your resume." },
  { mode: "technical", title: "Technical", description: "Questions on the technologies you know." },
  { mode: "dsa", title: "DSA", description: "Coding and algorithms practice." },
  { mode: "system_design", title: "System Design", description: "Architecture questions with deeper follow-ups." },
  { mode: "behavioral", title: "Behavioral", description: "STAR-style questions about real situations." },
  { mode: "hr", title: "HR", description: "Motivation, strengths, weaknesses and career goals." },
  { mode: "job_specific", title: "Job-Specific", description: "Questions generated from your target job description." },
  { mode: "live_coaching", title: "Live Coaching", description: "Listens to an interviewer and suggests grounded answers in real time." },
];

export default function InterviewsPage() {
  const router = useRouter();
  const { user } = useAuth();
  const [sessions, setSessions] = useState<InterviewSummary[] | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [mode, setMode] = useState<InterviewMode>("mock");
  const [jobId, setJobId] = useState<string>("");
  const [title, setTitle] = useState("");
  const [length, setLength] = useState<AnswerLength>(user?.preferences?.answer_length ?? "45s");
  const [consent, setConsent] = useState(false);
  const [creating, setCreating] = useState(false);

  const load = () => {
    api<InterviewSummary[]>("/interviews").then(setSessions).catch((e: Error) => toast.error(e.message));
  };
  useEffect(() => {
    load();
    api<Job[]>("/jobs").then((j) => {
      setJobs(j);
      if (j.length) setJobId(user?.preferences?.default_job_id ?? j[0].id);
    }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const create = async () => {
    setCreating(true);
    try {
      const s = await api<InterviewSummary>("/interviews", {
        json: { mode, job_id: jobId || null, title: title || null, answer_length: length, consent_acknowledged: mode === "live_coaching" ? consent : false },
      });
      router.push(`/interview/${s.id}`);
    } catch (e) {
      toast.error((e as Error).message);
      setCreating(false);
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm("Delete this interview, its transcript and report?")) return;
    try {
      await api(`/interviews/${id}`, { method: "DELETE" });
      toast.success("Interview deleted");
      load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Interviews" description="Practise with an AI interviewer, or get live, resume-grounded coaching." />
      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Start a session</CardTitle>
          <CardDescription>Choose a mode. Every answer is generated from your verified profile first.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div role="radiogroup" aria-label="Interview mode" className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {MODES.map((m) => (
              <button
                key={m.mode}
                type="button"
                role="radio"
                aria-checked={mode === m.mode}
                onClick={() => setMode(m.mode)}
                className={cn(
                  "rounded-xl border p-3 text-left transition-colors hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring",
                  mode === m.mode && "border-brand bg-brand/5 ring-1 ring-brand",
                )}
              >
                <div className="font-medium">{m.title}</div>
                <div className="mt-0.5 text-xs text-muted-foreground">{m.description}</div>
              </button>
            ))}
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="title">Title (optional)</Label>
              <Input id="title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Acme ML Engineer round 1" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="job">Target job</Label>
              <select id="job" className="h-8 w-full rounded-lg border bg-transparent px-2 text-sm" value={jobId} onChange={(e) => setJobId(e.target.value)}>
                <option value="">None</option>
                {jobs.map((j) => <option key={j.id} value={j.id}>{j.title}{j.company ? ` · ${j.company}` : ""}</option>)}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="length">Answer length</Label>
              <select id="length" className="h-8 w-full rounded-lg border bg-transparent px-2 text-sm" value={length} onChange={(e) => setLength(e.target.value as AnswerLength)}>
                <option value="20s">20 seconds</option>
                <option value="45s">45 seconds</option>
                <option value="90s">90 seconds</option>
                <option value="detailed">Detailed</option>
              </select>
            </div>
          </div>
          {mode === "live_coaching" ? (
            <div className="rounded-lg border border-warning/40 bg-warning/5 p-3">
              <label className="flex items-start gap-2.5 text-sm">
                <Checkbox checked={consent} onCheckedChange={(v) => setConsent(Boolean(v))} className="mt-0.5" />
                <span>
                  I&apos;m using live coaching for practice, coaching, accessibility, or an interview where AI assistance is explicitly
                  permitted. InterviewOS does not hide itself from screen sharing or proctoring.
                </span>
              </label>
            </div>
          ) : null}
          <Button onClick={() => void create()} disabled={creating || (mode === "live_coaching" && !consent)}>
            {creating ? <Loader2 className="animate-spin" /> : <Mic />} Start {MODES.find((m) => m.mode === mode)?.title}
          </Button>
        </CardContent>
      </Card>

      <h2 className="mb-3 text-lg font-semibold">Recent sessions</h2>
      {sessions === null ? (
        <Skeleton className="h-32 w-full" />
      ) : sessions.length === 0 ? (
        <p className="text-sm text-muted-foreground">No sessions yet.</p>
      ) : (
        <ul className="divide-y rounded-xl border">
          {sessions.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <Link href={`/interview/${s.id}`} className="font-medium hover:underline">{s.title}</Link>
                <div className="text-xs text-muted-foreground">
                  {s.mode_label} · {shortDate(s.created_at)} · {s.turn_count ?? 0} turns
                </div>
              </div>
              <Badge variant={s.status === "ended" ? "secondary" : "outline"} className="capitalize">{s.status}</Badge>
              <span className="w-14 text-right text-sm"><ScoreValue value={s.overall} /></span>
              <Button variant="ghost" size="icon-sm" aria-label={`Delete ${s.title}`} onClick={() => void remove(s.id)}><Trash2 /></Button>
              <Button variant="outline" size="sm" render={<Link href={`/interview/${s.id}`} />}>
                {s.status === "ended" ? "Review" : "Open"} <ArrowRight />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
