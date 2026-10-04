"use client";

import {
  CheckCircle2,
  CircleStop,
  Loader2,
  Mic,
  MicOff,
  Pause,
  Play,
  SendHorizontal,
  Settings2,
  Volume2,
  WifiOff,
  X,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/providers";
import { AnswerPanel, CurrentQuestionPanel, Timeline, TranscriptSearch } from "@/components/live/panels";
import { ReportView } from "@/components/live/report-view";
import { SourcesSheet } from "@/components/live/sources-sheet";
import { ConversationTranscript } from "@/components/live/transcript";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useLiveSession } from "@/hooks/use-live-session";
import { type AudioSource, browserSttSupported, useSpeech } from "@/hooks/use-speech";
import { api } from "@/lib/api";
import { duration } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useLive } from "@/stores/live";
import type { AnswerLength, AnswerVariant, InterviewDetail, Report, SttState } from "@/types/api";

const STT_LABEL: Record<SttState, string> = {
  IDLE: "Microphone off",
  LISTENING: "Listening…",
  SPEAKING_DETECTED: "Speech detected",
  TRANSCRIBING: "Transcribing…",
  QUESTION_READY: "Question detected",
  PROCESSING: "Generating answer…",
  ANSWER_READY: "Answer ready",
  ERROR: "Speech error",
};

function SttIndicator({ state, micOn }: { state: SttState; micOn: boolean }) {
  const effective: SttState = micOn ? state : state === "PROCESSING" || state === "ANSWER_READY" || state === "QUESTION_READY" ? state : "IDLE";
  const color =
    effective === "IDLE" ? "bg-muted-foreground/40" : effective === "ERROR" ? "bg-destructive" : effective === "PROCESSING" ? "bg-warning" : effective === "QUESTION_READY" || effective === "ANSWER_READY" ? "bg-success" : "bg-live";
  return (
    <span className="inline-flex items-center gap-2 text-sm font-medium" role="status" aria-live="polite">
      {effective === "PROCESSING" ? (
        <Loader2 className="size-3.5 animate-spin text-warning" aria-hidden />
      ) : effective === "QUESTION_READY" || effective === "ANSWER_READY" ? (
        <CheckCircle2 className="size-3.5 text-success" aria-hidden />
      ) : (
        <span className={cn("size-2.5 rounded-full", color, (effective === "LISTENING" || effective === "SPEAKING_DETECTED") && "animate-pulse")} aria-hidden />
      )}
      {STT_LABEL[effective]}
    </span>
  );
}

function LevelMeter({ level, active }: { level: number; active: boolean }) {
  return (
    <div className="flex h-4 items-end gap-0.5" aria-hidden>
      {[0.15, 0.3, 0.45, 0.6, 0.75].map((t) => (
        <span key={t} className={cn("w-1 rounded-sm transition-all", active && level >= t ? "bg-live" : "bg-muted")} style={{ height: `${30 + t * 70}%` }} />
      ))}
    </div>
  );
}

