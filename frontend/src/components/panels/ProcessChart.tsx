import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
  const limits = room.snapshot?.sis?.limits;
  const chartLimits =
    metric === "pressure" ? { high: limits?.pressure_high } : { high: limits?.level_high, low: limits?.level_low };

  return (
    <Card material="regular">
      <CardHeader className="flex-wrap">
        <div>
          <CardTitle>Process trend</CardTitle>
          <CardDescription>Measured signal, independent safety channel and protective limits.</CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Tabs value={metric} onValueChange={(v) => setMetric(v as Metric)}>
            <TabsList>
              <TabsTrigger value="pressure">Pressure</TabsTrigger>
              <TabsTrigger value="level">Level</TabsTrigger>
            </TabsList>
          </Tabs>
          <Tabs value={windowValue} onValueChange={setWindowValue}>
            <TabsList>
              {WINDOWS.map((w) => (
                <TabsTrigger key={w.value} value={w.value}>
                  {w.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
      </CardHeader>
      <CardContent>
        <TrendChart samples={room.samples} metric={metric} windowSeconds={Number(windowValue)} limits={chartLimits} showSetpoint={setpoint} />
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-t12 text-text-secondary">
          <span className="flex items-center gap-2"><i className="h-0.5 w-4 rounded-full bg-blue" />Measured</span>
          <span className="flex items-center gap-2"><i className="h-0.5 w-4 rounded-full bg-orange" />True (safety channel)</span>
          <span className="flex items-center gap-2"><i className="h-0.5 w-4 rounded-full bg-red/70" />Trip limit</span>
          <label className="ml-auto flex cursor-pointer items-center gap-2">
            <input type="checkbox" checked={setpoint} onChange={(e) => setSetpoint(e.target.checked)} className="h-4 w-4 accent-[var(--system-blue)]" />
            Setpoint
          </label>
        </div>
      </CardContent>
    </Card>
  );
}
