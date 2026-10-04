"use client";

import { Lightbulb, Loader2, Mic, MicOff, Shuffle, Send } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/app-shell";
import { GroundingBadge, ScoreBar, ScoreValue } from "@/components/live/score";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { browserSttSupported, useSpeech } from "@/hooks/use-speech";
import { api } from "@/lib/api";
import { pct, shortDate } from "@/lib/format";
import type { BankItem, GenerateResult, PracticeResult } from "@/types/api";

interface HistoryRow {
  id: string;
  question: string | null;
  category: string | null;
  overall: number;
  created_at: string;
}

function PracticeInner() {
  const params = useSearchParams();
  const [items, setItems] = useState<BankItem[] | null>(null);
  const [current, setCurrent] = useState<BankItem | null>(null);
  const [answer, setAnswer] = useState("");
  const [interim, setInterim] = useState("");
  const [result, setResult] = useState<PracticeResult | null>(null);
  const [model, setModel] = useState<GenerateResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [modelBusy, setModelBusy] = useState(false);
  const [history, setHistory] = useState<HistoryRow[]>([]);

  const speech = useSpeech({
    engine: "browser",
    onPartial: (t) => setInterim(t),
    onFinal: (t) => {
      setInterim("");
      setAnswer((a) => (a ? `${a} ${t}` : t));
    },
    onVad: () => undefined,
  });

  const loadHistory = () => api<HistoryRow[]>("/practice/history").then(setHistory).catch(() => undefined);

  useEffect(() => {
    api<{ items: BankItem[] }>("/question-bank?sort=weakest&limit=500")
      .then((b) => {
        setItems(b.items);
        const wanted = params.get("q");
        setCurrent(b.items.find((i) => i.id === wanted) ?? b.items[0] ?? null);
      })
      .catch((e: Error) => toast.error(e.message));
    void loadHistory();
  }, [params]);

  const pickNext = useCallback(() => {
    if (!items?.length) return;
    const pool = items.filter((i) => i.id !== current?.id);
    // favour unpractised and weak items
    pool.sort((a, b) => (a.performance ?? -1) - (b.performance ?? -1) + (Math.random() - 0.5) * 0.4);
    setCurrent(pool[0]);
    setAnswer("");
    setResult(null);
    setModel(null);
  }, [items, current]);

  const submit = async () => {
    if (!current || !answer.trim()) return;
    speech.stop();
    setBusy(true);
    try {
      const r = await api<PracticeResult>("/practice", { json: { question_bank_id: current.id, answer } });
      setResult(r);
      setItems((list) => list?.map((i) => (i.id === r.item.id ? r.item : i)) ?? null);
      void loadHistory();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const showModel = async () => {
    if (!current) return;
    setModelBusy(true);
    try {
      setModel(await api<GenerateResult>("/answers/generate", { json: { question: current.question } }));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setModelBusy(false);
    }
  };

  const listening = speech.status === "on";
  const wordCount = useMemo(() => answer.split(/\s+/).filter(Boolean).length, [answer]);

  if (!items) return <Skeleton className="h-96 w-full" />;
  if (!current) return <p className="text-sm text-muted-foreground">Your question bank is empty - upload a resume first.</p>;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeader title="Practice" description="Answer out loud or type. You get scored feedback and can compare with a grounded model answer." />
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3">
          <div>
            <div className="mb-1 flex flex-wrap gap-1.5">
              <Badge variant="outline">{current.category}</Badge>
              <Badge variant="outline" className="capitalize">{current.difficulty}</Badge>
              {current.performance !== null ? <Badge variant="secondary">Best avg {pct(current.performance)}</Badge> : null}
            </div>
            <CardTitle className="text-lg leading-snug">{current.question}</CardTitle>
            {current.expected_concepts.length ? <CardDescription>Good answers usually mention: {current.expected_concepts.join(", ")}</CardDescription> : null}
          </div>
          <Button variant="outline" size="sm" onClick={pickNext}><Shuffle /> Next</Button>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            rows={7}
            value={answer + (interim ? ` ${interim}` : "")}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="Type your answer, or press the microphone and speak…"
            aria-label="Your answer"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => void submit()} disabled={busy || !answer.trim()}>
              {busy ? <Loader2 className="animate-spin" /> : <Send />} Get feedback
            </Button>
            {browserSttSupported() ? (
              <Button variant="outline" onClick={() => (listening ? speech.stop() : void speech.start())} aria-pressed={listening}>
                {listening ? <MicOff /> : <Mic />} {listening ? "Stop dictation" : "Answer out loud"}
              </Button>
            ) : null}
            <Button variant="ghost" onClick={() => void showModel()} disabled={modelBusy}>
              {modelBusy ? <Loader2 className="animate-spin" /> : <Lightbulb />} Show model answer
            </Button>
            <span className="ml-auto text-xs text-muted-foreground">{wordCount} words · ≈{Math.round(wordCount / 2.5)}s</span>
          </div>
          {speech.error ? <p className="text-xs text-destructive">{speech.error}</p> : null}
        </CardContent>
      </Card>

      {result ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-3">Feedback <span className="text-2xl"><ScoreValue value={result.scores.overall} /></span></CardTitle>
            <CardDescription>Evaluator: {result.scores.method}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-6 md:grid-cols-2">
            <div className="space-y-2.5">
              <ScoreBar label="Relevance" value={result.scores.relevance} />
              <ScoreBar label="Correctness" value={result.scores.correctness} />
              <ScoreBar label="Completeness" value={result.scores.completeness} />
              <ScoreBar label="Clarity" value={result.scores.clarity} />
              <ScoreBar label="Conciseness" value={result.scores.conciseness} />
              <ScoreBar label="Naturalness" value={result.scores.naturalness} />
              <ScoreBar label="Confidence" value={result.scores.confidence} />
              {result.scores.job_alignment !== null ? <ScoreBar label="Job alignment" value={result.scores.job_alignment} /> : null}
            </div>
            <div className="space-y-3 text-sm">
              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Grounding of your claims</h3>
                <GroundingBadge score={result.grounding.grounding_score} supported={result.grounding.supported_claims} unsupported={result.grounding.unsupported_claims} />
                {result.claims_not_in_profile.length ? (
                  <ul className="mt-1 list-disc pl-5 text-xs text-warning">{result.claims_not_in_profile.map((c) => <li key={c}>Not in your verified profile: “{c}”</li>)}</ul>
                ) : null}
              </div>
              {result.concepts_covered.length ? <p><span className="text-muted-foreground">Covered: </span>{result.concepts_covered.join(", ")}</p> : null}
              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">How to improve</h3>
                {result.feedback.length ? <ul className="list-disc space-y-1 pl-5">{result.feedback.map((f) => <li key={f}>{f}</li>)}</ul> : <p className="text-muted-foreground">Solid answer.</p>}
              </div>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {model?.answer ? (
        <Card>
          <CardHeader>
            <CardTitle>Model answer from your profile</CardTitle>
            <CardDescription>
              Grounding <GroundingBadge score={model.answer.grounding.grounding_score} supported={model.answer.grounding.supported_claims} unsupported={model.answer.grounding.unsupported_claims} /> · {model.answer.word_count} words
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm leading-relaxed">
            <p>“{model.answer.text}”</p>
            {model.answer.notes.length ? <ul className="list-disc pl-5 text-xs text-muted-foreground">{model.answer.notes.map((n) => <li key={n}>{n}</li>)}</ul> : null}
          </CardContent>
        </Card>
      ) : null}

      {history.length ? (
        <Card>
          <CardHeader><CardTitle>Recent attempts</CardTitle></CardHeader>
          <CardContent>
            <ul className="divide-y text-sm">
              {history.slice(0, 10).map((h) => (
                <li key={h.id} className="flex items-center gap-3 py-2">
                  <span className="min-w-0 flex-1 truncate">{h.question}</span>
                  <span className="text-xs text-muted-foreground">{shortDate(h.created_at)}</span>
                  <ScoreValue value={h.overall} />
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

export default function PracticePage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full" />}>
      <PracticeInner />
    </Suspense>
  );
}
