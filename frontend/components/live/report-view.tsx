"use client";

import { AlertTriangle, BookOpen, CheckCircle2, CircleHelp, TrendingDown, TrendingUp } from "lucide-react";

import { ScoreBar, ScoreValue } from "@/components/live/score";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { duration, topicLabel } from "@/lib/format";
import type { Report } from "@/types/api";

export function ReportView({ report }: { report: Report }) {
  const s = report.scores;
  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-[minmax(0,260px)_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">Overall score</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-4xl font-semibold">
              <ScoreValue value={s.overall} />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              {report.scored_on === "candidate_answers" ? "Based on your own spoken answers." : "Based on the suggested answers in this session."}
            </p>
            <dl className="mt-4 space-y-1 text-sm">
              <div className="flex justify-between"><dt className="text-muted-foreground">Questions</dt><dd>{report.summary.questions}</dd></div>
              {report.summary.duration_seconds !== null ? (
                <div className="flex justify-between"><dt className="text-muted-foreground">Duration</dt><dd className="tabular-nums">{duration(report.summary.duration_seconds)}</dd></div>
              ) : null}
              {report.summary.avg_latency_ms !== null ? (
                <div className="flex justify-between"><dt className="text-muted-foreground">Avg. answer latency</dt><dd className="tabular-nums">{Math.round(report.summary.avg_latency_ms)} ms</dd></div>
              ) : null}
            </dl>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">Breakdown</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            <ScoreBar label="Technical" value={s.technical} />
            <ScoreBar label="Communication" value={s.communication} />
            <ScoreBar label="Resume knowledge" value={s.resume_knowledge} />
            <ScoreBar label="Problem solving" value={s.problem_solving} />
            <ScoreBar label="Confidence" value={s.confidence} />
            <ScoreBar label="Job alignment" value={s.job_alignment} />
            <ScoreBar label="Grounding" value={s.grounding} />
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base"><TrendingUp className="size-4 text-success" /> Strong areas</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {report.strong_areas.length ? report.strong_areas.map((a) => <Badge key={a} variant="secondary">{topicLabel(a)}</Badge>) : <p className="text-sm text-muted-foreground">Not enough data yet.</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base"><TrendingDown className="size-4 text-destructive" /> Weak areas</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {report.weak_areas.length ? report.weak_areas.map((a) => <Badge key={a} variant="outline">{topicLabel(a)}</Badge>) : <p className="text-sm text-muted-foreground">No weak areas detected.</p>}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><CircleHelp className="size-4" /> Questions missed</CardTitle>
        </CardHeader>
        <CardContent>
          {report.questions_missed.length ? (
            <ul className="divide-y text-sm">
              {report.questions_missed.map((m, i) => (
                <li key={i} className="flex items-start justify-between gap-3 py-2">
                  <span>{m.question}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{m.reason}{m.score !== undefined ? ` · ${Math.round(m.score * 100)}%` : ""}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="flex items-center gap-2 text-sm text-muted-foreground"><CheckCircle2 className="size-4 text-success" /> None.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><AlertTriangle className="size-4 text-warning" /> Hallucination events</CardTitle>
        </CardHeader>
        <CardContent>
          {report.hallucination_events.length ? (
            <ul className="space-y-3 text-sm">
              {report.hallucination_events.map((h, i) => (
                <li key={i}>
                  <div className="font-medium">{h.question}</div>
                  <ul className="mt-1 list-disc pl-5 text-muted-foreground">
                    {h.removed_claims.map((c) => <li key={c}>Removed before display: “{c}”</li>)}
                  </ul>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No unsupported statements were generated in this session.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><BookOpen className="size-4" /> Recommended study topics</CardTitle>
        </CardHeader>
        <CardContent>
          {report.recommended_study.length ? (
            <ul className="grid gap-2 text-sm sm:grid-cols-2">
              {report.recommended_study.map((r, i) => (
                <li key={i} className="rounded-lg border p-3">
                  <div className="text-xs text-muted-foreground">{topicLabel(r.topic)}</div>
                  {r.item}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">Nothing specific - keep practising across categories.</p>
          )}
        </CardContent>
      </Card>
      <p className="text-xs text-muted-foreground">{report.notes.join(" ")}</p>
    </div>
  );
}
