import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { HoldButton } from "@/components/ui/hold-button";
import { Input, Select } from "@/components/ui/input";
import { Section } from "@/components/ui/section";
import { Slider } from "@/components/ui/slider";
import type { ControlRoom } from "@/hooks/useControlRoom";
import type { ActionName } from "@/lib/types";

interface Scenario {
  action: ActionName;
  title: string;
  detail: string;
  extra?: Record<string, unknown>;
}

const SCENARIOS: Scenario[] = [
  { action: "forged", title: "Forged commands", detail: "A compromised agent sends unsigned, wrong-key and replayed valve commands.", extra: { duration_s: 60 } },
  { action: "operator_injection", title: "Scripted injection", detail: "Hostile text reaches the operator through an advisory channel.", extra: { duration_s: 60 } },
  { action: "actuator_fault", title: "Product valve fault", detail: "The valve loses capacity, then sticks while L1 is still moving it.", extra: { valve: "product", how: "demo" } },
  { action: "level_drift", title: "Level drift", detail: "The level transmitter drifts high so L1 drains the true inventory." },
  { action: "pressure_dropout", title: "Pressure dropout", detail: "The pressure transmitter reads zero half the time." },
  { action: "oob_write", title: "Out-of-band valve write", detail: "Write purge and product directly to the simulator. The provenance gate detects and overwrites the bypass.", extra: { purge: 100, product: 100 } }
];

const CHANNELS = [
  ["tank_pressure", "Reactor pressure"],
  ["tank_level", "Tank level"],
  ["f1_flow", "Feed 1 flow"],
  ["f2_flow", "Feed 2 flow"],
  ["purge_flow", "Purge flow"],
  ["product_flow", "Product flow"],
  ["analyzer", "Composition analyzer"]
];

const LINEUP = [
  ["f1", "Feed 1"],
  ["f2", "Feed 2"],
  ["purge", "Purge"],
  ["product", "Product"]
] as const;

