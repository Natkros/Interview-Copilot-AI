import type { QuestionType } from "@/types/api";

export function pct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return `${(v * 100).toFixed(digits)}%`;
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

export function duration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function speakingRange(words: number): string {
  const secs = words / 2.5;
  const lo = Math.max(1, Math.round((secs * 0.9) / 5) * 5);
  const hi = Math.max(lo + 5, Math.round((secs * 1.1) / 5) * 5);
  return `≈ ${lo}-${hi} seconds`;
}

const TYPE_LABELS: Partial<Record<QuestionType, string>> = {
  SYSTEM_DESIGN: "System design",
  MACHINE_LEARNING: "Machine learning",
  DEEP_LEARNING: "Deep learning",
  GENERATIVE_AI: "Generative AI",
  FOLLOW_UP: "Follow-up",
  DSA: "DSA",
  HR: "HR",
  RAG: "RAG",
  DEVOPS: "DevOps",
};

export function typeLabel(t: string | null | undefined): string {
  if (!t) return "";
  return TYPE_LABELS[t as QuestionType] ?? t.charAt(0) + t.slice(1).toLowerCase().replace(/_/g, " ");
}

export function topicLabel(topic: string | null | undefined): string {
  if (!topic) return "General";
  return typeLabel(topic);
}

/** Split text into fragments for highlighting search matches. */
export function highlight(text: string, query: string): { text: string; match: boolean }[] {
  if (!query.trim()) return [{ text, match: false }];
  const esc = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${esc})`, "gi"));
  return parts.filter(Boolean).map((p) => ({ text: p, match: p.toLowerCase() === query.toLowerCase() }));
}

export function scoreTone(v: number | null | undefined): "good" | "ok" | "weak" | "none" {
  if (v === null || v === undefined) return "none";
  if (v >= 0.75) return "good";
  if (v >= 0.5) return "ok";
  return "weak";
}
