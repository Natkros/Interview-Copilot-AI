import { create } from "zustand";

import type {
  AnswerLength,
  AnswerResult,
  Classification,
  Evaluation,
  InterviewDetail,
  InterviewMode,
  Latency,
  LiveEvent,
  SourceRef,
  SttConfig,
  SttState,
  Turn,
} from "@/types/api";

export type ConnectionState = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "ended";

export interface QuestionState {
  id: string;
  turnId?: string;
  classification: Classification;
  resolved: string;
  aspect: string | null;
  references: string[];
  isFollowUp: boolean;
  focus: { project: string | null; experience: string | null; technology: string | null; topic: string | null };
}

export interface AnswerState {
  questionId: string;
  status: "pending" | "streaming" | "complete" | "cancelled" | "skipped";
  draft: string;
  variant: string;
  result?: AnswerResult;
  evaluation?: Evaluation;
  latency?: Latency;
  sources: SourceRef[];
  agents: string[];
  retrievalMs?: number;
  resetReason?: string;
  skippedReason?: string;
}

export interface Notice {
  id: number;
  service: string;
  message: string;
  level: "warning" | "error";
}

interface LiveStore {
  sessionId: string | null;
  connection: ConnectionState;
  lastSeq: number;
  status: string;
  mode: InterviewMode;
  answerLength: AnswerLength;
  stt: SttConfig | null;
  sttState: SttState;
  turns: Turn[];
  partial: { role: string; text: string } | null;
  questions: Record<string, QuestionState>;
  answers: Record<string, AnswerState>;
  currentQuestionId: string | null;
  focus: { project: string | null; topic: string | null; technology: string | null };
  notices: Notice[];
  feedback: Record<string, { evaluation: Evaluation; claims: string[]; question: string }>;
  reportId: string | null;
  speakQueue: string[];
  init: (detail: InterviewDetail) => void;
  apply: (e: LiveEvent) => void;
  setConnection: (c: ConnectionState) => void;
  dismissNotice: (id: number) => void;
  setSttState: (s: SttState) => void;
  popSpeak: () => string | undefined;
  reset: () => void;
}

let noticeId = 0;

function upsertTurn(turns: Turn[], t: Turn): Turn[] {
  const idx = turns.findIndex((x) => x.id === t.id);
  if (idx >= 0) {
    const next = turns.slice();
    next[idx] = { ...turns[idx], ...t };
    return next;
  }
  const next = [...turns, t];
  next.sort((a, b) => a.seq - b.seq);
  return next;
}

const initial = {
  sessionId: null,
  connection: "idle" as ConnectionState,
  lastSeq: 0,
  status: "created",
  mode: "live_coaching" as InterviewMode,
  answerLength: "45s" as AnswerLength,
  stt: null,
  sttState: "IDLE" as SttState,
  turns: [] as Turn[],
  partial: null,
  questions: {} as Record<string, QuestionState>,
  answers: {} as Record<string, AnswerState>,
  currentQuestionId: null,
  focus: { project: null, topic: null, technology: null },
  notices: [] as Notice[],
  feedback: {},
  reportId: null,
  speakQueue: [] as string[],
};

