import { useState } from "react";
import { Flame, KeyRound, Radio, RotateCcw, ScanLine, ShieldCheck, Syringe, Wrench } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import type { ControlRoom } from "@/hooks/useControlRoom";
import type { ActionName } from "@/lib/types";

interface Scenario {
  action: ActionName;
  title: string;
  detail: string;
  Icon: LucideIcon;
  extra?: Record<string, unknown>;
}

const SCENARIOS: Scenario[] = [
  { action: "forged", title: "Forged commands", detail: "A compromised agent sends unsigned, wrong-key and replayed valve commands.", Icon: Syringe, extra: { duration_s: 60 } },
  { action: "operator_injection", title: "Scripted injection", detail: "Hostile text reaches the operator through an advisory channel.", Icon: Radio, extra: { duration_s: 60 } },
  { action: "actuator_fault", title: "Product valve fault", detail: "The valve loses capacity, then sticks while L1 is still moving it.", Icon: Wrench, extra: { valve: "product", how: "demo" } },
  { action: "level_drift", title: "Level drift", detail: "The level transmitter drifts high so L1 drains the true inventory.", Icon: ScanLine },
  { action: "pressure_dropout", title: "Pressure dropout", detail: "The pressure transmitter reads zero half the time.", Icon: Flame }
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

export function AttacksPanel({ room }: { room: ControlRoom }) {
  const { send, snapshot } = room;
  const [channel, setChannel] = useState("tank_pressure");
  const [mode, setMode] = useState("drift");
  const [severity, setSeverity] = useState("60");
  const [lineup, setLineup] = useState({ f1: 100, f2: 100, purge: 0, product: 0 });
  const [duration, setDuration] = useState("120");
  const active = [...Object.entries(snapshot?.faults ?? {}), ...Object.keys(snapshot?.scenarios ?? {}).map((k) => [k, "running"])];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {SCENARIOS.map(({ action, title, detail, Icon, extra }) => (
          <Card key={title} material="regular" className="flex flex-col p-5">
            <div className="material-thick mb-3 flex h-10 w-10 items-center justify-center rounded-[12px]">
              <Icon size={20} strokeWidth={1.75} className="text-text" />
            </div>
            <h3 className="text-t15 font-semibold text-text">{title}</h3>
            <p className="mb-4 mt-1 flex-1 text-t12 text-text-secondary">{detail}</p>
            <Button variant="destructive" size="sm" className="self-start" onClick={() => send(action, extra)}>
              Launch
            </Button>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card material="regular">
          <CardHeader>
            <div>
              <CardTitle>Custom sensor fault</CardTitle>
              <CardDescription>Corrupt what the AI and L1 see. The SIS reads its own channel.</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
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
            <Button variant="destructive" onClick={() => send("sensor_fault", { channel, mode, severity: Number(severity) || 0 })}>
              Inject fault
            </Button>
          </CardContent>
        </Card>

        <Card material="regular">
          <CardHeader>
            <div>
              <CardTitle>Stolen signing key</CardTitle>
              <CardDescription>The gate admits the command. Pick the valve lineup it forces.</CardDescription>
            </div>
            <KeyRound size={18} className="text-text-tertiary" />
          </CardHeader>
          <CardContent className="space-y-1">
            {(["f1", "f2", "purge", "product"] as const).map((key) => (
              <div key={key} className="flex items-center gap-3">
                <span className="w-16 text-t12 text-text-secondary">{key === "f1" ? "Feed 1" : key === "f2" ? "Feed 2" : key === "purge" ? "Purge" : "Product"}</span>
                <Slider min={0} max={100} step={5} value={[lineup[key]]} onValueChange={([v]) => setLineup((prev) => ({ ...prev, [key]: v }))} />
                <span className="tabular w-10 text-right text-t12 text-text">{lineup[key]}%</span>
              </div>
            ))}
            <div className="flex items-center gap-3 pt-3">
              <Input type="number" min={5} max={600} value={duration} onChange={(e) => setDuration(e.target.value)} className="w-28" aria-label="Duration in seconds" />
              <Button variant="destructive" onClick={() => send("stolen_key", { ...lineup, duration_s: Number(duration) || 120 })}>
                Take over
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card material="regular">
        <CardHeader>
          <div>
            <CardTitle>Recovery</CardTitle>
            <CardDescription>{active.length ? `Active: ${active.map(([k]) => k).join(", ")}` : "No active faults or attacks."}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => send("clear_faults")}>
            Clear faults and attacks
          </Button>
          <Button variant="tinted" onClick={() => send("reset_interlock")}>
            <ShieldCheck size={16} />
            Re-arm SIS
          </Button>
          <Button variant="tinted" onClick={() => send("reset_plant")}>
            <RotateCcw size={16} />
            Reset plant
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
