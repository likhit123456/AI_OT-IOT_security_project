import { useState } from "react";
import { Section } from "@/components/ui/section";
import { Segmented } from "@/components/ui/segmented";
import { TrendChart, type Metric } from "@/components/TrendChart";
import type { ControlRoom } from "@/hooks/useControlRoom";

const WINDOWS = [
  { label: "1 min", value: "60" },
  { label: "5 min", value: "300" },
  { label: "15 min", value: "900" },
  { label: "All", value: "0" }
];

export function ProcessChart({ room }: { room: ControlRoom }) {
  const [metric, setMetric] = useState<Metric>("pressure");
  const [windowValue, setWindowValue] = useState("300");
  const [setpoint, setSetpoint] = useState(true);
  const [scale, setScale] = useState<"fit" | "limits">("fit");
  const limits = room.snapshot?.sis?.limits;
  const chartLimits = metric === "pressure" ? { high: limits?.pressure_high } : { high: limits?.level_high, low: limits?.level_low };

  return (
    <Section
      title="Process trend"
      aside={
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <Segmented label="Metric" value={metric} onValueChange={(v) => setMetric(v as Metric)} options={[{ value: "pressure", label: "Pressure" }, { value: "level", label: "Level" }]} />
          <Segmented label="Scale" value={scale} onValueChange={(v) => setScale(v as "fit" | "limits")} options={[{ value: "fit", label: "Zoom" }, { value: "limits", label: "Limits" }]} />
          <Segmented label="Window" value={windowValue} onValueChange={setWindowValue} options={WINDOWS} />
        </div>
      }
    >
      <TrendChart samples={room.samples} metric={metric} windowSeconds={Number(windowValue)} limits={chartLimits} showSetpoint={setpoint} scale={scale} height={320} />
      <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-t12 text-text-3">
        <span className="flex items-center gap-2"><i className="h-0.5 w-5 rounded-full bg-text" />Measured</span>
        <span className="flex items-center gap-2"><i className="h-0 w-5 border-t border-dashed border-text-3" />Safety channel</span>
        <span className="flex items-center gap-2"><i className="h-0 w-5 border-t border-dotted border-bad" />Trip limit</span>
        <span className="flex items-center gap-2"><i className="h-3 w-5 rounded-[2px] bg-bad/20" />Tripped</span>
        <label className="ml-auto flex cursor-pointer items-center gap-2 text-text-2">
          <input type="checkbox" checked={setpoint} onChange={(e) => setSetpoint(e.target.checked)} className="h-3.5 w-3.5 accent-[var(--text)]" />
          Setpoint
        </label>
      </div>
    </Section>
  );
}
