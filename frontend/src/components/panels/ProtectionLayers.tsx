import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { ControlRoom } from "@/hooks/useControlRoom";
import type { Tone } from "@/lib/status";
import { fmt } from "@/lib/utils";

interface RowProps {
  layer: string;
  name: string;
  value: string;
  tone: Tone;
}

function Row({ layer, name, value, tone }: RowProps) {
  return (
    <div className="grouped-row flex items-center justify-between gap-3 py-3">
      <div className="min-w-0">
        <p className="text-t11 font-medium uppercase tracking-wide text-text-tertiary">{layer}</p>
        <p className="text-t13 font-medium text-text">{name}</p>
      </div>
      <Badge tone={tone} className="max-w-[55%] truncate">{value}</Badge>
    </div>
  );
}

export function ProtectionLayers({ room }: { room: ControlRoom }) {
  const s = room.snapshot;
  const keel = s?.keel;
  const sis = s?.sis;
  const gate = s?.gate ?? {};
  const rejected = Object.entries(gate).filter(([k]) => /reject|block|deny/i.test(k)).reduce((n, [, v]) => n + (Number(v) || 0), 0);
  const observer = s?.observer as { available?: boolean } | undefined;
  const keelTone: Tone = !keel?.verdict ? "neutral" : /allow|pass|ok|admit/i.test(keel.verdict) ? "good" : "warn";
  const suspicion = keel?.l2_suspicion ?? 0;

  return (
    <Card material="regular">
      <CardHeader>
        <div>
          <CardTitle>Protection layers</CardTitle>
          <CardDescription>Every command passes these in order before it reaches the plant.</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <Row layer="L3" name="Operator" value={s?.brain?.label ?? "Idle"} tone="info" />
        <Row
          layer="L2"
          name="Monitors"
          value={`Suspicion ${fmt(suspicion * 100, 0)}%${observer?.available ? " · AI observers on" : " · rules only"}`}
          tone={suspicion > 0.6 ? "bad" : suspicion > 0.3 ? "warn" : "good"}
        />
        <Row
          layer="L1"
          name="Keel enforcer"
          value={`${keel?.verdict ?? "No verdict"}${keel?.reasons?.length ? ` · ${keel.reasons.join(", ")}` : ""}`}
          tone={keelTone}
        />
        <Row layer="L0" name="Provenance gate" value={`${rejected} rejected`} tone={rejected ? "warn" : "good"} />
        <Row
          layer="SIS"
          name="Safety channel"
          value={sis?.tripped ? "Tripped" : "Armed"}
          tone={sis?.tripped ? "bad" : "good"}
        />
        {sis && (
          <p className="tabular mt-3 text-t11 text-text-tertiary">
            Trips at P ≥ {fmt(sis.limits.pressure_high, 0)} kPa · level ≤ {fmt(sis.limits.level_low, 0)}% or ≥ {fmt(sis.limits.level_high, 0)}%
          </p>
        )}
      </CardContent>
    </Card>
  );
}
