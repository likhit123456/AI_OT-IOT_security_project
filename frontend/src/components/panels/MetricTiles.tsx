import { Card } from "@/components/ui/card";
import type { ControlRoom } from "@/hooks/useControlRoom";
import { fmt } from "@/lib/utils";

interface TileProps {
  label: string;
  value: string;
  unit: string;
  hint: string;
  stale: boolean;
}

function Tile({ label, value, unit, hint, stale }: TileProps) {
  return (
    <Card material="regular" className={`p-4 transition-opacity duration-300 ${stale ? "opacity-60" : ""}`}>
      <p className="text-t12 font-medium text-text-secondary">{label}</p>
      <p className="mt-2 flex items-baseline gap-1.5">
        <span className="tabular text-t34 font-semibold leading-none text-text">{value}</span>
        <span className="text-t13 text-text-tertiary">{unit}</span>
      </p>
      <p className="tabular mt-2 text-t11 text-text-tertiary">{hint}</p>
    </Card>
  );
}

export function MetricTiles({ room }: { room: ControlRoom }) {
  const { snapshot, stale } = room;
  const m = snapshot?.measured;
  const tr = snapshot?.truth;
  const targets = snapshot?.targets;
  const delta = (a?: number | null, b?: number | null) =>
    a === null || a === undefined || b === null || b === undefined ? "True value unavailable" : `True ${fmt(b, 1)} · Δ ${fmt(a - b, 1)}`;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile label="Pressure" value={fmt(m?.pressure, 0)} unit="kPa" hint={delta(m?.pressure, tr?.pressure)} stale={stale} />
      <Tile label="Inventory" value={fmt(m?.level, 1)} unit="%" hint={delta(m?.level, tr?.level)} stale={stale} />
      <Tile label="Product flow" value={fmt(m?.product_flow, 1)} unit="kmol/h" hint={`Target ${fmt(targets?.production_rate_sp, 1)}`} stale={stale} />
      <Tile
        label="Effective authority"
        value={fmt((snapshot?.brain?.effective ?? 0) * 100, 0)}
        unit="%"
        hint={`Keel ${fmt((snapshot?.keel?.authority ?? 0) * 100, 0)}% · set ${fmt((snapshot?.brain?.authority ?? 0) * 100, 0)}%`}
        stale={stale}
      />
    </div>
  );
}
