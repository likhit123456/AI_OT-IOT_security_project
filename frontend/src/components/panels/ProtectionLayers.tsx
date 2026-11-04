import { Section } from "@/components/ui/section";
import { StatusText } from "@/components/ui/status-text";
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
    <li className="flex items-center gap-4 py-3.5">
      <span className="w-9 shrink-0 font-mono text-t11 text-text-3">{layer}</span>
      <span className="flex-1 text-t13 text-text">{name}</span>
      <StatusText tone={tone} className="max-w-[60%] justify-end text-right text-t12">{value}</StatusText>
    </li>
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
    <Section title="Protection layers">
      <ul className="divide-y divide-rule border-y border-rule">
        <Row layer="L3" name="Operator" value={s?.brain?.label ?? "Idle"} tone="neutral" />
        <Row
          layer="L2"
          name="Monitors"
          value={`Suspicion ${fmt(suspicion * 100, 0)}% · ${observer?.available ? "AI observers on" : "rules only"}`}
          tone={suspicion > 0.6 ? "bad" : suspicion > 0.3 ? "warn" : "good"}
        />
        <Row layer="L1" name="Keel enforcer" value={`${keel?.verdict ?? "No verdict"}${keel?.reasons?.length ? ` · ${keel.reasons.join(", ")}` : ""}`} tone={keelTone} />
        <Row layer="L0" name="Provenance gate" value={`${rejected} rejected`} tone={rejected ? "warn" : "good"} />
        <Row layer="SIS" name="Safety channel" value={sis?.tripped ? "Tripped" : "Armed"} tone={sis?.tripped ? "bad" : "good"} />
      </ul>
      {sis && (
        <p className="tabular mt-4 text-t12 text-text-3">
          Trips at P ≥ {fmt(sis.limits.pressure_high, 0)} kPa · level ≤ {fmt(sis.limits.level_low, 0)}% or ≥ {fmt(sis.limits.level_high, 0)}%
        </p>
      )}
    </Section>
  );
}
