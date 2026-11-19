import { Section } from "@/components/ui/section";
import { StatusText } from "@/components/ui/status-text";
import type { ControlRoom } from "@/hooks/useControlRoom";
import type { Tone } from "@/lib/status";
import { fmt } from "@/lib/utils";
import { observerStatus, type ObserverStatus } from "@/lib/observerStatus";

interface RowProps {
  layer: string;
  name: string;
  value: string;
  tone: Tone;
}

interface Review {
  label: string | null;
  raw_label: string | null;
  status: string;
  reason: string;
  findings?: { label: string; reason: string }[];
}

function EvidenceResult({ name, review, stale }: { name: string; review?: Review; stale: boolean }) {
  const uncertain = !review?.label || stale;
  const label = stale ? "Unavailable" : uncertain ? "Unconfirmed" : review?.label === "normal" ? "Normal" : `Supported: ${review!.label!.replaceAll("_", " ")}`;
  const tone: Tone = uncertain ? "neutral" : review?.label === "normal" ? "good" : "warn";
  return (
    <div className="py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="text-t13 text-text">{name}</span>
        <StatusText tone={tone}>{label}</StatusText>
      </div>
      <p className="mt-1 text-t12 text-text-3">Model: {review?.raw_label?.replaceAll("_", " ") ?? "No current verdict"}</p>
      <p className="mt-1 text-t12 text-text-2">{stale ? "Waiting for current readings." : review?.reason ?? "Collecting evidence."}</p>
      {!stale && review?.findings?.filter(f => f.label !== review.label).map(f => (
        <p key={`${f.label}:${f.reason}`} className="mt-1 text-t12 text-text-2">Also {f.label.replaceAll("_", " ")}: {f.reason}</p>
      ))}
    </div>
  );
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
  const observer = s?.observer as ObserverStatus & {
    evidence_review?: { enabled: boolean; proposals?: Review; telemetry?: Review };
  } | undefined;
  const keelTone: Tone = !keel?.verdict ? "neutral" : /allow|pass|ok|admit/i.test(keel.verdict) ? "good" : "warn";
  const suspicion = keel?.l2_suspicion ?? 0;

  return (
    <Section title="Protection layers">
      <ul className="divide-y divide-rule border-y border-rule">
        <Row layer="L3" name="Operator" value={s?.brain?.label ?? "Idle"} tone="neutral" />
        <Row
          layer="L2"
          name="Monitors"
          value={`Suspicion ${fmt(suspicion * 100, 0)}% · ${observerStatus(observer, s?.t, room.stale)}`}
          tone={room.stale ? "neutral" : suspicion > 0.6 ? "bad" : suspicion > 0.3 ? "warn" : "neutral"}
        />
        <Row layer="L1" name="Keel enforcer" value={`${keel?.verdict ?? "No verdict"}${keel?.reasons?.length ? ` · ${keel.reasons.join(", ")}` : ""}`} tone={keelTone} />
        <Row layer="L0" name="Provenance gate" value={`${rejected} rejected`} tone={rejected ? "warn" : "good"} />
        <Row layer="SIS" name="Safety channel" value={room.stale ? "Unknown — telemetry unavailable" : sis?.tripped ? "Tripped" : "Armed"} tone={room.stale ? "neutral" : sis?.tripped ? "bad" : "good"} />
      </ul>
      {observer?.evidence_review?.enabled && (
        <div className="mt-4 divide-y divide-rule rounded border border-rule px-3">
          <p className="py-3 text-t12 text-text-3">Model predictions checked against input evidence</p>
          <EvidenceResult name="Proposal review" review={observer.evidence_review.proposals} stale={room.stale} />
          <EvidenceResult name="Telemetry review" review={observer.evidence_review.telemetry} stale={room.stale} />
        </div>
      )}
      {sis && (
        <p className="tabular mt-4 text-t12 text-text-3">
          Trips at P ≥ {fmt(sis.limits.pressure_high, 0)} kPa · level ≤ {fmt(sis.limits.level_low, 0)}% or ≥ {fmt(sis.limits.level_high, 0)}%
          {sis.limits.pressure_low !== undefined && ` · feed-starvation recovery at P ≤ ${fmt(sis.limits.pressure_low, 0)} kPa`}
        </p>
      )}
    </Section>
  );
}
