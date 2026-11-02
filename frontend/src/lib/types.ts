export type HistoryRow = [
  number, number, number | null, number | null, number | null, number | null,
  number | null, number | null, number | null, number | null,
  number | null, number | null, number, number | null, number | null, number | null
];

export interface Sample {
  seq: number;
  t: number;
  pressure: number | null;
  pressureTrue: number | null;
  level: number | null;
  levelTrue: number | null;
  f1: number | null;
  f2: number | null;
  purge: number | null;
  product: number | null;
  pressureSp: number | null;
  levelSp: number | null;
  tripped: boolean;
  authority: number | null;
}

export interface EventRecord {
  id: number;
  t: number;
  kind: string;
  msg: string;
}

export interface ChatMessage {
  id: number;
  t: number;
  role: string;
  text: string;
}

export interface Limits {
  pressure_high: number;
  pressure_rearm: number;
  level_low: number;
  level_low_rearm: number;
  level_high: number;
  level_high_rearm: number;
}

export interface Valves {
  f1: number | null;
  f2: number | null;
  purge: number | null;
  product: number | null;
}

export interface Snapshot {
  t?: number;
  run_id?: string | number;
  status: string;
  error: string | null;
  cycles?: number;
  telemetry_age_s: number | null;
  telemetry_stale: boolean;
  brain?: {
    kind: string;
    label: string;
    authority: number;
    effective: number;
    inflight: boolean;
    calls: number | null;
  };
  measured?: {
    pressure: number | null;
    level: number | null;
    product_flow: number | null;
    a_comp_pct: number | null;
    f1_flow: number | null;
    f2_flow: number | null;
    purge_flow: number | null;
  };
  truth?: { pressure: number | null; level: number | null; product_flow: number | null };
  valves?: Valves;
  commanded?: Valves | null;
  targets?: {
    pressure_sp: number | null;
    level_sp: number | null;
    production_rate_sp: number | null;
    a_comp_sp: number | null;
    authority: number;
    rationale: string;
  } | null;
  sis?: {
    tripped: boolean;
    reason: string | null;
    cause: string | null;
    protection_mode: string | null;
    safe_state: string | null;
    escalated: boolean;
    auto_rearm_in: number | null;
    lockout: boolean;
    can_reset: boolean;
    limits: Limits;
  };
  gate?: Record<string, number>;
  keel?: {
    authority: number;
    verdict: string | null;
    reasons: string[];
    divert: boolean;
    l2_suspicion: number;
  };
  governance?: Record<string, unknown> | null;
  observer?: Record<string, unknown>;
  scenarios?: Record<string, Record<string, unknown>>;
  faults?: Record<string, string>;
  decision?: Record<string, unknown> | null;
  chat?: ChatMessage[];
  manual?: Record<string, unknown>;
  note?: string | null;
  history?: HistoryRow[];
  events?: EventRecord[];
  decisions?: Record<string, unknown>[];
}

export type ActionName =
  | "authority"
  | "brain"
  | "level_drift"
  | "pressure_dropout"
  | "forged"
  | "operator_injection"
  | "stolen_key"
  | "oob_write"
  | "actuator_fault"
  | "sensor_fault"
  | "chat"
  | "clear_note"
  | "clear_faults"
  | "reset_interlock"
  | "reset_plant";

export interface ActionResult {
  ok: boolean;
  error?: string;
}
