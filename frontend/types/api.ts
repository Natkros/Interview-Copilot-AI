// Typed contracts mirroring the backend (app/models/domain.py, app/api/routes/*).

export type QuestionType =
  | "GREETING" | "INTRODUCTION" | "BEHAVIORAL" | "HR" | "RESUME" | "PROJECT" | "TECHNICAL" | "CODING"
  | "DSA" | "SYSTEM_DESIGN" | "MACHINE_LEARNING" | "DEEP_LEARNING" | "GENERATIVE_AI" | "RAG" | "DATABASE"
  | "CLOUD" | "DEVOPS" | "SECURITY" | "SITUATIONAL" | "FOLLOW_UP" | "CLARIFICATION" | "FEEDBACK"
  | "STATEMENT" | "UNKNOWN";

export type AnswerLength = "20s" | "45s" | "90s" | "detailed";
export type AnswerVariant = "default" | "regenerate" | "shorter" | "longer" | "technical" | "natural";
export type InterviewMode =
  | "live_coaching" | "mock" | "resume_drill" | "technical" | "dsa" | "system_design" | "behavioral" | "hr"
  | "job_specific";

export interface User {
  id: string;
  email: string;
  preferences: Preferences;
}

export interface Preferences {
  answer_length?: AnswerLength;
  store_recordings?: boolean;
  font_scale?: number;
  high_contrast?: boolean;
  reduced_motion?: boolean;
  interviewer_voice?: boolean;
  default_job_id?: string | null;
}

export interface Source {
  document?: string;
  document_id?: string;
  section?: string;
  page?: number;
  date?: string | null;
}

interface ProfileRow {
  id: string;
  verified: boolean;
  origin: "resume" | "user";
  source: Source;
  position: number;
}

export interface Skill extends ProfileRow {
  name: string;
  category: string;
}

export interface Project extends ProfileRow {
  name: string;
  description: string | null;
  problem: string | null;
  solution: string | null;
  candidate_role: string | null;
  url: string | null;
  architecture: string[];
  technologies: string[];
  responsibilities: string[];
  challenges: string[];
  solutions: string[];
  results: string[];
  metrics: string[];
  limitations: string[];
  future_work: string[];
  testing: string[];
}

export interface Experience extends ProfileRow {
  kind: "job" | "internship";
  title: string | null;
  organization: string | null;
  location: string | null;
  start_date: string | null;
  end_date: string | null;
  highlights: string[];
  technologies: string[];
}

export interface Certification extends ProfileRow {
  name: string;
  issuer: string | null;
  date: string | null;
}

export interface ProfileItem extends ProfileRow {
  kind: "education" | "achievements" | "research" | "publications" | "leadership" | "activities";
  title: string;
  subtitle: string | null;
  date: string | null;
  details: string[];
}

export interface Profile {
  id: string;
  personal: {
    name: string | null;
    headline: string | null;
    email: string | null;
    phone: string | null;
    location: string | null;
    links: string[];
    summary: string | null;
    verified: boolean;
  };
  skills: Skill[];
  projects: Project[];
  experiences: Experience[];
  certifications: Certification[];
  items: ProfileItem[];
  verification: { total: number; verified: number; ratio: number };
  kb: { version: number; indexed_at: string | null };
}

export type ProfileKind = "skill" | "project" | "experience" | "certification" | "item";

export interface ProfileOperation {
  action: "confirm" | "unconfirm" | "edit" | "remove" | "add" | "confirm_all" | "personal";
  kind?: ProfileKind;
  id?: string;
  data?: Record<string, unknown>;
}

export interface ResumeResponse {
  profile: Profile;
  canonical: Record<string, unknown>;
  document: { id: string; filename: string; pages: number; uploaded_at: string; warnings: string[] } | null;
  sections: { name: string; heading: string; text: string; page: number }[];
  parse_report: { sections_found: string[]; counts: Record<string, number>; warnings: string[] } | null;
}

export interface JobMatch {
  score: number;
  required_coverage?: number;
  preferred_coverage?: number;
  matched: string[];
  missing: string[];
  preferred_matched: string[];
  preferred_missing: string[];
  evidence: Record<string, string[]>;
  method?: string;
}

export interface Job {
  id: string;
  title: string;
  company: string | null;
  created_at: string;
  match: JobMatch;
  domain: string | null;
  parsed?: {
    role: string;
    company: string | null;
    responsibilities: string[];
    required_skills: string[];
    preferred_skills: string[];
    required_items: string[];
    preferred_items: string[];
    technologies: string[];
    experience: string[];
    min_years: number | null;
    education: string[];
    domain: string | null;
    keywords: string[];
  };
  raw_text?: string;
}

