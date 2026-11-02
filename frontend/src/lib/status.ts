import type { Snapshot } from "@/lib/types";

export type Tone = "good" | "warn" | "bad" | "info" | "neutral";

export interface PlantStatus {
  tone: Tone;
  title: string;
  detail: string;
}

export function derivePlantStatus(snapshot: Snapshot | null, connected: boolean): PlantStatus {
  if (!connected || !snapshot) {
    return { tone: "neutral", title: "Disconnected", detail: "Showing last known readings. Reconnecting to the control room." };
  }
  if (snapshot.status === "error") {
    return { tone: "bad", title: "Plant error", detail: snapshot.error ?? "The control loop reported an error." };
  }
  if (snapshot.status !== "running") {
    return { tone: "info", title: "Starting up", detail: "The simulator is restarting and settling at the design point." };
  }
  const sis = snapshot.sis;
  if (sis?.tripped) {
    const mode = sis.protection_mode ? ` ${sis.protection_mode}` : "";
    return {
      tone: "bad",
      title: "Safety trip",
      detail: `${sis.cause ?? sis.reason ?? "Protective action latched"}.${mode} ${sis.safe_state ? `Safe state: ${sis.safe_state}.` : ""}`.trim()
    };
  }
  const faults = Object.keys(snapshot.faults ?? {}).length;
  const scenarios = Object.keys(snapshot.scenarios ?? {}).length;
  if (faults || scenarios) {
    return {
      tone: "warn",
      title: "Disturbance active",
      detail: `${faults} injected fault${faults === 1 ? "" : "s"} and ${scenarios} running scenario${scenarios === 1 ? "" : "s"}. The monitors and Keel are watching.`
    };
  }
  return { tone: "good", title: "Normal operation", detail: "All protection layers are armed and the plant is holding its targets." };
}

export const toneText: Record<Tone, string> = {
  good: "text-green",
  warn: "text-orange",
  bad: "text-red",
  info: "text-blue",
  neutral: "text-text-secondary"
};

export const toneTint: Record<Tone, string> = {
  good: "rgba(52,199,89,0.22)",
  warn: "rgba(255,149,0,0.22)",
  bad: "rgba(255,59,48,0.26)",
  info: "rgba(0,122,255,0.2)",
  neutral: "rgba(120,120,128,0.16)"
};
