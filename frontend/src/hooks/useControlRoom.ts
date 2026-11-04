import { useCallback, useEffect, useRef, useState } from "react";
import { fetchState, postAction } from "@/lib/api";
import type { ActionName, ActionResult, ChatMessage, EventRecord, HistoryRow, Sample, Snapshot } from "@/lib/types";

const POLL_MS = 1000;
const MAX_SAMPLES = 2400;
const MAX_EVENTS = 300;

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
  clearNotice: () => void;
  send: (action: ActionName, extra?: Record<string, unknown>) => Promise<ActionResult>;
}

export function useControlRoom(): ControlRoom {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [connected, setConnected] = useState(false);
  const [notice, setNotice] = useState("");
  const [noticeKey, setNoticeKey] = useState(0);
  const cursor = useRef({ history: 0, event: 0, run: "" as string });
  const snapshotRef = useRef<Snapshot | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();

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
        }
        cursor.current.run = run;
        const rows = (fresh.history ?? []).map(toSample);
        const incoming = fresh.events ?? [];
        if (rows.length) {
          cursor.current.history = rows[rows.length - 1].seq;
          setSamples((prev) => [...prev, ...rows].slice(-MAX_SAMPLES));
        }
        if (incoming.length) {
          cursor.current.event = incoming[incoming.length - 1].id;
          setEvents((prev) => [...prev, ...incoming].slice(-MAX_EVENTS));
        }
        snapshotRef.current = fresh;
        setSnapshot(fresh);
        setConnected(true);
      } catch {
        if (alive) setConnected(false);
      } finally {
        if (alive) timer = setTimeout(tick, POLL_MS);
      }
    };

    tick();
    return () => {
      alive = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, []);

  const send = useCallback(async (action: ActionName, extra: Record<string, unknown> = {}) => {
    const current = snapshotRef.current;
    if (!current || current.telemetry_stale) {
      const result = { ok: false, error: "Waiting for current plant telemetry before accepting controls." };
      setNotice(result.error);
      setNoticeKey((k) => k + 1);
      return result;
    }
    const result = await postAction(action, extra);
    setNotice(result.ok ? "" : result.error ?? "Action rejected.");
    if (!result.ok) setNoticeKey((k) => k + 1);
    return result;
  }, []);

  return {
    snapshot,
    samples,
    events,
    chat: snapshot?.chat ?? [],
    connected,
    stale: !connected || (snapshot?.telemetry_stale ?? true),
    notice,
    noticeKey,
    clearNotice: () => setNotice(""),
    send
  };
}
