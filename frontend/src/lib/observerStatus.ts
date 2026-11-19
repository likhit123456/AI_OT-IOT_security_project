type Verdict = { t?: number; label?: string | null; note?: string };
export interface ObserverStatus {
  models?: Record<string, string | null>;
  proposals?: Verdict | null;
  raw_proposals?: Verdict | null;
  llm_telemetry?: Verdict | null;
  telemetry_specialist?: boolean;
  cpu_status?: string;
}

export function observerStatus(observer: ObserverStatus | undefined, now: number | undefined, stale: boolean) {
  if (stale || !observer) return "Monitor availability unverified";
  const configured = Object.values(observer.models ?? {}).some(Boolean);
  const recent = [observer.raw_proposals ?? observer.proposals, observer.llm_telemetry].some(v =>
    v?.label && v.note === "ok" && typeof v.t === "number" && typeof now === "number" && now >= v.t && now - v.t <= 30);
  const llm = configured ? (recent ? "AI configured; recent inference received" : "AI configured; availability unverified") : "AI not configured";
  const cpu = observer.telemetry_specialist === true
    ? (observer.cpu_status === "ok" ? "CPU detector reporting" : `CPU detector: ${observer.cpu_status ?? "status unknown"}`)
    : "CPU detector unavailable";
  return `Rules · ${llm} · ${cpu}`;
}