export default function LiveInterviewPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const [detail, setDetail] = useState<InterviewDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [tabChoice, setTab] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [focusTurn, setFocusTurn] = useState<string | null>(null);
  const [sourcesFor, setSourcesFor] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [typedRole, setTypedRole] = useState<"interviewer" | "candidate">("interviewer");
  const [audioSource, setAudioSource] = useState<AudioSource>("microphone");
  const [voiceChoice, setVoice] = useState<boolean | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const live = useLive();
  const ended = live.status === "ended" || live.connection === "ended";
  const isMock = live.mode !== "live_coaching";
  const tab = tabChoice ?? (ended ? "report" : "current");

  useEffect(() => {
    useLive.getState().reset();
    let cancelled = false;
    api<InterviewDetail>(`/interviews/${id}`)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        useLive.getState().init(d);
        setTypedRole(d.mode === "live_coaching" ? "interviewer" : "candidate");
      })
      .catch((e: Error) => setLoadError(e.message));
    return () => {
      cancelled = true;
      useLive.getState().reset();
    };
  }, [id]);

  const voice = voiceChoice ?? user?.preferences?.interviewer_voice !== false;

  const { send, sendAudio } = useLiveSession(detail ? id : null, Boolean(detail) && detail?.status !== "ended");

  const serverStt = Boolean(live.stt?.server_side);
  const speech = useSpeech({
    engine: serverStt ? "server" : "browser",
    source: audioSource,
    onPartial: (text) => send({ type: "transcript.partial", text }),
    onFinal: (text, sttMs) => send({ type: "transcript.segment", text, stt_ms: sttMs }),
    onVad: (speaking) => send({ type: "vad", speaking }),
    onAudio: (buf) => sendAudio(buf),
  });
  const micOn = speech.status === "on";

  // timer
  useEffect(() => {
    if (ended) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ended]);
  const startedAt = detail?.started_at ?? detail?.created_at ?? null;
  const elapsed = startedAt ? Math.max(0, (now - new Date(startedAt.endsWith("Z") || startedAt.includes("+") ? startedAt : `${startedAt}Z`).getTime()) / 1000) : 0;

  // report once ended
  useEffect(() => {
    if (!ended) return;
    speech.stop();
    api<Report>(`/interviews/${id}/report`).then(setReport).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ended, id]);

  // Mock interviews: read the interviewer's questions aloud; mute the mic while speaking.
  const speaking = useRef(false);
  useEffect(() => {
    if (!isMock || !voice || typeof window === "undefined" || !("speechSynthesis" in window)) return;
    const next = live.speakQueue[0];
    if (!next || speaking.current) return;
    useLive.getState().popSpeak();
    const u = new SpeechSynthesisUtterance(next);
    speaking.current = true;
    speech.setMuted(true);
    u.onend = u.onerror = () => {
      speaking.current = false;
      speech.setMuted(false);
    };
    window.speechSynthesis.speak(u);
  }, [live.speakQueue, isMock, voice, speech]);

  const toggleListening = useCallback(async () => {
    if (micOn || speech.status === "muted") {
      speech.stop();
      if (serverStt) send({ type: "audio.stop" });
      send({ type: "control", action: "pause" });
    } else {
      send({ type: "control", action: "resume" });
      if (serverStt) send({ type: "audio.start", sample_rate: 16000 });
      await speech.start();
    }
  }, [micOn, speech, serverStt, send]);

  useEffect(() => {
    if (speech.error) toast.error(speech.error);
  }, [speech.error]);

  const submitTyped = () => {
    const text = typed.trim();
    if (!text) return;
    send({ type: "utterance.text", text, role: typedRole });
    setTyped("");
  };

  const onVariant = useCallback((questionId: string, variant: AnswerVariant) => send({ type: "answer.variant", question_id: questionId, variant }), [send]);

  const setLength = (len: AnswerLength) => {
    send({ type: "settings", answer_length: len });
    useLive.setState({ answerLength: len });
  };

  const endInterview = async () => {
    speech.stop();
    try {
      if (live.connection === "open") send({ type: "control", action: "end" });
      else await api(`/interviews/${id}/end`, { method: "POST" });
      useLive.setState({ status: "ended" });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  // keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || ended) return;
      if (e.key.toLowerCase() === "l") {
        e.preventDefault();
        void toggleListening();
      } else if (e.key.toLowerCase() === "m" && (micOn || speech.status === "muted")) {
        e.preventDefault();
        speech.setMuted(speech.status !== "muted");
      } else if (e.key.toLowerCase() === "a") {
        e.preventDefault();
        send({ type: "control", action: "answer_now" });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleListening, micOn, speech, send, ended]);

  const currentQ = live.currentQuestionId ? live.questions[live.currentQuestionId] : undefined;
  const currentA = live.currentQuestionId ? live.answers[live.currentQuestionId] : undefined;
  const sourceList = useMemo(() => (sourcesFor ? live.answers[sourcesFor]?.sources ?? [] : []), [sourcesFor, live.answers]);

  if (loadError) {
    return (
      <div className="mx-auto max-w-md py-20 text-center">
        <p className="text-sm text-muted-foreground">{loadError}</p>
        <Button className="mt-4" render={<Link href="/interview" />}>Back to interviews</Button>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-[60vh] w-full" />
      </div>
    );
  }

  const partialVisible = live.partial && !ended;

  return (
    <div className="-mx-4 -my-6 flex h-[calc(100dvh-3.5rem)] flex-col md:-mx-8 lg:h-dvh">
      {/* ------------------------------------------------------------- top bar */}
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2.5 md:px-6">
        <div className="min-w-0">
          <h1 className="truncate font-semibold">{detail.title}</h1>
          <p className="text-xs text-muted-foreground">{detail.mode_label}</p>
        </div>
        <div className="flex items-center gap-3">
          {ended ? (
            <Badge variant="secondary">Ended</Badge>
          ) : live.status === "paused" ? (
            <Badge variant="outline">Paused</Badge>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-live">
              <span className="size-2 animate-pulse rounded-full bg-live" aria-hidden /> LIVE
            </span>
          )}
          <time className="tabular-nums text-sm text-muted-foreground" aria-label="Elapsed time">{duration(elapsed)}</time>
          {live.connection === "reconnecting" || live.connection === "connecting" ? (
            <span className="inline-flex items-center gap-1 text-xs text-warning">
              <WifiOff className="size-3.5" aria-hidden /> {live.connection === "connecting" ? "Connecting…" : "Reconnecting - transcript preserved"}
            </span>
          ) : null}
        </div>
        {!ended ? (
          <div className="ml-auto flex flex-wrap items-center gap-1.5" role="toolbar" aria-label="Session controls">
            <Button
              onClick={() => void toggleListening()}
              variant={micOn ? "secondary" : "default"}
              aria-keyshortcuts="Alt+L"
              disabled={live.connection !== "open" || speech.status === "requesting"}
            >
              {micOn || speech.status === "muted" ? <Pause /> : speech.status === "requesting" ? <Loader2 className="animate-spin" /> : <Play />}
              {micOn || speech.status === "muted" ? "Pause listening" : live.turns.length ? "Resume listening" : "Start"}
            </Button>
            <Button
              variant="outline"
              size="icon"
              aria-label={speech.status === "muted" ? "Unmute microphone" : "Mute microphone"}
              aria-pressed={speech.status === "muted"}
              aria-keyshortcuts="Alt+M"
              disabled={!(micOn || speech.status === "muted")}
              onClick={() => speech.setMuted(speech.status !== "muted")}
            >
              {speech.status === "muted" ? <MicOff /> : <Mic />}
            </Button>
            {!isMock ? (
              <Button variant="outline" size="sm" onClick={() => send({ type: "control", action: "answer_now" })} aria-keyshortcuts="Alt+A" title="Treat what was heard so far as the complete question">
                <Zap /> Answer now
              </Button>
            ) : null}
            <Popover>
              <PopoverTrigger render={<Button variant="outline" size="icon" aria-label="Session settings" />}>
                <Settings2 />
              </PopoverTrigger>
              <PopoverContent className="w-72 space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="len">Answer length</Label>
                  <select
                    id="len"
                    className="h-8 w-full rounded-lg border bg-transparent px-2 text-sm"
                    value={live.answerLength}
                    onChange={(e) => setLength(e.target.value as AnswerLength)}
                  >
                    <option value="20s">20 seconds</option>
                    <option value="45s">45 seconds</option>
                    <option value="90s">90 seconds</option>
                    <option value="detailed">Detailed</option>
                  </select>
                </div>
                {serverStt ? (
                  <div className="space-y-1.5">
                    <Label htmlFor="src">Audio source</Label>
                    <select id="src" className="h-8 w-full rounded-lg border bg-transparent px-2 text-sm" value={audioSource} onChange={(e) => setAudioSource(e.target.value as AudioSource)} disabled={micOn}>
                      <option value="microphone">Microphone</option>
                      <option value="tab">Shared tab audio</option>
                    </select>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Speech recognition: {browserSttSupported() ? "browser (Web Speech API)" : "not supported in this browser - type questions instead"}.
                  </p>
                )}
                {isMock ? (
                  <div className="flex items-center justify-between">
                    <Label htmlFor="voice" className="flex items-center gap-2"><Volume2 className="size-4" /> Read questions aloud</Label>
                    <Switch id="voice" checked={voice} onCheckedChange={(v) => setVoice(Boolean(v))} />
                  </div>
                ) : null}
                <p className="text-xs text-muted-foreground">Shortcuts: Alt+L listen · Alt+M mute · Alt+A answer now · Ctrl+Enter send</p>
              </PopoverContent>
            </Popover>
            <Dialog>
              <DialogTrigger render={<Button variant="destructive" size="sm" />}>
                <CircleStop /> End interview
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>End this interview?</DialogTitle>
                  <DialogDescription>Listening stops and your interview report is generated. The transcript stays available.</DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
                  <DialogClose render={<Button variant="destructive" onClick={() => void endInterview()} />}>End interview</DialogClose>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>
        ) : (
          <div className="ml-auto flex flex-wrap gap-1.5">
            {detail.has_recording ? (
              <>
                <Button variant="outline" size="sm" render={<a href={`/api/interviews/${id}/recording`} download />}>Download recording</Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={async () => {
                    if (!window.confirm("Delete the stored audio recording for this interview?")) return;
                    await api(`/interviews/${id}/recording`, { method: "DELETE" });
                    setDetail({ ...detail, has_recording: false });
                    toast.success("Recording deleted");
                  }}
                >
                  Delete recording
                </Button>
              </>
            ) : null}
            <Button variant="outline" size="sm" render={<Link href="/interview" />}>All interviews</Button>
          </div>
        )}
      </header>

      {live.notices.length ? (
        <div className="space-y-1 border-b bg-warning/10 px-4 py-2 md:px-6" role="alert">
          {live.notices.map((n) => (
            <div key={n.id} className="flex items-start justify-between gap-3 text-sm">
              <span>{n.message}</span>
              <button aria-label="Dismiss" onClick={() => live.dismissNotice(n.id)} className="text-muted-foreground"><X className="size-4" /></button>
            </div>
          ))}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* ----------------------------------------------------------- conversation */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <ConversationTranscript
            turns={live.turns}
            questions={live.questions}
            answers={live.answers}
            currentQuestionId={live.currentQuestionId}
            mode={live.mode}
            query={query}
            focusTurnId={focusTurn}
            feedback={live.feedback}
            onVariant={onVariant}
            onSources={setSourcesFor}
            readOnly={ended}
          />
          {!ended ? (
            <div className="border-t bg-background px-4 py-3 md:px-6">
              <div className="mx-auto max-w-3xl space-y-2">
                <div className="flex items-center gap-3">
                  <SttIndicator state={live.sttState} micOn={micOn} />
                  <LevelMeter level={speech.level} active={micOn} />
                  {speech.status === "denied" ? <span className="text-xs text-destructive">Microphone blocked</span> : null}
                  {speech.status === "muted" ? <span className="text-xs text-muted-foreground">Muted</span> : null}
                </div>
                <div className="min-h-6 text-sm" aria-live="polite">
                  {partialVisible ? (
                    <p>
                      <span className="text-xs font-medium text-muted-foreground">Current transcript: </span>
                      <span className="italic">“{live.partial!.text}”</span>
                    </p>
                  ) : null}
                </div>
                <div className="flex items-end gap-2">
                  {!isMock ? (
                    <select
                      aria-label="Who said this"
                      className="h-9 rounded-lg border bg-transparent px-2 text-xs"
                      value={typedRole}
                      onChange={(e) => setTypedRole(e.target.value as "interviewer" | "candidate")}
                    >
                      <option value="interviewer">Interviewer</option>
                      <option value="candidate">Me</option>
                    </select>
                  ) : null}
                  <Textarea
                    value={typed}
                    onChange={(e) => setTyped(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                        e.preventDefault();
                        submitTyped();
                      }
                    }}
                    rows={1}
                    placeholder={isMock ? "Type your answer (or answer out loud)…" : "Type the interviewer's question (or listen)…"}
                    aria-label={isMock ? "Your answer" : "Interviewer question"}
                    className="max-h-32 min-h-9 resize-none"
                  />
                  <Button size="icon" onClick={submitTyped} aria-label="Send" aria-keyshortcuts="Control+Enter" disabled={!typed.trim()}>
                    <SendHorizontal />
                  </Button>
                </div>
              </div>
            </div>
          ) : null}
        </div>

        {/* ----------------------------------------------------------- side panel */}
        <aside className="flex max-h-[45vh] min-h-0 w-full shrink-0 flex-col border-t lg:max-h-none lg:w-[400px] lg:border-l lg:border-t-0" aria-label="Interview intelligence">
          <Tabs value={tab} onValueChange={(v) => setTab(String(v))} className="flex min-h-0 flex-1 flex-col gap-0">
            <TabsList className="m-3 mb-0 w-[calc(100%-1.5rem)]">
              {ended ? <TabsTrigger value="report">Report</TabsTrigger> : null}
              <TabsTrigger value="current">{isMock ? "Feedback" : "Current"}</TabsTrigger>
              <TabsTrigger value="timeline">Timeline</TabsTrigger>
              <TabsTrigger value="search">Search</TabsTrigger>
            </TabsList>
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {ended ? (
                <TabsContent value="report">
                  {report ? <ReportPanelLink report={report} /> : <Skeleton className="h-40 w-full" />}
                </TabsContent>
              ) : null}
              <TabsContent value="current" className="space-y-3">
                {isMock ? <MockFeedbackPanel /> : null}
                <CurrentQuestionPanel question={currentQ} answer={currentA} />
                {!isMock || currentA ? (
                  <AnswerPanel
                    answer={currentA}
                    onVariant={(v) => live.currentQuestionId && onVariant(live.currentQuestionId, v)}
                    onSources={() => setSourcesFor(live.currentQuestionId)}
                  />
                ) : null}
                {live.focus.project ? (
                  <p className="px-1 text-xs text-muted-foreground">Conversation focus: {live.focus.project}{live.focus.technology ? ` · ${live.focus.technology}` : ""}</p>
                ) : null}
              </TabsContent>
              <TabsContent value="timeline">
                <Timeline turns={live.turns} startedAt={startedAt} onJump={(t) => setFocusTurn(`${t}`)} />
              </TabsContent>
              <TabsContent value="search">
                <TranscriptSearch sessionId={id} query={query} onQuery={setQuery} onJump={(t) => setFocusTurn(`${t}`)} />
              </TabsContent>
            </div>
          </Tabs>
        </aside>
      </div>
      <SourcesSheet open={Boolean(sourcesFor)} onOpenChange={(o) => !o && setSourcesFor(null)} sources={sourceList} />
      {ended && report ? (
        <section className="sr-only" aria-live="polite">Interview ended. Overall score {report.scores.overall !== null ? Math.round(report.scores.overall * 100) : "not available"} percent.</section>
      ) : null}
    </div>
  );
}

function ReportPanelLink({ report }: { report: Report }) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">Your interview report is ready.</p>
      <Dialog>
        <DialogTrigger render={<Button className="w-full" />}>Open full report</DialogTrigger>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>Interview report</DialogTitle>
            <DialogDescription>{report.title}</DialogDescription>
          </DialogHeader>
          <ReportView report={report} />
        </DialogContent>
      </Dialog>
      <div className="rounded-xl border p-4 text-sm">
        <div className="text-xs text-muted-foreground">Overall</div>
        <div className="text-3xl font-semibold tabular-nums">{report.scores.overall !== null ? `${Math.round(report.scores.overall * 100)}%` : "-"}</div>
        {report.weak_areas.length ? <p className="mt-2 text-xs">Focus next on: {report.weak_areas.join(", ")}</p> : null}
      </div>
    </div>
  );
}

