"use client";

import { ArrowLeft, Check, Mic, RefreshCw, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/app-shell";
import { ScoreBar, ScoreValue } from "@/components/live/score";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { InterviewSummary, Job } from "@/types/api";

function List({ title, items }: { title: string; items: string[] }) {
  if (!items?.length) return null;
  return (
    <div>
      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
      <ul className="list-disc space-y-1 pl-5 text-sm">{items.map((i) => <li key={i}>{i}</li>)}</ul>
    </div>
  );
}

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);

  useEffect(() => {
    api<Job>(`/jobs/${id}`).then(setJob).catch((e: Error) => toast.error(e.message));
  }, [id]);

  if (!job) return <Skeleton className="h-96 w-full" />;
  const p = job.parsed!;
  const m = job.match;

  const startInterview = async () => {
    try {
      const s = await api<InterviewSummary>("/interviews", { json: { mode: "job_specific", job_id: job.id, title: `${job.title} - practice` } });
      router.push(`/interview/${s.id}`);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader
        title={job.title}
        description={[job.company, p.domain].filter(Boolean).join(" · ") || undefined}
        actions={
          <>
            <Button variant="outline" render={<Link href="/jobs" />}><ArrowLeft /> Jobs</Button>
            <Button variant="outline" onClick={async () => setJob(await api<Job>(`/jobs/${id}/rematch`, { method: "POST" }))}><RefreshCw /> Recompute match</Button>
            <Button onClick={() => void startInterview()}><Mic /> Job-specific interview</Button>
          </>
        }
      />
      <div className="grid gap-4 md:grid-cols-[280px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">Resume ↔ job match</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="text-4xl font-semibold"><ScoreValue value={m.score} /></div>
            <ScoreBar label="Required skills" value={m.required_coverage} />
            <ScoreBar label="Preferred skills" value={m.preferred_coverage} />
            <p className="text-xs text-muted-foreground">{m.method}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Skills</CardTitle>
            <CardDescription>Verified skills count fully; unverified count half. Evidence shows where each appears in your profile.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div>
              <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Required</h3>
              <ul className="flex flex-wrap gap-1.5">
                {p.required_skills.map((s) => {
                  const ok = m.matched.includes(s);
                  return (
                    <li key={s}>
                      <Badge variant={ok ? "secondary" : "outline"} title={m.evidence[s.toLowerCase()]?.join(", ")}>
                        {ok ? <Check className="text-success" /> : <X className="text-destructive" />} {s}
                      </Badge>
                    </li>
                  );
                })}
                {!p.required_skills.length ? <li className="text-muted-foreground">No specific technologies detected.</li> : null}
              </ul>
            </div>
            {p.preferred_skills.length ? (
              <div>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Preferred</h3>
                <ul className="flex flex-wrap gap-1.5">
                  {p.preferred_skills.map((s) => (
                    <li key={s}>
                      <Badge variant={m.preferred_matched.includes(s) ? "secondary" : "outline"}>
                        {m.preferred_matched.includes(s) ? <Check className="text-success" /> : <X className="text-muted-foreground" />} {s}
                      </Badge>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {m.missing.length ? (
              <p className="rounded-lg bg-muted/60 p-2.5 text-xs">
                Missing required skills: <strong>{m.missing.join(", ")}</strong>. Be ready to explain how you&apos;d ramp up - don&apos;t claim experience you don&apos;t have.
              </p>
            ) : null}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Extracted requirements</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-6 md:grid-cols-2">
          <List title="Responsibilities" items={p.responsibilities} />
          <List title="Requirements" items={p.required_items} />
          <List title="Nice to have" items={p.preferred_items} />
          <List title="Experience" items={p.experience} />
          <List title="Education" items={p.education} />
          <div>
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Keywords</h3>
            <div className="flex flex-wrap gap-1">{p.keywords.map((k) => <Badge key={k} variant="outline">{k}</Badge>)}</div>
          </div>
        </CardContent>
      </Card>
      <details className="rounded-xl border p-4 text-sm">
        <summary className="cursor-pointer font-medium">Original text</summary>
        <pre className="mt-3 whitespace-pre-wrap font-sans text-muted-foreground">{job.raw_text}</pre>
      </details>
      <Button
        variant="ghost"
        className="text-destructive"
        onClick={async () => {
          if (!window.confirm("Delete this job description?")) return;
          await api(`/jobs/${id}`, { method: "DELETE" });
          toast.success("Job deleted");
          router.push("/jobs");
        }}
      >
        <Trash2 /> Delete job
      </Button>
    </div>
  );
}
