"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/app-shell";
import { ChartFrame, HBars, ProgressLine, StatTile } from "@/components/charts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { pct, shortDate, topicLabel } from "@/lib/format";
import type { Analytics } from "@/types/api";

export default function AnalyticsPage() {
  const [a, setA] = useState<Analytics | null>(null);
  useEffect(() => {
    api<Analytics>("/analytics").then(setA).catch((e: Error) => toast.error(e.message));
  }, []);
  if (!a) return <Skeleton className="h-96 w-full" />;

  const progress = a.progress.map((p) => ({ label: shortDate(p.date), value: p.overall, sub: p.title }));
  const topics = Object.entries(a.topic_scores)
    .sort((x, y) => x[1] - y[1])
    .map(([k, v]) => ({ label: topicLabel(k), value: v }));
  const dist = Object.entries(a.topic_distribution).sort((x, y) => y[1] - x[1]).map(([k, v]) => ({ label: topicLabel(k), value: v }));
  const diff = ["easy", "medium", "hard"].map((k) => ({ label: k[0].toUpperCase() + k.slice(1), value: a.difficulty_distribution[k] ?? 0 }));

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader title="Analytics" description="Measured across every interview and practice attempt. Scores come from the heuristic-v1 evaluator." />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Questions answered" value={a.questions_answered.toLocaleString()} sub={`${a.sessions} sessions · ${a.practice_attempts} practice attempts`} />
        <StatTile label="Average response time" value={a.avg_response_ms !== null ? `${(a.avg_response_ms / 1000).toFixed(2)} s` : "-"} sub="End-to-end answer latency" />
        <StatTile label="Average answer length" value={a.avg_answer_words !== null ? `${Math.round(a.avg_answer_words)} words` : "-"} />
        <StatTile label="Technical accuracy" value={pct(a.technical_accuracy)} sub={`Grounding ${pct(a.grounding)} · Confidence ${pct(a.confidence)}`} />
      </div>

      <ChartFrame
        title="Overall score by session"
        description="From each session's interview report"
        table={{ headers: ["Session", "Date", "Overall", "Technical", "Communication"], rows: a.progress.map((p) => [p.title, shortDate(p.date), pct(p.overall), pct(p.technical), pct(p.communication)]) }}
      >
        <ProgressLine points={progress} />
      </ChartFrame>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartFrame title="Average score by topic" description="Weakest first" table={{ headers: ["Topic", "Average"], rows: topics.map((t) => [t.label, pct(t.value)]) }}>
          <HBars items={topics} max={1} />
        </ChartFrame>
        <ChartFrame title="Questions by topic" description="What you've been asked or practised" table={{ headers: ["Topic", "Questions"], rows: dist.map((t) => [t.label, t.value]) }}>
          <HBars items={dist} format={(v) => String(v)} />
        </ChartFrame>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartFrame title="Question difficulty" table={{ headers: ["Difficulty", "Questions"], rows: diff.map((d) => [d.label, d.value]) }}>
          <HBars items={diff} format={(v) => String(v)} emptyText="No questions yet." />
        </ChartFrame>
        <Card>
          <CardHeader>
            <CardTitle>Adaptive preparation plan</CardTitle>
            <CardDescription>Topics averaging under 60% across at least two attempts.</CardDescription>
          </CardHeader>
          <CardContent>
            {a.preparation_plan.length ? (
              <div className="space-y-4">
                {a.preparation_plan.map((p) => (
                  <div key={p.topic}>
                    <div className="font-medium">{topicLabel(p.topic)}</div>
                    <ol className="mt-1 space-y-1 text-sm text-muted-foreground">
                      {p.steps.map((s) => <li key={s.step}><span className="font-medium text-foreground">{s.step}</span> - {s.detail}</li>)}
                    </ol>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No weak topics yet - keep practising and the plan will adapt.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