export function AttacksPanel({ room }: { room: ControlRoom }) {
  const { send, snapshot } = room;
  const [channel, setChannel] = useState("tank_pressure");
  const [mode, setMode] = useState("drift");
  const [severity, setSeverity] = useState("60");
  const [lineup, setLineup] = useState({ f1: 100, f2: 100, purge: 0, product: 0 });
  const [duration, setDuration] = useState("120");
  const [actuator, setActuator] = useState("product");
  const [actuatorMode, setActuatorMode] = useState("demo");
  const active = [...Object.keys(snapshot?.faults ?? {}), ...Object.keys(snapshot?.scenarios ?? {})];
  const busy = (action: ActionName) => room.pending.includes(action);
  const severityValue = Number(severity);
  const validSeverity = mode === "frozen" || (severity.trim() !== "" && Number.isFinite(severityValue)
    && (mode === "drift" || severityValue >= 0) && (mode !== "dropout" || severityValue <= 1));
  const validDuration = duration.trim() !== "" && Number.isFinite(Number(duration)) && Number(duration) >= 5 && Number(duration) <= 600;
  const severityHelp = mode === "dropout" ? "Fraction of each 10-second window: 0 to 1 (0.5 = 50%)."
    : mode === "drift" ? "Offset added per hour, in the selected sensor's units. Negative drift is supported."
    : mode === "noise" ? "Nonnegative noise amplitude, in the selected sensor's units."
    : "Freeze the reading at its current value; no severity is required.";

  return (
    <div className="space-y-14">
      <Section title="Scenarios">
        <ul className="divide-y divide-rule border-y border-rule">
          {SCENARIOS.map(({ action, title, detail, extra }) => (
            <li key={title} className="flex items-center justify-between gap-6 py-5">
              <div className="min-w-0">
                <p className="text-t17 font-medium text-text">{title}</p>
                <p className="mt-1 text-t13 text-text-2">{detail}</p>
              </div>
              <Button disabled={room.stale || busy(action)} variant="danger" size="sm" className="shrink-0" onClick={() => send(action, extra)}>
                Launch
              </Button>
            </li>
          ))}
        </ul>
      </Section>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-14 lg:grid-cols-2 lg:gap-16">
        <Section title="Custom sensor fault">
          <p className="mb-5 text-t13 text-text-2">Corrupt what the AI and L1 see. The SIS reads its own channel.</p>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Select value={channel} onChange={(e) => setChannel(e.target.value)} aria-label="Sensor">
                {CHANNELS.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </Select>
              <Select value={mode} onChange={(e) => {
                const next = e.target.value;
                setMode(next);
                setSeverity(next === "dropout" ? "0.5" : next === "frozen" ? "0" : next === "noise" ? "1" : "60");
              }} aria-label="Fault">
                {["drift", "frozen", "noise", "dropout"].map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </Select>
            </div>
            <Input type="number" disabled={mode === "frozen"} min={mode === "drift" ? undefined : 0} max={mode === "dropout" ? 1 : undefined} step="any" value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity" aria-describedby="fault-severity-help" aria-invalid={!validSeverity} />
            <p id="fault-severity-help" className="text-t12 text-text-3">{severityHelp}</p>
            <Button variant="danger" disabled={room.stale || busy("sensor_fault") || !validSeverity} onClick={() => send("sensor_fault", { channel, mode, severity: mode === "frozen" ? 0 : severityValue })}>
              Inject fault
            </Button>
          </div>
        </Section>

        <Section title="Stolen signing key">
          <p className="mb-4 text-t13 text-text-2">The gate admits the command. Choose the valve lineup it forces.</p>
          <div className="divide-y divide-rule border-y border-rule">
            {LINEUP.map(([key, label]) => (
              <div key={key} className="flex items-center gap-4 py-1.5">
                <span className="w-16 text-t13 text-text">{label}</span>
                <Slider min={0} max={100} step={5} value={[lineup[key]]} onValueChange={([v]) => setLineup((prev) => ({ ...prev, [key]: v }))} aria-label={label} />
                <span className="tabular w-10 text-right text-t12 text-text-2">{lineup[key]}%</span>
              </div>
            ))}
          </div>
          <div className="mt-5 flex items-center gap-3">
            <Input type="number" min={5} max={600} value={duration} onChange={(e) => setDuration(e.target.value)} className="w-28" aria-label="Duration in seconds" />
            <span className="text-t12 text-text-3">seconds</span>
            <HoldButton disabled={room.stale || busy("stolen_key") || !validDuration} className="ml-auto" onConfirm={() => send("stolen_key", { ...lineup, duration_s: Number(duration) })}>
              Hold to take over
            </HoldButton>
          </div>
          {!validDuration && <p className="mt-3 text-t12 text-bad" role="alert">Duration must be between 5 and 600 seconds.</p>}
        </Section>
      </div>

      <Section title="Custom actuator fault">
        <p className="mb-5 text-t13 text-text-2">Choose a valve and inject a stuck actuator, a capacity loss, or the staged demonstration.</p>
        <div className="flex flex-wrap gap-3">
          <Select value={actuator} onChange={(e) => setActuator(e.target.value)} aria-label="Actuator valve">
            {LINEUP.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </Select>
          <Select value={actuatorMode} onChange={(e) => setActuatorMode(e.target.value)} aria-label="Actuator fault mode">
            <option value="demo">Capacity loss, then stuck</option>
            <option value="stuck">Stuck in place</option>
            <option value="cv">Half capacity</option>
          </Select>
          <Button variant="danger" disabled={room.stale || busy("actuator_fault")} onClick={() => send("actuator_fault", { valve: actuator, how: actuatorMode })}>Inject actuator fault</Button>
        </div>
      </Section>

      <Section title="Recovery">
        {snapshot?.sis?.tripped && <p className="mb-5 text-t13 text-text-2">The SIS controls the final valve lineup during this trip. Clear faults, then re-arm when eligible, or reset the plant for a fresh scenario.</p>}
        <p className="mb-5 text-t13 text-text-2">{active.length ? `Active: ${active.join(", ")}` : "No active faults or attacks."}</p>
        <div className="flex flex-wrap gap-3">
          <Button disabled={busy("clear_faults")} onClick={() => send("clear_faults")}>Clear faults and attacks</Button>
          <Button variant="quiet" disabled={room.stale || !snapshot?.sis?.can_reset || busy("reset_interlock")} onClick={() => send("reset_interlock")}>Re-arm SIS</Button>
          <HoldButton disabled={busy("reset_plant") || snapshot?.status?.startsWith("resetting")} onConfirm={() => send("reset_plant")}>
            <RotateCcw size={14} />
            Hold to reset plant
          </HoldButton>
        </div>
      </Section>
    </div>
  );
}
