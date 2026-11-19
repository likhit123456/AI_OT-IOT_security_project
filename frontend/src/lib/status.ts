import type { Snapshot } from "@/lib/types";

export type Tone = "good" | "warn" | "bad" | "info" | "neutral";

export interface PlantStatus {
  tone: Tone;
  title: string;
  detail: string;
}

export function derivePlantStatus(snapshot: Snapshot | null, connected: boolean): PlantStatus {
  if (!connected || !snapshot) {
    return { tone: "neutral", title: "Disconnected", detail: "Showing the last known readings while the control room reconnects." };
  }
  if (snapshot.status === "error") {
    return { tone: "bad", title: "Plant error", detail: snapshot.error ?? "The control loop reported an error." };
  }
  if (snapshot.status !== "running") {
    return { tone: "info", title: "Starting up", detail: snapshot.status };
  }
  if (snapshot.telemetry_stale) {
    return { tone: "warn", title: "Stale telemetry", detail: "Readings are out of date. Operating controls are paused; recovery controls remain available." };
  }
  const sis = snapshot.sis;
  if (sis?.tripped) {
    const mode = sis.protection_mode ? ` ${sis.protection_mode}.` : "";
    const safe = sis.safe_state ? ` Safe state: ${sis.safe_state}.` : "";
    return { tone: "bad", title: "Safety trip", detail: `${sis.cause ?? sis.reason ?? "Protective action latched"}.${mode}${safe}` };
  }
  if (snapshot.keel?.reasons?.length || snapshot.keel?.divert) {
    return { tone: "warn", title: "Defense active", detail: `The Keel is limiting commands: ${snapshot.keel.reasons.join(", ") || "authority restricted"}.` };
  }
  if (Object.keys(snapshot.manual ?? {}).length) {
    return { tone: "info", title: "Manual control", detail: "Manual valve requests are active. Keel and the SIS retain authority over the final lineup." };
  }
  const faults = Object.keys(snapshot.faults ?? {}).length;
  const scenarios = Object.keys(snapshot.scenarios ?? {}).length;
  if (faults || scenarios) {
    return {
      tone: "warn",
      title: "Disturbance active",
      detail: `${faults} injected fault${faults === 1 ? "" : "s"} and ${scenarios} running scenario${scenarios === 1 ? "" : "s"}. The monitors and the Keel are watching.`
    };
  }
  return { tone: "good", title: "Normal operation", detail: "Every protection layer is armed and the plant is holding its targets." };
}

export const toneText: Record<Tone, string> = {
  good: "text-text",
  warn: "text-warn",
  bad: "text-bad",
  info: "text-text-2",
  neutral: "text-text-2"
};

export const toneDot: Record<Tone, string> = {
  good: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  info: "bg-text-3",
  neutral: "bg-text-3"
};
