"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type MicStatus = "off" | "requesting" | "on" | "muted" | "denied" | "unsupported" | "error";
export type AudioSource = "microphone" | "tab";

interface SpeechCallbacks {
  onPartial: (text: string) => void;
  onFinal: (text: string, sttMs: number | null) => void;
  onVad: (speaking: boolean) => void;
  onAudio?: (pcm16: ArrayBuffer) => void;
}

interface SpeechOptions extends SpeechCallbacks {
  /** browser: Web Speech API recognises locally; server: stream PCM16 to the backend STT provider */
  engine: "browser" | "server";
  source?: AudioSource;
  lang?: string;
}

// Minimal typings for the Web Speech API (not in lib.dom for all TS versions)
interface SRAlternative { transcript: string }
interface SRResult { isFinal: boolean; 0: SRAlternative; length: number }
interface SREvent { resultIndex: number; results: { length: number; [i: number]: SRResult } }
interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((e: SREvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

function recognitionCtor(): (new () => SpeechRecognitionLike) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as Record<string, unknown>;
  return (w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null) as (new () => SpeechRecognitionLike) | null;
}

export function browserSttSupported(): boolean {
  return recognitionCtor() !== null;
}

// AudioWorklet that forwards raw Float32 frames to the main thread.
const WORKLET = `
class Tap extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor('iv-tap', Tap);
`;

function downsampleToPcm16(input: Float32Array, inRate: number, outRate = 16000): Int16Array {
  const ratio = inRate / outRate;
  const len = Math.floor(input.length / ratio);
  const out = new Int16Array(len);
  for (let i = 0; i < len; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    const v = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return out;
}

/**
 * Microphone / tab-audio capture with energy-based voice activity detection.
 * VAD uses an adaptive noise floor with hysteresis (on after ~150 ms above
 * threshold, off after ~500 ms below) so brief pauses don't toggle state.
 */
export function useSpeech(opts: SpeechOptions) {
  const [status, setStatus] = useState<MicStatus>("off");
  const [level, setLevel] = useState(0);
  const [speaking, setSpeaking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cb = useRef<SpeechCallbacks>(opts);
  useEffect(() => {
    cb.current = opts;
  });

  const stream = useRef<MediaStream | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const raf = useRef<number | null>(null);
  const recog = useRef<SpeechRecognitionLike | null>(null);
  const active = useRef(false);
  const muted = useRef(false);
  const vad = useRef({ floor: 0.01, speaking: false, aboveSince: 0, belowSince: 0, lastVoiceEnd: 0 });
  const pcmBuffer = useRef<Int16Array[]>([]);

  const cleanup = useCallback(() => {
    active.current = false;
    if (raf.current) cancelAnimationFrame(raf.current);
    raf.current = null;
    try {
      recog.current?.abort();
    } catch {
      /* already stopped */
    }
    recog.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    void ctx.current?.close().catch(() => undefined);
    ctx.current = null;
    setLevel(0);
    setSpeaking(false);
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const startRecognition = useCallback(() => {
    const Ctor = recognitionCtor();
    if (!Ctor) return false;
    const r = new Ctor();
    r.continuous = true;
    r.interimResults = true;
    r.lang = opts.lang ?? (typeof navigator !== "undefined" ? navigator.language : "en-US");
    let lastPartialAt = 0;
    r.onresult = (e) => {
      if (muted.current) return;
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        const text = res[0].transcript.trim();
        if (!text) continue;
        if (res.isFinal) {
          const end = vad.current.lastVoiceEnd || lastPartialAt;
          const sttMs = end ? Math.max(0, Math.round(performance.now() - end)) : null;
          cb.current.onFinal(text, sttMs);
        } else {
          interim += (interim ? " " : "") + text;
        }
      }
      if (interim) {
        lastPartialAt = performance.now();
        cb.current.onPartial(interim);
      }
    };
    r.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        setStatus("denied");
        setError("Microphone access was denied. Allow it in your browser's site settings, or type questions instead.");
        cleanup();
      } else if (e.error === "network") {
        setError("Browser speech recognition is unavailable (network). You can type questions instead.");
      } else if (e.error === "audio-capture") {
        setError("No microphone was found.");
      }
      // "no-speech" / "aborted" are normal; onend restarts recognition
    };
    r.onend = () => {
      // Chrome ends recognition after silence; keep it running while active
      if (active.current) {
        setTimeout(() => {
          if (active.current) {
            try {
              r.start();
            } catch {
              /* already started */
            }
          }
        }, 150);
      }
    };
    r.start();
    recog.current = r;
    return true;
  }, [cleanup, opts.lang]);

  const start = useCallback(async () => {
    setError(null);
    if (opts.engine === "browser" && !browserSttSupported()) {
      setStatus("unsupported");
      setError("This browser has no built-in speech recognition (try Chrome or Edge), or type questions instead.");
      return;
    }
    if (!navigator.mediaDevices) {
      setStatus("unsupported");
      setError("Audio capture requires a secure (https) connection.");
      return;
    }
    setStatus("requesting");
    try {
      if (opts.source === "tab") {
        const display = await navigator.mediaDevices.getDisplayMedia({ audio: true, video: true });
        display.getVideoTracks().forEach((t) => t.stop());
        if (!display.getAudioTracks().length) throw new DOMException("No audio shared", "NotFoundError");
        stream.current = display;
      } else {
        stream.current = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
      }
    } catch (err) {
      const name = (err as DOMException).name;
      setStatus(name === "NotAllowedError" ? "denied" : "error");
      setError(
        name === "NotAllowedError"
          ? "Microphone access was denied. Allow it in your browser's site settings, or type questions instead."
          : "Could not start audio capture. Check your microphone and try again.",
      );
      return;
    }
    active.current = true;
    muted.current = false;
    const audio = new AudioContext();
    ctx.current = audio;
    const src = audio.createMediaStreamSource(stream.current);
    const analyser = audio.createAnalyser();
    analyser.fftSize = 1024;
    src.connect(analyser);
    const buf = new Float32Array(analyser.fftSize);

    const tick = () => {
      if (!active.current) return;
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const rms = Math.sqrt(sum / buf.length);
      const v = vad.current;
      const now = performance.now();
      // adaptive noise floor: falls quickly, rises slowly
      v.floor = rms < v.floor ? rms * 0.6 + v.floor * 0.4 : v.floor * 0.999 + rms * 0.001;
      const onThreshold = Math.max(0.015, v.floor * 3);
      const offThreshold = Math.max(0.01, v.floor * 2);
      if (!muted.current) {
        if (rms > onThreshold) {
          v.belowSince = 0;
          v.aboveSince ||= now;
          if (!v.speaking && now - v.aboveSince > 150) {
            v.speaking = true;
            setSpeaking(true);
            cb.current.onVad(true);
          }
        } else if (rms < offThreshold) {
          v.aboveSince = 0;
          v.belowSince ||= now;
          if (v.speaking && now - v.belowSince > 500) {
            v.speaking = false;
            v.lastVoiceEnd = now;
            setSpeaking(false);
            cb.current.onVad(false);
          }
        }
      }
      setLevel(Math.min(1, rms * 8));
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);

    if (opts.engine === "server") {
      const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
      await audio.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      const node = new AudioWorkletNode(audio, "iv-tap");
      src.connect(node);
      let pending = 0;
      node.port.onmessage = (ev: MessageEvent<Float32Array>) => {
        if (muted.current) return;
        const pcm = downsampleToPcm16(ev.data, audio.sampleRate);
        pcmBuffer.current.push(pcm);
        pending += pcm.length;
        if (pending >= 1600) {
          // ~100 ms of 16 kHz audio per frame
          const out = new Int16Array(pending);
          let off = 0;
          for (const p of pcmBuffer.current) {
            out.set(p, off);
            off += p.length;
          }
          pcmBuffer.current = [];
          pending = 0;
          cb.current.onAudio?.(out.buffer);
        }
      };
    } else if (!startRecognition()) {
      setStatus("unsupported");
      cleanup();
      return;
    }
    setStatus("on");
  }, [opts.engine, opts.source, startRecognition, cleanup]);

  const stop = useCallback(() => {
    cleanup();
    setStatus("off");
  }, [cleanup]);

  const setMuted = useCallback((m: boolean) => {
    muted.current = m;
    stream.current?.getAudioTracks().forEach((t) => (t.enabled = !m));
    setStatus((s) => (s === "on" || s === "muted" ? (m ? "muted" : "on") : s));
    if (m && vad.current.speaking) {
      vad.current.speaking = false;
      setSpeaking(false);
      cb.current.onVad(false);
    }
  }, []);

  return { status, level, speaking, error, start, stop, setMuted };
}
