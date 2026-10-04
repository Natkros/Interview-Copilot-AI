"use client";

import { ArrowRight, CheckCircle2, FileUp, Mic, Target } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/app-shell";
import { StatTile } from "@/components/charts";
import { ScoreValue } from "@/components/live/score";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { pct, shortDate, topicLabel } from "@/lib/format";
import type { Dashboard } from "@/types/api";

export default function DashboardPage() {
  const [d, setD] = useState<Dashboard | null>(null);
  useEffect(() => {
    api<Dashboard>("/dashboard").then(setD).catch((e: Error) => toast.error(e.message));
  }, []);

  if (!d) return <Skeleton className="h-96 w-full" />;
  const onboarding = [
    { done: d.profile.projects + d.profile.skills > 0, label: "Upload your resume", href: "/resume", icon: FileUp },
    { done: d.profile.verification.ratio >= 0.8, label: "Verify extracted facts", href: "/resume", icon: CheckCircle2 },
    { done: Boolean(d.resume_match), label: "Add a target job description", href: "/jobs", icon: Target },
    { done: d.recent_sessions.length > 0, label: "Run your first mock interview", href: "/interview", icon: Mic },
  ];
  const remaining = onboarding.filter((o) => !o.done);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader
        title={d.profile.name ? `Welcome back, ${d.profile.name.split(" ")[0]}` : "Welcome to InterviewOS"}
        description="Your resume. Your experience. Your AI interview coach."
        actions={<Button render={<Link href="/interview" />}><Mic /> Start interview</Button>}
      />

      {remaining.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Get interview-ready</CardTitle>
            <CardDescription>{onboarding.length - remaining.length} of {onboarding.length} steps done</CardDescription>
          </CardHeader>
          <CardContent>
            <ol className="grid gap-2 sm:grid-cols-2">
              {onboarding.map((o) => (
                <li key={o.label}>
                  <Link href={o.href} className="flex items-center gap-3 rounded-lg border p-3 text-sm hover:bg-muted/50">
                    {o.done ? <CheckCircle2 className="size-4 text-success" aria-label="Done" /> : <o.icon className="size-4 text-muted-foreground" aria-hidden />}
                    <span className={o.done ? "text-muted-foreground line-through" : ""}>{o.label}</span>
                  </Link>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Interview readiness" value={<ScoreValue value={d.interview_readiness} />} sub={<span title={d.readiness_method}>Profile, job match and performance</span>} />
        <StatTile label="Resume match" value={<ScoreValue value={d.resume_match?.score} />} sub={d.resume_match ? d.resume_match.title : "Add a job description"} />
        <StatTile label="Technical score" value={<ScoreValue value={d.technical_score} />} sub="Average across technical topics" />
        <StatTile label="Behavioral score" value={<ScoreValue value={d.behavioral_score} />} sub="Behavioral & HR answers" />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Recent sessions</CardTitle>
            <Button variant="ghost" size="sm" render={<Link href="/interview" />}>All <ArrowRight /></Button>
          </CardHeader>
          <CardContent>
            {d.recent_sessions.length ? (
              <ul className="divide-y text-sm">
                {d.recent_sessions.map((s) => (
                  <li key={s.id} className="flex items-center gap-3 py-2.5">
                    <Link href={`/interview/${s.id}`} className="min-w-0 flex-1 truncate font-medium hover:underline">{s.title}</Link>
                    <span className="text-xs text-muted-foreground">{shortDate(s.created_at)}</span>
                    <Badge variant="outline" className="capitalize">{s.status}</Badge>
                    <span className="w-12 text-right"><ScoreValue value={s.overall} /></span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No interviews yet.</p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Practice</CardTitle>
            <CardDescription>{d.questions_practiced} questions answered</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Weak topics</h3>
              {d.weak_topics.length ? (
                <ul className="space-y-1 text-sm">
                  {d.weak_topics.map((w) => (
                    <li key={w.topic} className="flex justify-between"><span>{topicLabel(w.topic)}</span><span className="tabular-nums text-muted-foreground">{pct(w.avg)}</span></li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">None detected yet.</p>
              )}
            </div>
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Recommended practice</h3>
              <ul className="space-y-1.5 text-sm">
                {d.recommended_practice.map((r) => (
                  <li key={r.topic}><span className="font-medium">{topicLabel(r.topic)}</span> <span className="text-muted-foreground">- {r.reason}</span></li>
                ))}
              </ul>
            </div>
            <Button variant="outline" className="w-full" render={<Link href="/practice" />}>Practice now</Button>
          </CardContent>
        </Card>
      </div>

      {d.preparation_plan.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Your preparation plan</CardTitle>
            <CardDescription>Built from the topics where your scores are lowest.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-3">
            {d.preparation_plan.map((p) => (
              <div key={p.topic} className="rounded-lg border p-3">
                <div className="mb-2 font-medium">{topicLabel(p.topic)}</div>
                <ol className="space-y-1.5 text-sm">
                  {p.steps.map((s, i) => (
                    <li key={s.step} className="flex gap-2"><span className="tabular-nums text-muted-foreground">{i + 1}.</span><span><span className="font-medium">{s.step}:</span> {s.detail}</span></li>
                  ))}
                </ol>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
