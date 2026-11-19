import type { ActionName, ActionResult, Snapshot } from "@/lib/types";

export async function fetchState(historySeq: number, eventId: number, signal?: AbortSignal): Promise<Snapshot> {
  const timeout = AbortSignal.timeout(8000);
  const res = await fetch(`/api/state?h=${historySeq}&e=${eventId}`, {
    cache: "no-store", signal: signal ? AbortSignal.any([signal, timeout]) : timeout
  });
  if (!res.ok) throw new Error(`state request failed with ${res.status}`);
  const data = await res.json();
  if (!data || typeof data !== "object" || Array.isArray(data)
      || typeof data.status !== "string" || typeof data.telemetry_stale !== "boolean"
      || (data.run_id !== undefined && typeof data.run_id !== "string" && typeof data.run_id !== "number")
      || (data.history !== undefined && (!Array.isArray(data.history) || data.history.some((row: unknown) =>
        !Array.isArray(row) || row.length < 14 || !Number.isFinite(row[0]) || !Number.isFinite(row[1]))))
      || (data.events !== undefined && (!Array.isArray(data.events) || data.events.some((event: unknown) => {
        if (!event || typeof event !== "object") return true;
        const value = event as Record<string, unknown>;
        return !Number.isFinite(value.id) || !Number.isFinite(value.t) || typeof value.kind !== "string" || typeof value.msg !== "string";
      })))) {
    throw new Error("The control room returned an incomplete telemetry response.");
  }
  return data as Snapshot;
}

export async function postAction(action: ActionName, extra: Record<string, unknown> = {}): Promise<ActionResult> {
  try {
    const res = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...extra, action }),
      signal: AbortSignal.timeout(10000)
    });
    let data: ActionResult;
    try {
      data = (await res.json()) as ActionResult;
    } catch {
      return { ok: false, error: `The server returned an unreadable action response (HTTP ${res.status}). Check the latest state before retrying.` };
    }
    if (!data || typeof data !== "object" || typeof data.ok !== "boolean") {
      return { ok: false, error: "The server returned an incomplete action response. Check the latest state before retrying." };
    }
    const error = typeof data.error === "string" && data.error.trim() ? data.error :
      res.ok ? "The action was not accepted. Check the latest state." : `Action request failed (${res.status}).`;
    return res.ok && data.ok ? data : { ok: false, error };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "request failed" };
  }
}
