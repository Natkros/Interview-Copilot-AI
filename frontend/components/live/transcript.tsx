"use client";

import {
  ArrowDown,
  BookOpenText,
  Copy,
  Info,
  Loader2,
  Minimize2,
  Maximize2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Wrench,
} from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { GroundingBadge, ScoreValue } from "@/components/live/score";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { clockTime, highlight, pct, topicLabel, typeLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AnswerState, QuestionState } from "@/stores/live";
import type { AnswerVariant, Evaluation, InterviewMode, Turn } from "@/types/api";

function Highlighted({ text, query }: { text: string; query: string }) {
  return (
    <>
      {highlight(text, query).map((p, i) =>
        p.match ? (
          <mark key={i} className="hl">
            {p.text}
          </mark>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  );
}

function IconAction({ label, onClick, children, disabled }: { label: string; onClick: () => void; children: React.ReactNode; disabled?: boolean }) {
  return (
    <Tooltip>
      <TooltipTrigger render={<Button variant="ghost" size="icon-sm" aria-label={label} onClick={onClick} disabled={disabled} />}>
        {children}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

function ClassificationPopover({ q }: { q: QuestionState | undefined }) {
  if (!q) return null;
  const c = q.classification;
  return (
    <Popover>
      <PopoverTrigger render={<Button variant="ghost" size="xs" aria-label="View classification" />}>
        <Info /> {typeLabel(c.type)}
      </PopoverTrigger>
      <PopoverContent className="w-80 text-sm">
        <div className="space-y-1.5">
          <div className="font-medium">Classification</div>
          <Row k="Type" v={typeLabel(c.type)} />
          <Row k="Topic" v={topicLabel(c.topic)} />
          <Row k="Difficulty" v={c.difficulty} />
          <Row k="Needs resume context" v={c.requires_resume_context ? "yes" : "no"} />
          <Row k="Needs technical context" v={c.requires_technical_context ? "yes" : "no"} />
          <Row k="Refers to earlier turns" v={c.requires_previous_turn_context ? "yes" : "no"} />
          <Row k="Confidence" v={pct(c.confidence)} />
          {q.resolved && q.resolved !== "" ? (
            <div className="border-t pt-2">
              <div className="text-xs text-muted-foreground">Understood as</div>
              <div>{q.resolved}</div>
            </div>
          ) : null}
          {q.references.length ? <div className="text-xs text-muted-foreground">Resolved: {q.references.join(", ")}</div> : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-muted-foreground">{k}</span>
      <span className="text-right capitalize">{v}</span>
    </div>
  );
}

function FeedbackCard({ fb }: { fb: { evaluation: Evaluation; claims: string[] } }) {
  const e = fb.evaluation;
  return (
    <div className="mt-2 rounded-lg border bg-background/60 p-3 text-xs">
      <div className="mb-1 flex flex-wrap gap-x-4 gap-y-1">
        <span>
          Overall <ScoreValue value={e.overall} />
        </span>
        <span>
          Relevance <ScoreValue value={e.relevance} />
        </span>
        <span>
          Clarity <ScoreValue value={e.clarity} />
        </span>
        <span>
          Confidence <ScoreValue value={e.confidence} />
        </span>
      </div>
      {e.notes.length ? <ul className="list-disc pl-4 text-muted-foreground">{e.notes.map((n) => <li key={n}>{n}</li>)}</ul> : null}
      {fb.claims.length ? (
        <p className="mt-1 text-warning">Not in your verified profile: {fb.claims.map((c) => `“${c}”`).join(" ")}</p>
      ) : null}
    </div>
  );
}

export interface TranscriptProps {
  turns: Turn[];
  questions: Record<string, QuestionState>;
  answers: Record<string, AnswerState>;
  currentQuestionId: string | null;
  mode: InterviewMode;
  query: string;
  focusTurnId: string | null;
  feedback: Record<string, { evaluation: Evaluation; claims: string[] }>;
  onVariant: (questionId: string, variant: AnswerVariant) => void;
  onSources: (questionId: string) => void;
  readOnly?: boolean;
}

export function ConversationTranscript(props: TranscriptProps) {
  const { turns, questions, answers, currentQuestionId, mode, query, focusTurnId, feedback, onVariant, onSources, readOnly } = props;
  const scroller = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

  const current = currentQuestionId ? answers[currentQuestionId] : undefined;
  const pendingQuestion =
    current && (current.status === "streaming" || current.status === "pending") && !turns.some((t) => t.role === "candidate" && t.question_id === currentQuestionId)
      ? currentQuestionId
      : null;

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && pinned) el.scrollTop = el.scrollHeight;
  }, [turns, current?.draft, pinned]);

  useEffect(() => {
    if (!focusTurnId) return;
    const el = document.getElementById(`turn-${focusTurnId}`);
    if (el) {
      // scrolling away from the bottom un-pins auto-scroll via onScroll
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("ring-2", "ring-brand");
      const t = setTimeout(() => el.classList.remove("ring-2", "ring-brand"), 1600);
      return () => clearTimeout(t);
    }
  }, [focusTurnId]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Copied to clipboard");
    } catch {
      toast.error("Copy failed");
    }
  };

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={scroller}
        onScroll={onScroll}
        className="absolute inset-0 overflow-y-auto px-1 py-4 sm:px-4"
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-label="Interview conversation"
      >
        {turns.length === 0 && !pendingQuestion ? (
          <div className="mx-auto mt-16 max-w-md text-center text-sm text-muted-foreground">
            {mode === "live_coaching"
              ? "Start listening, or type the interviewer's question below. The conversation will appear here."
              : "The interviewer's first question will appear here. Answer out loud or type your answer."}
          </div>
        ) : null}
        <ol className="mx-auto flex max-w-3xl flex-col gap-5">
          {turns.map((t) => {
            const isInterviewer = t.role === "interviewer";
            const q = t.question_id ? questions[t.question_id] : undefined;
            const a = t.question_id ? answers[t.question_id] : undefined;
            const regenerating = !isInterviewer && t.kind === "suggestion" && a?.status === "streaming" && currentQuestionId === t.question_id;
            const text = regenerating ? a!.draft || "…" : t.text;
            const fb = feedback[t.id] ?? (t.evaluation ? { evaluation: t.evaluation, claims: [] } : undefined);
            return (
              <li key={t.id} id={`turn-${t.id}`} className={cn("turn-cv flex scroll-mt-24 flex-col rounded-xl transition-shadow", isInterviewer ? "items-start" : "items-end")}>
                <div className={cn("mb-1 flex items-center gap-2 text-[11px] font-semibold tracking-wider text-muted-foreground", !isInterviewer && "flex-row-reverse")}>
                  <span>{isInterviewer ? "INTERVIEWER" : t.kind === "suggestion" ? "CANDIDATE · SUGGESTED" : "CANDIDATE"}</span>
                  <time className="font-normal tabular-nums">{clockTime(t.created_at)}</time>
                </div>
                <div
                  className={cn(
                    "max-w-[92%] rounded-2xl px-4 py-3 text-[15px] leading-relaxed sm:max-w-[85%]",
                    isInterviewer
                      ? "rounded-tl-sm bg-interviewer text-interviewer-foreground"
                      : "rounded-tr-sm border border-candidate-border bg-candidate",
                  )}
                >
                  <p className="whitespace-pre-wrap">
                    <Highlighted text={text} query={query} />
                  </p>
                  {!isInterviewer && t.kind === "suggestion" && a?.result && !regenerating ? (
                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                      <span className="inline-flex items-center gap-1">
                        <ShieldCheck className="size-3.5" aria-hidden /> Grounding{" "}
                        <GroundingBadge
                          score={a.result.grounding.grounding_score}
                          supported={a.result.grounding.supported_claims}
                          unsupported={a.result.grounding.unsupported_claims}
                        />
                      </span>
                      <span>{a.result.word_count} words · ≈{Math.round(a.result.speaking_seconds)}s</span>
                      {a.result.insufficient_context ? <Badge variant="outline">Profile lacks detail</Badge> : null}
                      {a.result.grounding.removed_claims.length ? (
                        <Badge variant="outline">{a.result.grounding.removed_claims.length} unsupported removed</Badge>
                      ) : null}
                    </div>
                  ) : null}
                  {regenerating ? (
                    <div className="mt-2 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Loader2 className="size-3 animate-spin" aria-hidden /> Rewriting ({a?.variant})…
                    </div>
                  ) : null}
                  {t.role === "candidate" && t.kind === "spoken" && fb ? <FeedbackCard fb={fb} /> : null}
                </div>
                {isInterviewer && q && !readOnly ? (
                  <div className="mt-1 flex items-center gap-1">
                    <ClassificationPopover q={q} />
                    {t.question_id ? (
                      <Button variant="ghost" size="xs" onClick={() => onSources(t.question_id!)} aria-label="View relevant context">
                        <BookOpenText /> Context
                      </Button>
                    ) : null}
                  </div>
                ) : null}
                {!isInterviewer && t.kind === "suggestion" && t.question_id && !readOnly ? (
                  <div className="mt-1 flex flex-wrap items-center gap-0.5" role="toolbar" aria-label="Answer actions">
                    <IconAction label="Copy" onClick={() => void copy(t.text)}>
                      <Copy />
                    </IconAction>
                    <IconAction label="Regenerate" onClick={() => onVariant(t.question_id!, "regenerate")} disabled={regenerating}>
                      <RefreshCw />
                    </IconAction>
                    <IconAction label="Shorten" onClick={() => onVariant(t.question_id!, "shorter")} disabled={regenerating}>
                      <Minimize2 />
                    </IconAction>
                    <IconAction label="Expand" onClick={() => onVariant(t.question_id!, "longer")} disabled={regenerating}>
                      <Maximize2 />
                    </IconAction>
                    <IconAction label="More technical" onClick={() => onVariant(t.question_id!, "technical")} disabled={regenerating}>
                      <Wrench />
                    </IconAction>
                    <IconAction label="More natural" onClick={() => onVariant(t.question_id!, "natural")} disabled={regenerating}>
                      <Sparkles />
                    </IconAction>
                    <IconAction label="Show sources" onClick={() => onSources(t.question_id!)}>
                      <BookOpenText />
                    </IconAction>
                  </div>
                ) : null}
              </li>
            );
          })}
          {pendingQuestion && current ? (
            <li className="flex flex-col items-end" aria-busy="true">
              <div className="mb-1 text-[11px] font-semibold tracking-wider text-muted-foreground">CANDIDATE · SUGGESTED</div>
              <div className="max-w-[92%] rounded-2xl rounded-tr-sm border border-dashed border-candidate-border bg-candidate/60 px-4 py-3 text-[15px] leading-relaxed sm:max-w-[85%]">
                {current.draft ? <p className="whitespace-pre-wrap">{current.draft}</p> : null}
                <div className="mt-1 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Loader2 className="size-3 animate-spin" aria-hidden />
                  {current.status === "pending" ? "Retrieving your context…" : "Drafting · grounding check runs when complete"}
                </div>
                {current.resetReason ? <p className="mt-1 text-xs text-warning">{current.resetReason}</p> : null}
              </div>
            </li>
          ) : null}
        </ol>
      </div>
      {!pinned ? (
        <Button
          size="sm"
          variant="secondary"
          className="absolute bottom-3 left-1/2 -translate-x-1/2 shadow"
          onClick={() => {
            setPinned(true);
            scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
          }}
        >
          <ArrowDown /> Jump to latest
        </Button>
      ) : null}
    </div>
  );
}
