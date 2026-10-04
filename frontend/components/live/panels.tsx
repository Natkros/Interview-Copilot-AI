"use client";

import { AlertTriangle, BookOpenText, Loader2, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { GroundingBadge, Metric, ScoreValue } from "@/components/live/score";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { clockTime, speakingRange, topicLabel, typeLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AnswerState, QuestionState } from "@/stores/live";
import type { AnswerVariant, Turn } from "@/types/api";

function relevance(q: QuestionState): { resume: string; job: string } {
  const c = q.classification;
  const resume = c.requires_resume_context ? (c.type === "FOLLOW_UP" || c.type === "PROJECT" || c.type === "RESUME" ? "High" : "Medium") : "Low";
  return { resume, job: "-" };
}

export function CurrentQuestionPanel({ question, answer }: { question: QuestionState | undefined; answer: AnswerState | undefined }) {
  if (!question) {
    return (
      <section aria-label="Current question" className="rounded-xl border p-4">
        <h2 className="text-xs font-semibold tracking-wider text-muted-foreground">CURRENT QUESTION</h2>
        <p className="mt-2 text-sm text-muted-foreground">Waiting for the first question…</p>
      </section>
    );
  }
  const c = question.classification;
  const rel = relevance(question);
  const jobRel = answer?.result?.job_relevance;
  return (
    <section aria-label="Current question" className="rounded-xl border p-4">
      <h2 className="text-xs font-semibold tracking-wider text-muted-foreground">CURRENT QUESTION</h2>
      <p className="mt-2 font-medium leading-snug">{question.resolved}</p>
      {question.references.length ? (
        <p className="mt-1 text-xs text-muted-foreground">Resolved using: {question.references.map((r) => r.replace(/^context -> /, "")).join(" · ")}</p>
      ) : null}
      <div className="mt-3 space-y-1.5">
        <Metric label="Category" value={<span>{typeLabel(c.type)}{c.topic && c.topic !== c.type ? ` / ${topicLabel(c.topic)}` : ""}</span>} />
        <Metric label="Difficulty" value={<span className="capitalize">{c.difficulty}</span>} />
        <Metric label="Resume relevance" value={rel.resume} />
        <Metric label="Job relevance" value={jobRel !== undefined && jobRel !== null ? <ScoreValue value={jobRel} /> : "-"} hint="Share of the target job's key skills addressed by the answer" />
        {question.focus.project ? <Metric label="About" value={question.focus.project} /> : null}
      </div>
    </section>
  );
}

const VARIANTS: { v: AnswerVariant; label: string }[] = [
  { v: "regenerate", label: "Regenerate" },
  { v: "shorter", label: "Shorter" },
  { v: "longer", label: "Longer" },
  { v: "natural", label: "More natural" },
  { v: "technical", label: "More technical" },
];

export function AnswerPanel({
  answer,
  onVariant,
  onSources,
}: {
  answer: AnswerState | undefined;
  onVariant: (v: AnswerVariant) => void;
  onSources: () => void;
}) {
  const [view, setView] = useState<"full" | "points" | "star">("full");
  const r = answer?.result;

  if (!answer) {
    return (
      <section aria-label="Suggested response" className="rounded-xl border p-4">
        <h2 className="text-xs font-semibold tracking-wider text-muted-foreground">SUGGESTED RESPONSE</h2>
        <p className="mt-2 text-sm text-muted-foreground">A grounded answer will appear here.</p>
      </section>
    );
  }
  if (answer.status === "skipped") {
    return (
      <section aria-label="Suggested response" className="rounded-xl border p-4">
        <h2 className="text-xs font-semibold tracking-wider text-muted-foreground">SUGGESTED RESPONSE</h2>
        <p className="mt-2 text-sm text-muted-foreground">No answer needed ({answer.skippedReason}).</p>
      </section>
    );
  }
  const busy = answer.status === "streaming" || answer.status === "pending";
  const hasStar = Boolean(r && Object.values(r.star ?? {}).some(Boolean));
  const shownView = view === "star" && !hasStar ? "full" : view;
  const text = busy ? answer.draft : r?.text ?? answer.draft;
  const words = r?.word_count ?? text.split(/\s+/).filter(Boolean).length;
  return (
    <section aria-label="Suggested response" className="rounded-xl border p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold tracking-wider text-muted-foreground">SUGGESTED RESPONSE</h2>
        <div role="tablist" aria-label="Answer view" className="inline-flex rounded-md bg-muted p-0.5 text-xs">
          {(["full", "points", "star"] as const).map((v) => (
            <button
              key={v}
              role="tab"
              aria-selected={shownView === v}
              disabled={busy || (v === "star" && !hasStar)}
              onClick={() => setView(v)}
              className={cn("rounded px-2 py-1 disabled:opacity-40", shownView === v && "bg-background shadow-sm")}
            >
              {v === "full" ? "Full" : v === "points" ? "Key points" : "STAR"}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-3 max-h-80 overflow-y-auto text-sm leading-relaxed">
        {busy && !text ? (
          <p className="inline-flex items-center gap-2 text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> {answer.status === "pending" ? "Retrieving context…" : "Drafting…"}
          </p>
        ) : shownView === "points" && r ? (
          <ul className="list-disc space-y-1 pl-5">{r.key_points.map((p) => <li key={p}>{p}</li>)}</ul>
        ) : shownView === "star" && r ? (
          <dl className="space-y-2">
            {(["situation", "task", "action", "result"] as const).map((k) => (
              <div key={k}>
                <dt className="text-xs font-semibold uppercase text-muted-foreground">{k}</dt>
                <dd>{r.star[k] || <span className="text-muted-foreground">Not in your profile</span>}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="whitespace-pre-wrap">“{text}”</p>
        )}
      </div>
      {answer.resetReason ? <p className="mt-2 text-xs text-warning">{answer.resetReason}</p> : null}
      <div className="mt-3 space-y-1.5 border-t pt-3">
        <Metric label="Estimated speaking time" value={`${words} words ${speakingRange(words)}`} />
        <Metric
          label="Grounding"
          value={busy ? <span className="text-muted-foreground">checking after draft…</span> : <GroundingBadge score={r?.grounding.grounding_score} supported={r?.grounding.supported_claims} unsupported={r?.grounding.unsupported_claims} />}
        />
        <Metric label="Job relevance" value={r?.job_relevance !== null && r?.job_relevance !== undefined ? <ScoreValue value={r.job_relevance} /> : "-"} />
        {answer.evaluation ? <Metric label="Quality (evaluator)" value={<ScoreValue value={answer.evaluation.overall} />} /> : null}
        {answer.latency ? (
          <Metric
            label="Latency"
            value={<span className="tabular-nums text-xs">{Math.round(answer.latency.total_ms)} ms total{answer.latency.first_token_ms !== null ? ` · ${Math.round(answer.latency.first_token_ms)} ms first token` : ""}</span>}
            hint={`classify ${answer.latency.classification_ms}ms · retrieval ${answer.latency.retrieval_ms}ms · generation ${answer.latency.generation_ms}ms · grounding ${answer.latency.grounding_ms}ms`}
          />
        ) : null}
      </div>
      {r?.notes.length ? (
        <ul className="mt-3 space-y-1 rounded-lg bg-muted/60 p-2.5 text-xs">
          {r.notes.map((n) => (
            <li key={n} className="flex gap-1.5">
              <AlertTriangle className="mt-0.5 size-3 shrink-0 text-warning" aria-hidden /> {n}
            </li>
          ))}
        </ul>
      ) : null}
      {r?.grounding.removed_claims.length ? (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-muted-foreground">Removed unsupported statements ({r.grounding.removed_claims.length})</summary>
          <ul className="mt-1 list-disc pl-4 text-muted-foreground line-through">{r.grounding.removed_claims.map((c) => <li key={c}>{c}</li>)}</ul>
        </details>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {VARIANTS.map(({ v, label }) => (
          <Button key={v} size="xs" variant="outline" disabled={busy} onClick={() => onVariant(v)}>
            {label}
          </Button>
        ))}
        <Button size="xs" variant="outline" disabled={busy || !r} onClick={() => setView(shownView === "points" ? "full" : "points")}>
          {shownView === "points" ? "Full answer" : "Show key points"}
        </Button>
        <Button size="xs" variant="outline" onClick={onSources} disabled={!answer.sources.length}>
          <BookOpenText /> Sources
        </Button>
      </div>
      {r ? (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {r.model}
          {r.degraded ? " · degraded mode" : ""}
          {answer.agents.length ? ` · agents: ${answer.agents.join(", ")}` : ""}
        </p>
      ) : null}
    </section>
  );
}

export function Timeline({ turns, onJump, startedAt }: { turns: Turn[]; onJump: (turnId: string) => void; startedAt: string | null }) {
  const items = turns.filter((t) => t.role === "interviewer");
  return (
    <section aria-label="Conversation timeline">
      <ol className="space-y-1 text-sm">
        {startedAt ? (
          <li className="flex gap-3 px-2 py-1 text-muted-foreground">
            <time className="w-16 shrink-0 tabular-nums">{clockTime(startedAt).slice(0, 5)}</time> Interview started
          </li>
        ) : null}
        {items.map((t) => (
          <li key={t.id}>
            <button
              type="button"
              onClick={() => onJump(t.id)}
              className="flex w-full gap-3 rounded-md px-2 py-1.5 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
            >
              <time className="w-16 shrink-0 tabular-nums text-muted-foreground">{clockTime(t.created_at).slice(0, 5)}</time>
              <span className="line-clamp-2">{t.text}</span>
              {t.classification ? (
                <Badge variant="outline" className="ml-auto shrink-0">
                  {typeLabel(t.classification.type)}
                </Badge>
              ) : null}
            </button>
          </li>
        ))}
        {!items.length ? <li className="px-2 text-muted-foreground">No questions yet.</li> : null}
      </ol>
    </section>
  );
}

interface SearchHit {
  turn_id: string;
  seq: number;
  role: string;
  text: string;
}

export function TranscriptSearch({
  sessionId,
  query,
  onQuery,
  onJump,
}: {
  sessionId: string;
  query: string;
  onQuery: (q: string) => void;
  onJump: (turnId: string) => void;
}) {
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    const q = query.trim();
    if (!q) return;
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await api<{ results: SearchHit[] }>(`/interviews/${sessionId}/search?q=${encodeURIComponent(q)}`, { signal: ctrl.signal });
        setHits(r.results);
      } catch {
        /* aborted or failed */
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [query, sessionId]);
  const shown = query.trim() ? hits : null;
  const summary = useMemo(() => (shown ? `${shown.length} match${shown.length === 1 ? "" : "es"}` : ""), [shown]);
  return (
    <section aria-label="Search transcript" className="space-y-3">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Search transcript…"
          aria-label="Search transcript"
          className="pl-8 pr-8"
        />
        {query ? (
          <button type="button" aria-label="Clear search" onClick={() => onQuery("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground">
            <X className="size-4" />
          </button>
        ) : null}
      </div>
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {loading ? "Searching…" : summary}
        {query && !loading ? " · questions, answers, projects, technologies and topics" : ""}
      </p>
      <ul className="space-y-1">
        {shown?.map((h) => (
          <li key={h.turn_id}>
            <button type="button" onClick={() => onJump(h.turn_id)} className="w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted">
              <span className="text-[11px] font-semibold uppercase text-muted-foreground">{h.role}</span>
              <span className="line-clamp-2">{h.text}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
