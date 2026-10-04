"use client";

import { ArrowRight, Loader2, Plus, Upload } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/app-shell";
import { ScoreValue } from "@/components/live/score";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { Job } from "@/types/api";

export default function JobsPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api<Job[]>("/jobs").then(setJobs).catch((e: Error) => toast.error(e.message));
  }, []);

  const submit = async (f?: File) => {
    const form = new FormData();
    if (f) form.append("file", f);
    else form.append("text", text);
    if (title) form.append("title", title);
    if (company) form.append("company", company);
    setBusy(true);
    try {
      const job = await api<Job>("/jobs", { form });
      toast.success("Job description analysed");
      router.push(`/jobs/${job.id}`);
    } catch (e) {
      toast.error((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader title="Target jobs" description="Add a job description to tailor answers, generate job-specific questions and measure your match." />
      <Card>
        <CardHeader>
          <CardTitle>Add a job description</CardTitle>
          <CardDescription>Paste the text, or upload a PDF / DOCX / TXT.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="jt">Role title (optional)</Label>
              <Input id="jt" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Detected automatically if empty" />
            </div>
            <div className="space-y-1">
              <Label htmlFor="jc">Company (optional)</Label>
              <Input id="jc" value={company} onChange={(e) => setCompany(e.target.value)} />
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="jd">Job description</Label>
            <Textarea id="jd" rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the full job description…" />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void submit()} disabled={busy || text.trim().length < 40}>
              {busy ? <Loader2 className="animate-spin" /> : <Plus />} Analyse
            </Button>
            <input ref={file} type="file" accept=".pdf,.docx,.txt" className="sr-only" aria-label="Upload job description file" onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void submit(f);
            }} />
            <Button variant="outline" onClick={() => file.current?.click()} disabled={busy}><Upload /> Upload file</Button>
          </div>
        </CardContent>
      </Card>

      <section>
        <h2 className="mb-3 text-lg font-semibold">Saved jobs</h2>
        {jobs === null ? (
          <Skeleton className="h-24 w-full" />
        ) : jobs.length ? (
          <ul className="divide-y rounded-xl border">
            {jobs.map((j) => (
              <li key={j.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <Link href={`/jobs/${j.id}`} className="font-medium hover:underline">{j.title}</Link>
                  <div className="text-xs text-muted-foreground">{[j.company, j.domain, shortDate(j.created_at)].filter(Boolean).join(" · ")}</div>
                </div>
                <Badge variant="outline">{j.match.missing.length} missing skills</Badge>
                <span className="text-sm">Match <ScoreValue value={j.match.score} /></span>
                <Button variant="outline" size="sm" render={<Link href={`/jobs/${j.id}`} />}>Details <ArrowRight /></Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No jobs yet.</p>
        )}
      </section>
    </div>
  );
}