function MockFeedbackPanel() {
  const turns = useLive((s) => s.turns);
  const feedback = useLive((s) => s.feedback);
  const last = [...turns].reverse().find((t) => t.role === "candidate" && t.kind === "spoken");
  const fb = last ? feedback[last.id] ?? (last.evaluation ? { evaluation: last.evaluation, claims: [], question: "" } : undefined) : undefined;
  if (!fb) {
    return <div className="rounded-xl border p-4 text-sm text-muted-foreground">Answer the question out loud or type it. You&apos;ll get feedback after each answer.</div>;
  }
  const e = fb.evaluation;
  return (
    <section className="rounded-xl border p-4" aria-label="Feedback on your last answer">
      <h2 className="text-xs font-semibold tracking-wider text-muted-foreground">YOUR LAST ANSWER</h2>
      <div className="mt-2 text-3xl font-semibold tabular-nums">{Math.round(e.overall * 100)}%</div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        {(["relevance", "completeness", "clarity", "conciseness", "naturalness", "confidence"] as const).map((k) => (
          <div key={k} className="flex justify-between"><dt className="capitalize text-muted-foreground">{k}</dt><dd className="tabular-nums">{Math.round(e[k] * 100)}%</dd></div>
        ))}
      </dl>
      {e.notes.length ? <ul className="mt-3 list-disc pl-5 text-xs text-muted-foreground">{e.notes.map((n) => <li key={n}>{n}</li>)}</ul> : null}
      {fb.claims.length ? <p className="mt-2 text-xs text-warning">Statements not in your verified profile: {fb.claims.join(" ")}</p> : null}
    </section>
  );
}
