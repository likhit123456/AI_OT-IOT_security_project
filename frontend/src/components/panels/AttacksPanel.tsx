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
  { action: "pressure_dropout", title: "Pressure dropout", detail: "The pressure transmitter reads zero half the time." }
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
  const active = [...Object.keys(snapshot?.faults ?? {}), ...Object.keys(snapshot?.scenarios ?? {})];

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
              <Button variant="danger" size="sm" className="shrink-0" onClick={() => send(action, extra)}>
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
              <Select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Fault">
                {["drift", "frozen", "noise", "dropout"].map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </Select>
            </div>
            <Input type="number" min={0} step="any" value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity" />
            <Button variant="danger" onClick={() => send("sensor_fault", { channel, mode, severity: Number(severity) || 0 })}>
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
            <HoldButton className="ml-auto" onConfirm={() => send("stolen_key", { ...lineup, duration_s: Number(duration) || 120 })}>
              Hold to take over
            </HoldButton>
          </div>
        </Section>
      </div>

      <Section title="Recovery">
        <p className="mb-5 text-t13 text-text-2">{active.length ? `Active: ${active.join(", ")}` : "No active faults or attacks."}</p>
        <div className="flex flex-wrap gap-3">
          <Button onClick={() => send("clear_faults")}>Clear faults and attacks</Button>
          <Button variant="quiet" onClick={() => send("reset_interlock")}>Re-arm SIS</Button>
          <HoldButton onConfirm={() => send("reset_plant")}>
            <RotateCcw size={14} />
            Hold to reset plant
          </HoldButton>
        </div>
      </Section>
    </div>
  );
}
