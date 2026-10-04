import { cn } from "@/lib/utils";
import { pct, scoreTone } from "@/lib/format";

const TONE = {
  good: "text-success",
  ok: "text-warning",
  weak: "text-destructive",
  none: "text-muted-foreground",
};

export function ScoreValue({ value, className }: { value: number | null | undefined; className?: string }) {
  return <span className={cn("tabular-nums font-medium", TONE[scoreTone(value)], className)}>{pct(value)}</span>;
}

export function Metric({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-sm" title={hint}>
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right">{value}</span>
    </div>
  );
}

export function ScoreBar({ label, value }: { label: string; value: number | null | undefined }) {
  const v = value ?? 0;
  const tone = scoreTone(value);
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <ScoreValue value={value} />
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(v * 100)}>
        <div
          className={cn("h-full rounded-full", tone === "good" ? "bg-success" : tone === "ok" ? "bg-warning" : tone === "weak" ? "bg-destructive" : "bg-muted-foreground/30")}
          style={{ width: `${Math.round(v * 100)}%` }}
        />
      </div>
    </div>
  );
}

/** Grounding display: shows the measured claim support, never a made-up precision. */
export function GroundingBadge({ score, supported, unsupported }: { score: number | null | undefined; supported?: number; unsupported?: number }) {
  if (score === null || score === undefined) {
    return (
      <span className="text-sm text-muted-foreground" title="No candidate-specific claims in this answer">
        n/a · general knowledge
      </span>
    );
  }
  return (
    <span title={`${supported ?? 0} supported / ${unsupported ?? 0} unsupported candidate-specific claims (lexical-entity-v1)`}>
      <ScoreValue value={score} />
      {supported !== undefined ? (
        <span className="ml-1 text-xs text-muted-foreground">
          ({supported}/{(supported ?? 0) + (unsupported ?? 0)} claims)
        </span>
      ) : null}
    </span>
  );
}
