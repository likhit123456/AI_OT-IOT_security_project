import { useEffect, useMemo, useRef, useState } from "react";
import type { Sample } from "@/lib/types";
import { clock, fmt } from "@/lib/utils";

export type Metric = "pressure" | "level";

interface TrendChartProps {
  samples: Sample[];
  metric: Metric;
  windowSeconds: number;
  limits?: { high?: number; low?: number };
  showSetpoint: boolean;
  scale?: "fit" | "limits";
  height?: number;
}

const PAD = { top: 16, right: 16, bottom: 26, left: 48 };

function pick(sample: Sample, metric: Metric) {
  return metric === "pressure"
    ? { measured: sample.pressure, truth: sample.pressureTrue, sp: sample.pressureSp }
    : { measured: sample.level, truth: sample.levelTrue, sp: sample.levelSp };
}

export function TrendChart({ samples, metric, windowSeconds, limits, showSetpoint, scale = "fit", height = 280 }: TrendChartProps) {
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const view = useMemo(() => {
    if (!samples.length) return [];
    const end = samples[samples.length - 1].t;
    return windowSeconds > 0 ? samples.filter((s) => s.t >= end - windowSeconds) : samples;
  }, [samples, windowSeconds]);

  const geometry = useMemo(() => {
    if (view.length < 2) return null;
    const t0 = view[0].t;
    const t1 = view[view.length - 1].t;
    const values: number[] = [];
    view.forEach((s) => {
      const p = pick(s, metric);
      [p.measured, p.truth, showSetpoint ? p.sp : null].forEach((v) => {
        if (v !== null && Number.isFinite(v)) values.push(v);
      });
    });
    if (scale === "limits") {
      [limits?.high, limits?.low].forEach((v) => {
        if (v !== undefined && Number.isFinite(v)) values.push(v);
      });
    }
    if (!values.length) return null;
    let lo = Math.min(...values);
    let hi = Math.max(...values);
    const span = Math.max(hi - lo, metric === "pressure" ? 30 : 3);
    lo -= span * 0.12;
    hi += span * 0.12;
    const w = width - PAD.left - PAD.right;
    const h = height - PAD.top - PAD.bottom;
    const x = (t: number) => PAD.left + ((t - t0) / Math.max(t1 - t0, 1e-6)) * w;
    const y = (v: number) => PAD.top + (1 - (v - lo) / (hi - lo)) * h;
    return { t0, t1, lo, hi, w, h, x, y };
  }, [view, metric, width, height, limits, showSetpoint, scale]);

  const paths = useMemo(() => {
    if (!geometry) return null;
    const build = (key: "measured" | "truth" | "sp") => {
      let d = "";
      let open = false;
      view.forEach((s) => {
        const v = pick(s, metric)[key];
        if (v === null || !Number.isFinite(v)) {
          open = false;
          return;
        }
        d += `${open ? "L" : "M"}${geometry.x(s.t).toFixed(1)},${geometry.y(v).toFixed(1)}`;
        open = true;
      });
      return d;
    };
    const measured = build("measured");
    const baseline = PAD.top + geometry.h;
    const first = view.find((s) => pick(s, metric).measured !== null);
    const last = [...view].reverse().find((s) => pick(s, metric).measured !== null);
    const area =
      measured && first && last
        ? `${measured}L${geometry.x(last.t).toFixed(1)},${baseline}L${geometry.x(first.t).toFixed(1)},${baseline}Z`
        : "";
    return { measured, truth: build("truth"), sp: build("sp"), area };
  }, [geometry, view, metric]);

  const tripBands = useMemo(() => {
    if (!geometry) return [];
    const bands: { x1: number; x2: number }[] = [];
    let start: number | null = null;
    view.forEach((s, i) => {
      if (s.tripped && start === null) start = s.t;
      const closing = start !== null && (!s.tripped || i === view.length - 1);
      if (closing && start !== null) {
        bands.push({ x1: geometry.x(start), x2: geometry.x(s.t) });
        start = null;
      }
    });
    return bands;
  }, [geometry, view]);

  const unit = metric === "pressure" ? "kPa" : "%";
  const ticks = geometry ? Array.from({ length: 5 }, (_, i) => geometry.lo + ((geometry.hi - geometry.lo) * i) / 4) : [];
  const hovered = hover !== null && view[hover] ? view[hover] : null;

  const onMove = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!geometry || !view.length) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * width;
    const ratio = Math.min(1, Math.max(0, (px - PAD.left) / geometry.w));
    const target = geometry.t0 + ratio * (geometry.t1 - geometry.t0);
    let best = 0;
    let gap = Infinity;
    view.forEach((s, i) => {
      const d = Math.abs(s.t - target);
      if (d < gap) {
        gap = d;
        best = i;
      }
    });
    setHover(best);
  };

  return (
    <div ref={host} className="relative w-full" style={{ height }}>
      {!geometry || !paths ? (
        <div className="flex h-full items-center justify-center text-t13 text-text-3">Waiting for telemetry</div>
      ) : (
        <svg
          width="100%"
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
          role="img"
          aria-label={`${metric} trend`}
        >
          <defs>
            <linearGradient id="trip-band" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--bad)" stopOpacity="0.2" />
              <stop offset="100%" stopColor="var(--bad)" stopOpacity="0.03" />
            </linearGradient>
            <linearGradient id={`fill-${metric}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--text)" stopOpacity="0.1" />
              <stop offset="100%" stopColor="var(--text)" stopOpacity="0" />
            </linearGradient>
          </defs>
          {tripBands.map((band, i) => (
            <rect key={i} x={band.x1} y={PAD.top} width={Math.max(band.x2 - band.x1, 2)} height={geometry.h} fill="url(#trip-band)" />
          ))}
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={PAD.left} x2={width - PAD.right} y1={geometry.y(tick)} y2={geometry.y(tick)} stroke="var(--rule)" />
              <text x={PAD.left - 8} y={geometry.y(tick) + 4} textAnchor="end" fontSize="11" fill="var(--text-3)" className="tabular">
                {fmt(tick, metric === "pressure" ? 0 : 1)}
              </text>
            </g>
          ))}
          <text x={PAD.left} y={height - 6} fontSize="11" fill="var(--text-3)" className="tabular">
            {clock(geometry.t0)}
          </text>
          <text x={width - PAD.right} y={height - 6} textAnchor="end" fontSize="11" fill="var(--text-3)" className="tabular">
            {clock(geometry.t1)}
          </text>
          {[limits?.high, limits?.low].map((limit, i) =>
            limit !== undefined && Number.isFinite(limit) && limit >= geometry.lo && limit <= geometry.hi ? (
              <line
                key={i}
                x1={PAD.left}
                x2={width - PAD.right}
                y1={geometry.y(limit)}
                y2={geometry.y(limit)}
                stroke="var(--bad)"
                strokeOpacity="0.7"
                strokeDasharray="2 5"
                strokeLinecap="round"
              />
            ) : null
          )}
          {[limits?.high, limits?.low].map((limit, i) => {
            if (limit === undefined || !Number.isFinite(limit)) return null;
            if (limit >= geometry.lo && limit <= geometry.hi) return null;
            const above = limit > geometry.hi;
            return (
              <text
                key={`edge-${i}`}
                x={width - PAD.right}
                y={above ? PAD.top + 10 : PAD.top + geometry.h - 4}
                textAnchor="end"
                fontSize="11"
                fill="var(--bad)"
                fillOpacity="0.85"
                className="tabular"
              >
                {above ? "▲" : "▼"} trip limit {fmt(limit, 0)} {unit}
              </text>
            );
          })}
          <path d={paths.area} fill={`url(#fill-${metric})`} />
          {showSetpoint && paths.sp && (
            <path d={paths.sp} fill="none" stroke="var(--text-3)" strokeWidth="1" strokeDasharray="1 5" strokeLinecap="round" />
          )}
          {paths.truth && (
            <path d={paths.truth} fill="none" stroke="var(--text-3)" strokeWidth="1.25" strokeDasharray="5 4" strokeLinejoin="round" />
          )}
          <path d={paths.measured} fill="none" stroke="var(--text)" strokeWidth="1.75" strokeLinejoin="round" strokeLinecap="round" />
          {hovered && (
            <g>
              <line x1={geometry.x(hovered.t)} x2={geometry.x(hovered.t)} y1={PAD.top} y2={PAD.top + geometry.h} stroke="var(--rule-strong)" strokeWidth="1" />
              {pick(hovered, metric).measured !== null && (
                <circle cx={geometry.x(hovered.t)} cy={geometry.y(pick(hovered, metric).measured as number)} r="4" fill="var(--text)" />
              )}
            </g>
          )}
        </svg>
      )}
      {hovered && geometry && (
        <div
          className="glass pointer-events-none absolute top-2 rounded-[12px] px-3 py-2 text-t12"
          style={{ left: Math.min(Math.max(geometry.x(hovered.t) + 12, 8), width - 168) }}
        >
          <div className="tabular font-semibold text-text">{clock(hovered.t)}</div>
          <div className="tabular text-text">Measured {fmt(pick(hovered, metric).measured, 1)} {unit}</div>
          <div className="tabular text-text-2">True {fmt(pick(hovered, metric).truth, 1)} {unit}</div>
          {showSetpoint && <div className="tabular text-text-3">Setpoint {fmt(pick(hovered, metric).sp, 1)} {unit}</div>}
        </div>
      )}
    </div>
  );
}
