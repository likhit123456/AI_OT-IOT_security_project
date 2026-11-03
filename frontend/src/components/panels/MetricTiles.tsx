import { Sparkline } from "@/components/Sparkline";
import type { ControlRoom } from "@/hooks/useControlRoom";
import type { Sample } from "@/lib/types";
import { cn, fmt } from "@/lib/utils";

interface ReadoutProps {
  index: number;
  label: string;
  value: string;
  unit: string;
  hint: string;
  warn?: boolean;
  stale: boolean;
  trend?: (number | null)[];
}

const POSITION = [
  "",
  "border-l pl-5 lg:pl-6",
  "border-t lg:border-l lg:border-t-0 lg:pl-6",
  "border-l border-t pl-5 lg:border-t-0 lg:pl-6"
];

function Readout({ index, label, value, unit, hint, warn, stale, trend }: ReadoutProps) {
  return (
    <div className={cn("flex flex-col border-rule py-6 pr-5 transition-opacity duration-ui ease-out lg:pr-6", POSITION[index], stale && "opacity-50")}>
      <p className="eyebrow">{label}</p>
      <p className="mt-4 flex items-baseline gap-2">
        <span className="tabular text-t44 text-text">{value}</span>
        <span className="text-t13 text-text-3">{unit}</span>
      </p>
      <div className="mt-4 flex-1">{trend ? <Sparkline values={trend} /> : <div className="h-6" />}</div>
      <p className={cn("tabular mt-3 text-t12", warn ? "text-warn" : "text-text-3")}>{hint}</p>
    </div>
  );
}

function recent(samples: Sample[], pick: (s: Sample) => number | null) {
  return samples.slice(-90).map(pick);
}

export function MetricTiles({ room }: { room: ControlRoom }) {
  const { snapshot, stale, samples } = room;
  const m = snapshot?.measured;
  const tr = snapshot?.truth;
  const targets = snapshot?.targets;
  const gap = (a?: number | null, b?: number | null, limit = 0) => {
    if (a === null || a === undefined || b === null || b === undefined) return { text: "Safety channel unavailable", warn: false };
    return { text: `Safety channel ${fmt(b, 1)} · Δ ${fmt(a - b, 1)}`, warn: Math.abs(a - b) > limit };
  };
  const p = gap(m?.pressure, tr?.pressure, 25);
  const l = gap(m?.level, tr?.level, 2);

  return (
    <div className="grid grid-cols-2 border-t border-rule lg:grid-cols-4">
      <Readout index={0} label="Pressure" value={fmt(m?.pressure, 0)} unit="kPa" hint={p.text} warn={p.warn} stale={stale} trend={recent(samples, (s) => s.pressure)} />
      <Readout index={1} label="Inventory" value={fmt(m?.level, 1)} unit="%" hint={l.text} warn={l.warn} stale={stale} trend={recent(samples, (s) => s.level)} />
      <Readout index={2} label="Product flow" value={fmt(m?.product_flow, 1)} unit="kmol/h" hint={`Target ${fmt(targets?.production_rate_sp, 1)}`} stale={stale} />
      <Readout
        index={3}
        label="Effective authority"
        value={fmt((snapshot?.brain?.effective ?? 0) * 100, 0)}
        unit="%"
        hint={`Keel ${fmt((snapshot?.keel?.authority ?? 0) * 100, 0)}% · set ${fmt((snapshot?.brain?.authority ?? 0) * 100, 0)}%`}
        stale={stale}
        trend={recent(samples, (s) => (s.authority === null ? null : s.authority * 100))}
      />
    </div>
  );
}