export const useLive = create<LiveStore>((set, get) => ({
  ...initial,

  reset: () => set({ ...initial }),

  init: (d) => {
    const questions: Record<string, QuestionState> = {};
    const answers: Record<string, AnswerState> = {};
    let current: string | null = null;
    for (const t of d.turns) {
      if (t.role === "interviewer" && t.question_id && t.classification) {
        questions[t.question_id] = {
          id: t.question_id, turnId: t.id, classification: t.classification, resolved: t.text, aspect: null,
          references: [], isFollowUp: t.classification.type === "FOLLOW_UP",
          focus: { project: null, experience: null, technology: null, topic: t.classification.topic },
        };
        current = t.question_id;
      }
      if (t.role === "candidate" && t.answer_id && d.answers[t.answer_id] && t.question_id) {
        const a = d.answers[t.answer_id];
        answers[t.question_id] = {
          questionId: t.question_id, status: "complete", draft: a.text, variant: a.variant, sources: a.sources, agents: [],
          evaluation: a.evaluation ?? undefined, latency: a.latency,
          result: {
            text: a.text, key_points: a.key_points, star: a.star, structure: "", sources: a.sources, grounding: a.grounding,
            word_count: a.word_count, speaking_seconds: a.speaking_seconds,
            speaking_range: [Math.round(a.speaking_seconds * 0.9), Math.round(a.speaking_seconds * 1.1)],
            insufficient_context: a.insufficient_context, variant: a.variant, length: d.answer_length, model: a.model,
            job_relevance: a.evaluation?.job_alignment ?? null, notes: [], degraded: false,
          },
        };
      }
    }
    set({
      sessionId: d.id, status: d.status, mode: d.mode, answerLength: d.answer_length, stt: d.stt,
      turns: d.turns, questions, answers, currentQuestionId: current, focus: d.focus,
      connection: d.status === "ended" ? "ended" : get().connection,
    });
  },

  setConnection: (c) => set({ connection: c }),
  setSttState: (s) => set({ sttState: s }),
  dismissNotice: (id) => set({ notices: get().notices.filter((n) => n.id !== id) }),
  popSpeak: () => {
    const [first, ...rest] = get().speakQueue;
    set({ speakQueue: rest });
    return first;
  },

  apply: (e) => {
    const s = get();
    if (e.seq && e.seq > s.lastSeq) set({ lastSeq: e.seq });
    switch (e.event) {
      case "session.snapshot": {
        // authoritative turn list from the server; keep any richer local state
        const byId = new Map(s.turns.map((t) => [t.id, t]));
        const turns = e.turns.map((t) => ({ ...byId.get(t.id), ...t }));
        set({
          status: e.status, mode: e.mode, answerLength: e.answer_length, stt: e.stt, sttState: e.stt_state,
          turns, focus: e.focus, lastSeq: Math.max(s.lastSeq, e.seq ?? 0),
        });
        if (!e.services.vector_store.available) {
          get().apply({ ...e, event: "degraded", service: "vector_store", message: "Vector search unavailable - using keyword retrieval." } as LiveEvent);
        }
        return;
      }
      case "stt.state":
        set({ sttState: e.state });
        return;
      case "transcript.partial":
        set({ partial: e.text ? { role: e.role, text: e.text } : null });
        return;
      case "transcript.final": {
        const speak = e.speak && e.turn.role === "interviewer" && !e.replayed ? [...s.speakQueue, e.turn.text] : s.speakQueue;
        set({ turns: upsertTurn(s.turns, e.turn), partial: e.turn.role === "interviewer" || s.mode !== "live_coaching" ? null : s.partial, speakQueue: speak });
        return;
      }
      case "question.classified": {
        const q: QuestionState = {
          id: e.question_id, turnId: e.turn_id, classification: e.classification, resolved: e.resolved, aspect: e.aspect,
          references: e.references, isFollowUp: e.is_follow_up, focus: e.focus,
        };
        const turns = e.turn_id
          ? s.turns.map((t) => (t.id === e.turn_id ? { ...t, question_id: e.question_id, classification: e.classification } : t))
          : s.turns;
        set({
          questions: { ...s.questions, [e.question_id]: q }, currentQuestionId: e.question_id, turns,
          focus: { project: e.focus.project, topic: e.focus.topic, technology: e.focus.technology },
        });
        return;
      }
      case "context.ready": {
        const prev = s.answers[e.question_id];
        set({
          answers: {
            ...s.answers,
            [e.question_id]: {
              questionId: e.question_id, status: prev?.status === "complete" ? "complete" : "pending", draft: prev?.draft ?? "",
              variant: prev?.variant ?? "default", result: prev?.result, evaluation: prev?.evaluation, latency: prev?.latency,
              sources: e.sources, agents: e.agents, retrievalMs: e.retrieval_ms,
            },
          },
        });
        return;
      }
      case "answer.start": {
        const prev = s.answers[e.question_id];
        set({
          currentQuestionId: e.question_id,
          answers: {
            ...s.answers,
            [e.question_id]: {
              ...prev,
              questionId: e.question_id,
              sources: prev?.sources ?? [],
              agents: prev?.agents ?? [],
              status: "streaming",
              draft: "",
              variant: e.variant,
              resetReason: undefined,
            },
          },
        });
        return;
      }
      case "answer.delta": {
        const qid = s.currentQuestionId;
        if (!qid || !s.answers[qid]) return;
        const a = s.answers[qid];
        set({ answers: { ...s.answers, [qid]: { ...a, status: "streaming", draft: a.draft + e.text } } });
        return;
      }
      case "answer.reset": {
        const qid = s.currentQuestionId;
        if (!qid || !s.answers[qid]) return;
        set({ answers: { ...s.answers, [qid]: { ...s.answers[qid], draft: "", resetReason: e.reason } } });
        return;
      }
      case "answer.complete": {
        const prev = s.answers[e.question_id];
        const answers = {
          ...s.answers,
          [e.question_id]: {
            questionId: e.question_id, status: "complete" as const, draft: e.answer.text, variant: e.answer.variant,
            result: e.answer, evaluation: e.evaluation, latency: e.latency, sources: e.answer.sources,
            agents: prev?.agents ?? [], retrievalMs: prev?.retrievalMs,
          },
        };
        set({ answers, turns: e.turn ? upsertTurn(s.turns, e.turn) : s.turns });
        return;
      }
      case "answer.skipped": {
        set({
          answers: { ...s.answers, [e.question_id]: { questionId: e.question_id, status: "skipped", draft: "", variant: "default", sources: [], agents: [], skippedReason: e.reason } },
        });
        return;
      }
      case "answer.cancelled": {
        const qid = s.currentQuestionId;
        if (qid && s.answers[qid] && s.answers[qid].status !== "complete") {
          set({ answers: { ...s.answers, [qid]: { ...s.answers[qid], status: "cancelled" } } });
        }
        return;
      }
      case "feedback":
        set({ feedback: { ...s.feedback, [e.turn_id]: { evaluation: e.evaluation, claims: e.claims_not_in_profile, question: e.question } } });
        return;
      case "degraded":
      case "error": {
        const message = e.message;
        if (s.notices.some((n) => n.message === message)) return;
        const notice: Notice = {
          id: ++noticeId, service: e.event === "degraded" ? e.service : e.code, message,
          level: e.event === "error" && !e.recoverable ? "error" : "warning",
        };
        set({ notices: [...s.notices, notice].slice(-4) });
        return;
      }
      case "session.status":
        set({ status: e.status });
        return;
      case "session.ended":
        set({ status: "ended", reportId: e.report_id, connection: "ended", sttState: "IDLE" });
        return;
      default:
        return;
    }
  },
}));
