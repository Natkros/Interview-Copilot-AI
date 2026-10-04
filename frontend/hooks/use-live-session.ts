"use client";

import { useCallback, useEffect, useRef } from "react";

import { api, wsUrl } from "@/lib/api";
import { useLive } from "@/stores/live";
import type { LiveEvent } from "@/types/api";

type Outbound = Record<string, unknown>;
const EPHEMERAL = new Set(["transcript.partial", "vad", "ping"]);

/**
 * Owns the live WebSocket for one interview session.
 * - single-use ticket per connection
 * - `hello{last_seq}` on every (re)connect -> the server replays missed events
 * - exponential-backoff reconnect; messages sent while offline are queued and flushed
 */
export function useLiveSession(sessionId: string | null, enabled: boolean) {
  const wsRef = useRef<WebSocket | null>(null);
  const queue = useRef<Outbound[]>([]);

  const send = useCallback((msg: Outbound) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
    else if (!EPHEMERAL.has(String(msg.type))) queue.current.push(msg);
  }, []);

  const sendAudio = useCallback((buf: ArrayBuffer) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN && ws.bufferedAmount < 1_000_000) ws.send(buf);
  }, []);

  useEffect(() => {
    if (!enabled || !sessionId) return;
    const { apply, setConnection } = useLive.getState();
    let stopped = false;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const flush = () => {
      const ws = wsRef.current;
      while (ws && ws.readyState === WebSocket.OPEN && queue.current.length) {
        ws.send(JSON.stringify(queue.current.shift()));
      }
    };

    const scheduleReconnect = () => {
      if (stopped) return;
      setConnection("reconnecting");
      const delay = Math.min(15000, 1000 * 2 ** attempts) + Math.random() * 300;
      attempts += 1;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void connect(), delay);
    };

    const connect = async () => {
      if (stopped) return;
      setConnection(attempts ? "reconnecting" : "connecting");
      let ticket: string;
      let path: string;
      try {
        const r = await api<{ ticket: string; path: string }>(`/interviews/${sessionId}/ticket`, { method: "POST" });
        ticket = r.ticket;
        path = r.path;
      } catch (err) {
        if ((err as { status?: number }).status === 409) {
          setConnection("ended");
          return;
        }
        scheduleReconnect();
        return;
      }
      if (stopped) return;
      const ws = new WebSocket(wsUrl(path, ticket));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;
      ws.onopen = () => {
        attempts = 0;
        ws.send(JSON.stringify({ type: "hello", last_seq: useLive.getState().lastSeq }));
        setConnection("open");
        flush();
      };
      ws.onmessage = (msg) => {
        try {
          apply(JSON.parse(msg.data as string) as LiveEvent);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = (ev) => {
        if (wsRef.current === ws) wsRef.current = null;
        if (stopped) return;
        if (ev.code === 4409 || useLive.getState().status === "ended") {
          setConnection("ended");
          return;
        }
        if (ev.code === 4404) {
          setConnection("closed");
          return;
        }
        scheduleReconnect();
      };
      ws.onerror = () => ws.close();
    };

    void connect();
    const ping = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify({ type: "ping" }));
    }, 20000);
    const onOnline = () => {
      if (!wsRef.current) {
        attempts = 0;
        void connect();
      }
    };
    window.addEventListener("online", onOnline);
    return () => {
      stopped = true;
      clearInterval(ping);
      window.removeEventListener("online", onOnline);
      if (timer) clearTimeout(timer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [enabled, sessionId]);

  return { send, sendAudio };
}
