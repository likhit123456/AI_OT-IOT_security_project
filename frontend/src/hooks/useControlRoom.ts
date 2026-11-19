import { useCallback, useEffect, useRef, useState } from "react";
import { fetchState, postAction } from "@/lib/api";
import { telemetryExpired } from "@/lib/telemetryFreshness";
import type { ActionName, ActionResult, ChatMessage, EventRecord, HistoryRow, Sample, Snapshot } from "@/lib/types";

const POLL_MS = 1000;
const MAX_SAMPLES = 2400;
const MAX_EVENTS = 300;
const RECOVERY_ACTIONS = new Set<ActionName>(["reset_plant", "clear_faults", "reset_interlock", "clear_note"]);

function toSample(row: HistoryRow): Sample {
  return {
    seq: row[0],
    t: row[1],
    pressure: row[2],
    pressureTrue: row[3],
    level: row[4],
    levelTrue: row[5],
    f1: row[6],
    f2: row[7],
    purge: row[8],
    product: row[9],
    pressureSp: row[10],
    levelSp: row[11],
    tripped: row[12] === 1,
    authority: row[13]
  };
}

export interface ControlRoom {
  snapshot: Snapshot | null;
  samples: Sample[];
  events: EventRecord[];
  chat: ChatMessage[];
  connected: boolean;
  stale: boolean;
  notice: string;
  noticeKey: number;
  noticeKind: "error" | "success";
  clearNotice: () => void;
  pending: ActionName[];
  send: (action: ActionName, extra?: Record<string, unknown>) => Promise<ActionResult>;
}

export function useControlRoom(): ControlRoom {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [connected, setConnected] = useState(false);
  const [wallStale, setWallStale] = useState(true);
  const [notice, setNotice] = useState("");
  const [noticeKey, setNoticeKey] = useState(0);
  const [noticeKind, setNoticeKind] = useState<"error" | "success">("error");
  const cursor = useRef({ history: 0, event: 0, run: "" as string });
  const snapshotRef = useRef<Snapshot | null>(null);
  const lastSuccess = useRef(0);
  const online = useRef(false);
  const sending = useRef(new Set<ActionName>());
  const [pending, setPending] = useState<ActionName[]>([]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const watchdog = setInterval(() => {
      if (alive) setWallStale(telemetryExpired(lastSuccess.current));
    }, 500);

    const tick = async () => {
      try {
        const data = await fetchState(cursor.current.history, cursor.current.event, controller.signal);
        if (!alive) return;
        const run = String(data.run_id ?? "");
        let fresh = data;
        if (cursor.current.run && run !== cursor.current.run) {
          cursor.current = { history: 0, event: 0, run };
          setSamples([]);
          setEvents([]);
          fresh = await fetchState(0, 0, controller.signal);
          if (!alive) return;
        }
        // A second reset can happen during the full-history request too.
        cursor.current.run = String(fresh.run_id ?? "");
        const rows = (fresh.history ?? []).map(toSample);
        const incoming = fresh.events ?? [];
        const failure = cursor.current.event > 0 ? [...incoming].reverse().find((event) => event.kind === "error") : undefined;
        if (failure) {
          setNoticeKind("error");
          setNotice(failure.msg);
          setNoticeKey((k) => k + 1);
        }
        if (rows.length) {
          cursor.current.history = rows[rows.length - 1].seq;
          setSamples((prev) => [...prev, ...rows].slice(-MAX_SAMPLES));
        }
        if (incoming.length) {
          cursor.current.event = incoming[incoming.length - 1].id;
          setEvents((prev) => [...prev, ...incoming].slice(-MAX_EVENTS));
        }
        snapshotRef.current = fresh;
        lastSuccess.current = performance.now();
        online.current = true;
        setSnapshot(fresh);
        setConnected(true);
        setWallStale(false);
      } catch {
        online.current = false;
        if (alive) setConnected(false);
      } finally {
        if (alive) timer = setTimeout(tick, POLL_MS);
      }
    };

    tick();
    return () => {
      alive = false;
      online.current = false;
      controller.abort();
      clearTimeout(timer);
      clearInterval(watchdog);
    };
  }, []);

  const send = useCallback(async (action: ActionName, extra: Record<string, unknown> = {}) => {
    const current = snapshotRef.current;
    if (!RECOVERY_ACTIONS.has(action) && (!online.current || !current || current.telemetry_stale || telemetryExpired(lastSuccess.current))) {
      const result = { ok: false, error: "Waiting for current plant telemetry before accepting controls." };
      setNoticeKind("error");
      setNotice(result.error);
      setNoticeKey((k) => k + 1);
      return result;
    }
    if (sending.current.has(action)) return { ok: false, error: "This request is already pending." };
    sending.current.add(action);
    setPending([...sending.current]);
    try {
      const result = await postAction(action, { ...extra, expected_run_id: current?.run_id });
      setNoticeKind(result.ok ? "success" : "error");
      setNotice(result.ok ? `${action.replaceAll("_", " ")} request accepted.` : result.error ?? "Action rejected.");
      setNoticeKey((k) => k + 1);
      return result;
    } finally {
      sending.current.delete(action);
      setPending([...sending.current]);
    }
  }, []);

  return {
    snapshot: snapshot ? { ...snapshot, telemetry_stale: snapshot.telemetry_stale || wallStale } : null,
    samples,
    events,
    chat: snapshot?.chat ?? [],
    connected,
    pending,
    stale: !connected || wallStale || (snapshot?.telemetry_stale ?? true),
    notice,
    noticeKey,
    noticeKind,
    clearNotice: () => setNotice(""),
    send
  };
}
