// Typed client for the InterviewOS API. All requests go to same-origin /api/*
// (proxied to the backend), carry the httpOnly session cookie automatically and
// send the CSRF header the backend requires for state-changing requests.

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

type Method = "GET" | "POST" | "PATCH" | "DELETE";

interface RequestOptions {
  method?: Method;
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  /** Don't redirect to /login on 401 (used by the auth bootstrap). */
  allowUnauthorized?: boolean;
}

function messageFrom(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === "object" && d && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)))
      .join("; ");
  }
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  return fallback;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "x-interviewos-csrf": "1" };
  let body: BodyInit | undefined;
  if (opts.json !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(opts.json);
  } else if (opts.form) {
    body = opts.form;
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method: opts.method ?? (body ? "POST" : "GET"),
      headers,
      body,
      credentials: "same-origin",
      signal: opts.signal,
      cache: "no-store",
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "Network error - check your connection and try again.");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = undefined;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | undefined)?.detail ?? data;
    if (res.status === 401 && !opts.allowUnauthorized && typeof window !== "undefined") {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      // A full navigation intentionally discards all in-memory client state on session expiry.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      if (!window.location.pathname.startsWith("/login")) window.location.assign(`/login?next=${next}&expired=1`);
    }
    const fallback =
      res.status === 429 ? "Too many requests - please wait a moment." : res.status >= 500 ? "Server error - please retry." : `Request failed (${res.status})`;
    throw new ApiError(res.status, messageFrom(detail, fallback), detail);
  }
  return data as T;
}

export function wsUrl(path: string, ticket: string): string {
  const configured = process.env.NEXT_PUBLIC_WS_URL;
  let base: string;
  if (configured) {
    base = configured.replace(/\/$/, "");
  } else {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    // default dev topology: Next on :3000, API on :8000
    const host = window.location.port === "3000" ? `${window.location.hostname}:8000` : window.location.host;
    base = `${proto}//${host}`;
  }
  return `${base}${path}?ticket=${encodeURIComponent(ticket)}`;
}
