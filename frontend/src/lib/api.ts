import type { ActionName, ActionResult, Snapshot } from "@/lib/types";

export async function fetchState(historySeq: number, eventId: number, signal?: AbortSignal): Promise<Snapshot> {
  const res = await fetch(`/api/state?h=${historySeq}&e=${eventId}`, { cache: "no-store", signal });
  if (!res.ok) throw new Error(`state request failed with ${res.status}`);
  return (await res.json()) as Snapshot;
}

export async function postAction(action: ActionName, extra: Record<string, unknown> = {}): Promise<ActionResult> {
  try {
    const res = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...extra })
    });
    return (await res.json()) as ActionResult;
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "request failed" };
  }
}
