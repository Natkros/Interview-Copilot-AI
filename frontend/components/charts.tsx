"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";

/*
 * Hand-rolled SVG charts following the dataviz method: single-series forms
 * (title names the series, no legend box), 2px lines with ringed >=8px dots,
 * <=24px bars with a 4px rounded data-end, hairline recessive grid, text in ink
 * tokens (never the series colour), hover tooltips, and a table view.
 */

interface Point {
  label: string;
  value: number; // 0..1
  sub?: string;
}

function Tooltip({ x, y, children }: { x: number; y: number; children: React.ReactNode }) {
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-md border bg-popover px-2.5 py-1.5 text-xs text-popover-foreground shadow-md"
      style={{ left: x, top: y - 10 }}
    >
      {children}
    </div>
  );
}

export function ChartFrame({
  title,
  description,
  table,
  children,
}: {
  title: string;
  description?: string;
  table: { headers: string[]; rows: (string | number)[][] };
  children: React.ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    <figure className="rounded-xl border bg-[var(--chart-surface)] p-4">
      <figcaption className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-[var(--chart-ink)]">{title}</div>
          {description ? <div className="text-xs text-[var(--chart-ink-2)]">{description}</div> : null}
        </div>
        <button
          type="button"
          onClick={() => setAsTable((v) => !v)}
          className="shrink-0 rounded px-1.5 py-0.5 text-xs text-[var(--chart-ink-2)] underline-offset-2 hover:underline"
          aria-pressed={asTable}
        >
          {asTable ? "View chart" : "View table"}
        </button>
      </figcaption>
      {asTable ? (
        <table className="w-full text-sm">
          <thead>
            <tr>{table.headers.map((h) => <th key={h} className="border-b py-1 text-left font-medium text-[var(--chart-ink-2)]">{h}</th>)}</tr>
          </thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i}>{r.map((c, j) => <td key={j} className={cn("border-b py-1", j > 0 && "tabular-nums")}>{c}</td>)}</tr>
            ))}
          </tbody>
        </table>
      ) : (
        children
      )}
    </figure>
  );
}

/** Score over sessions (0-100%), one series. */
export function ProgressLine({ points, height = 200 }: { points: Point[]; height?: number }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [rendered, setRendered] = useState(640);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setRendered(entry.contentRect.width || 640));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const width = 640;
  const pad = { l: 36, r: 44, t: 12, b: 28 };
  const iw = width - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const xs = points.map((_, i) => pad.l + (points.length === 1 ? iw / 2 : (i / (points.length - 1)) * iw));
  const y = (v: number) => pad.t + (1 - v) * ih;
  const path = points.map((p, i) => `${i ? "L" : "M"}${xs[i].toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const area = points.length > 1 ? `${path} L${xs[xs.length - 1]},${y(0)} L${xs[0]},${y(0)} Z` : "";
  if (!points.length) return <p className="py-10 text-center text-sm text-muted-foreground">Complete an interview to see progress.</p>;
  const last = points.length - 1;
  const scale = rendered / width;
  return (
    <div ref={wrap} className="relative" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img" aria-label="Score over sessions">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke={t === 0 ? "var(--chart-axis)" : "var(--chart-grid)"} strokeWidth={1} />
            <text x={pad.l - 6} y={y(t)} textAnchor="end" dominantBaseline="middle" fontSize={11} fill="var(--chart-muted)" className="tabular-nums">
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        {area ? <path d={area} fill="var(--chart-wash)" /> : null}
        <path d={path} fill="none" stroke="var(--chart-series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {hover !== null ? <line x1={xs[hover]} x2={xs[hover]} y1={pad.t} y2={y(0)} stroke="var(--chart-axis)" strokeWidth={1} /> : null}
        {points.map((p, i) => (
          <g key={i}>
            <circle cx={xs[i]} cy={y(p.value)} r={hover === i ? 5.5 : 4} fill="var(--chart-series-1)" stroke="var(--chart-surface)" strokeWidth={2} />
            {/* hit target larger than the mark */}
            <rect
              x={xs[i] - Math.max(12, iw / Math.max(1, points.length) / 2)}
              y={pad.t}
              width={Math.max(24, iw / Math.max(1, points.length))}
              height={ih}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
              tabIndex={0}
              aria-label={`${p.label}: ${Math.round(p.value * 100)}%`}
            />
          </g>
        ))}
        <text x={xs[last] + 8} y={y(points[last].value)} dominantBaseline="middle" fontSize={12} fill="var(--chart-ink)" fontWeight={600}>
          {Math.round(points[last].value * 100)}%
        </text>
        {points.length <= 12
          ? points.map((p, i) => (
              <text key={i} x={xs[i]} y={height - 8} textAnchor="middle" fontSize={10} fill="var(--chart-muted)">
                {i === 0 || i === last || points.length <= 6 ? p.label : ""}
              </text>
            ))
          : null}
      </svg>
      {hover !== null ? (
        <Tooltip x={xs[hover] * scale} y={y(points[hover].value) * scale}>
          <div className="font-medium">{Math.round(points[hover].value * 100)}%</div>
          <div className="text-muted-foreground">{points[hover].label}{points[hover].sub ? ` · ${points[hover].sub}` : ""}</div>
        </Tooltip>
      ) : null}
    </div>
  );
}

/** Horizontal bars for one measure across categories. `format` renders the value label. */
export function HBars({
  items,
  max,
  format = (v) => `${Math.round(v * 100)}%`,
  emptyText = "No data yet.",
}: {
  items: { label: string; value: number; sub?: string }[];
  max?: number;
  format?: (v: number) => string;
  emptyText?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const top = useMemo(() => max ?? Math.max(1e-9, ...items.map((i) => i.value)), [items, max]);
  if (!items.length) return <p className="py-6 text-center text-sm text-muted-foreground">{emptyText}</p>;
  return (
    <ul className="space-y-2.5" onMouseLeave={() => setHover(null)}>
      {items.map((it, i) => {
        const w = Math.max(0, Math.min(1, it.value / top));
        return (
          <li
            key={it.label}
            className="grid grid-cols-[minmax(0,9rem)_1fr_3.5rem] items-center gap-3 text-sm"
            onMouseEnter={() => setHover(i)}
            title={it.sub ? `${it.label}: ${format(it.value)} · ${it.sub}` : undefined}
          >
            <span className="truncate text-[var(--chart-ink-2)]">{it.label}</span>
            <span className="relative h-4" aria-hidden>
              <span className="absolute inset-y-0 left-0 right-0 my-auto h-px bg-[var(--chart-grid)]" />
              <span
                className="absolute inset-y-0 left-0 rounded-r-[4px] transition-[width]"
                style={{ width: `${w * 100}%`, background: "var(--chart-series-1)", opacity: hover === null || hover === i ? 1 : 0.55 }}
              />
            </span>
            <span className="text-right tabular-nums text-[var(--chart-ink)]">{format(it.value)}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function StatTile({ label, value, sub, className }: { label: string; value: React.ReactNode; sub?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-xl border p-4", className)}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      {sub ? <div className="mt-1 text-xs text-muted-foreground">{sub}</div> : null}
    </div>
  );
}