export interface Classification {
  type: QuestionType;
  topic: string | null;
  difficulty: "easy" | "medium" | "hard";
  is_question: boolean;
  requires_resume_context: boolean;
  requires_technical_context: boolean;
  requires_previous_turn_context: boolean;
  confidence: number;
  signals: string[];
  entities: string[];
}

export interface Claim {
  text: string;
  kind: "candidate" | "general";
  supported: boolean;
  support: number;
  evidence_ids: string[];
  unsupported_terms: string[];
  verified_support: boolean;
}

export interface Grounding {
  method: string;
  grounding_score: number | null;
  supported_claims: number;
  unsupported_claims: number;
  general_claims: number;
  risk: "low" | "medium" | "high";
  claims: Claim[];
  removed_claims: string[];
  rewritten: boolean;
}

export interface SourceRef {
  id: string;
  label: string;
  collection: string;
  excerpt: string;
  verified: boolean;
  meta: Record<string, unknown>;
}

export interface Evaluation {
  relevance: number;
  correctness: number;
  grounding: number;
  completeness: number;
  clarity: number;
  conciseness: number;
  naturalness: number;
  job_alignment: number | null;
  confidence: number;
  hallucination_risk: number;
  overall: number;
  method: string;
  notes: string[];
}

export interface AnswerResult {
  text: string;
  key_points: string[];
  star: Partial<Record<"situation" | "task" | "action" | "result", string>>;
  structure: string;
  sources: SourceRef[];
  grounding: Grounding;
  word_count: number;
  speaking_seconds: number;
  speaking_range: [number, number];
  insufficient_context: boolean;
  variant: string;
  length: string;
  model: string;
  job_relevance: number | null;
  notes: string[];
  degraded: boolean;
}

export interface Latency {
  stt_ms: number | null;
  classification_ms: number;
  resolution_ms: number;
  retrieval_ms: number;
  first_token_ms: number | null;
  generation_ms: number;
  grounding_ms: number;
  evaluation_ms: number;
  total_ms: number;
}

export interface Turn {
  id: string;
  seq: number;
  role: "interviewer" | "candidate" | "system";
  kind: "utterance" | "suggestion" | "spoken";
  text: string;
  created_at: string | null;
  question_id: string | null;
  answer_id: string | null;
  classification: Classification | null;
  grounding_score: number | null;
  insufficient_context: boolean | null;
  evaluation: Evaluation | null;
  mock: { text: string; category: string; difficulty: string; kind: string } | null;
}

export interface StoredAnswer {
  id: string;
  question_id: string;
  variant: string;
  text: string;
  word_count: number;
  speaking_seconds: number;
  key_points: string[];
  star: AnswerResult["star"];
  sources: SourceRef[];
  grounding: Grounding;
  latency: Latency;
  insufficient_context: boolean;
  model: string;
  created_at: string;
  evaluation: Evaluation | null;
}

export interface InterviewSummary {
  id: string;
  mode: InterviewMode;
  mode_label: string;
  title: string;
  status: "created" | "live" | "paused" | "ended";
  job_id: string | null;
  answer_length: AnswerLength;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  turn_count?: number;
  overall?: number | null;
}

export interface SttConfig {
  provider: "browser" | "deepgram";
  server_side: boolean;
  sample_rate: number;
  endpoint_ms: number;
  available: boolean;
}

export interface InterviewDetail extends InterviewSummary {
  turns: Turn[];
  has_more: boolean;
  answers: Record<string, StoredAnswer>;
  focus: { project: string | null; topic: string | null; technology: string | null };
  stt: SttConfig;
  live: boolean;
  has_recording?: boolean;
}

export interface Report {
  id: string;
  created_at: string;
  session_id: string;
  title: string;
  mode: InterviewMode;
  scored_on: "candidate_answers" | "suggested_answers";
  summary: {
    duration_seconds: number | null;
    questions: number;
    turns: number;
    topics: Record<string, number>;
    avg_latency_ms: number | null;
  };
  scores: {
    overall: number | null;
    technical: number | null;
    communication: number | null;
    resume_knowledge: number | null;
    problem_solving: number | null;
    confidence: number | null;
    job_alignment: number | null;
    grounding: number | null;
  };
  strong_areas: string[];
  weak_areas: string[];
  questions_missed: { question: string; reason: string; score?: number }[];
  hallucination_events: { question: string; removed_claims: string[]; unsupported: number }[];
  recommended_study: { topic: string; item: string }[];
  notes: string[];
}

export interface PrepStep {
  topic: string;
  steps: { step: string; detail: string }[];
}

export interface Analytics {
  questions_answered: number;
  sessions: number;
  practice_attempts: number;
  avg_response_ms: number | null;
  avg_answer_words: number | null;
  technical_accuracy: number | null;
  grounding: number | null;
  confidence: number | null;
  difficulty_distribution: Record<string, number>;
  topic_distribution: Record<string, number>;
  topic_scores: Record<string, number>;
  weak_topics: { topic: string; avg: number }[];
  progress: { session_id: string; title: string; mode: string; date: string; overall: number; technical: number | null; communication: number | null }[];
  preparation_plan: PrepStep[];
}

export interface Dashboard {
  profile: { name: string | null; verification: Profile["verification"]; projects: number; skills: number; kb_version: number };
  resume_match: { job_id: string; title: string; score: number | null } | null;
  interview_readiness: number | null;
  readiness_method: string;
  technical_score: number | null;
  behavioral_score: number | null;
  questions_practiced: number;
  recent_sessions: { id: string; title: string; mode: string; status: string; created_at: string; overall: number | null }[];
  weak_topics: { topic: string; avg: number }[];
  recommended_practice: { topic: string; reason: string }[];
  preparation_plan: PrepStep[];
}

export interface BankItem {
  id: string;
  category: string;
  question: string;
  difficulty: "easy" | "medium" | "hard";
  expected_concepts: string[];
  resume_relevance: number;
  job_relevance: number;
  source: string;
  last_practiced: string | null;
  attempts: number;
  performance: number | null;
}

export interface PracticeResult {
  attempt_id: string;
  classification: Classification;
  scores: Evaluation;
  grounding: Grounding;
  claims_not_in_profile: string[];
  feedback: string[];
  concepts_covered: string[];
  concepts_missing: string[];
  item: BankItem;
}

export interface GenerateResult {
  classification?: Classification;
  resolved?: string;
  answer?: AnswerResult;
  evaluation?: Evaluation;
  latency?: Latency;
  skipped?: string;
}

export interface SystemStatus {
  llm: { provider: string; model: string };
  stt: SttConfig;
  embeddings: { provider: string };
  vector_store: { available: boolean; error: string | null };
  store_raw_audio: boolean;
}

// ------------------------------------------------------------------ WebSocket events

export type SttState =
  | "IDLE" | "LISTENING" | "SPEAKING_DETECTED" | "TRANSCRIBING" | "QUESTION_READY" | "PROCESSING" | "ANSWER_READY"
  | "ERROR";

interface EventBase {
  seq: number;
  session_id: string;
  ts: number;
  replayed?: boolean;
}

export type LiveEvent = EventBase &
  (
    | {
        event: "session.snapshot";
        status: string;
        mode: InterviewMode;
        answer_length: AnswerLength;
        stt: SttConfig;
        stt_state: SttState;
        turns: Turn[];
        focus: { project: string | null; topic: string | null; technology: string | null };
        services: { vector_store: { available: boolean }; llm: string };
        resync_required: boolean;
      }
    | { event: "stt.state"; state: SttState }
    | { event: "transcript.partial"; role: Turn["role"]; text: string }
    | { event: "transcript.final"; turn: Turn; speak?: boolean }
    | {
        event: "question.classified";
        question_id: string;
        turn_id?: string;
        classification: Classification;
        resolved: string;
        is_follow_up: boolean;
        aspect: string | null;
        references: string[];
        focus: { project: string | null; experience: string | null; technology: string | null; topic: string | null };
      }
    | { event: "status"; state: string; agents: string[] }
    | { event: "context.ready"; question_id: string; agents: string[]; retrieval_ms: number; sources: SourceRef[]; focus_project: string | null }
    | { event: "answer.start"; question_id: string; variant: string }
    | { event: "answer.delta"; text: string; variant?: string }
    | { event: "answer.reset"; reason: string }
    | {
        event: "answer.complete";
        question_id: string;
        answer_id: string;
        answer: AnswerResult;
        grounding_score: number | null;
        evaluation: Evaluation;
        latency: Latency;
        turn?: Turn | null;
        variant?: string;
      }
    | { event: "answer.skipped"; question_id: string; reason: string }
    | { event: "answer.cancelled"; reason: string }
    | { event: "feedback"; turn_id: string; question: string; evaluation: Evaluation; claims_not_in_profile: string[] }
    | { event: "summary.updated"; summary: Record<string, unknown> }
    | { event: "degraded"; service: string; message: string }
    | { event: "error"; code: string; message: string; recoverable: boolean }
    | { event: "session.status"; status: string }
    | { event: "session.ended"; report_id: string }
    | { event: "pong" }
  );
