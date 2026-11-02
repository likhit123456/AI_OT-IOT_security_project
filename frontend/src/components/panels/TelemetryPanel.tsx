import { useMemo, useState } from "react";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TrendChart } from "@/components/TrendChart";
import type { ControlRoom } from "@/hooks/useControlRoom";
import { clock, fmt } from "@/lib/utils";

const HEADERS = ["t", "pressure", "pressure_true", "level", "level_true", "f1", "f2", "purge", "product", "pressure_sp", "level_sp", "tripped", "authority"];

export function TelemetryPanel({ room }: { room: ControlRoom }) {
  const [view, setView] = useState("charts");
  const samples = room.samples;
  const limits = room.snapshot?.sis?.limits;

  const stats = useMemo(() => {
    const summarize = (pick: (s: (typeof samples)[number]) => number | null) => {
      const v = samples.map(pick).filter((n): n is number => n !== null && Number.isFinite(n));
      if (!v.length) return null;
      return { min: Math.min(...v), max: Math.max(...v), avg: v.reduce((a, b) => a + b, 0) / v.length };
    };
    return { pressure: summarize((s) => s.pressure), level: summarize((s) => s.level) };
  }, [samples]);

  const exportCsv = () => {
    const rows = samples.map((s) =>
      [s.t, s.pressure, s.pressureTrue, s.level, s.levelTrue, s.f1, s.f2, s.purge, s.product, s.pressureSp, s.levelSp, s.tripped ? 1 : 0, s.authority]
        .map((v) => (v === null ? "" : v))
        .join(",")
    );
    const blob = new Blob([[HEADERS.join(","), ...rows].join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "scram-telemetry.csv";
    link.click();
    URL.revokeObjectURL(url);
  };

  const recent = [...samples].reverse().slice(0, 120);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        {([["Pressure", "kPa", stats.pressure, 0], ["Inventory", "%", stats.level, 1]] as const).map(([label, unit, s, digits]) => (
          <Card key={label} material="regular" className="p-4">
            <p className="text-t12 font-medium text-text-secondary">{label} over retained window</p>
            <div className="tabular mt-2 flex gap-6 text-t15 text-text">
              <span><span className="text-t11 text-text-tertiary">MIN </span>{fmt(s?.min, digits)}</span>
              <span><span className="text-t11 text-text-tertiary">AVG </span>{fmt(s?.avg, digits)}</span>
              <span><span className="text-t11 text-text-tertiary">MAX </span>{fmt(s?.max, digits)}</span>
              <span className="text-t12 text-text-tertiary">{unit}</span>
            </div>
          </Card>
        ))}
      </div>

      <Card material="regular">
        <CardHeader className="flex-wrap">
          <div>
            <CardTitle>Telemetry</CardTitle>
            <CardDescription>{samples.length} retained samples. Viewing and export make no plant writes.</CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Tabs value={view} onValueChange={setView}>
              <TabsList>
                <TabsTrigger value="charts">Charts</TabsTrigger>
                <TabsTrigger value="data">Data</TabsTrigger>
              </TabsList>
            </Tabs>
            <Button variant="bordered" size="sm" onClick={exportCsv} disabled={!samples.length}>
              <Download size={14} />
              CSV
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {view === "charts" ? (
            <div className="grid gap-6 xl:grid-cols-2">
              <TrendChart samples={samples} metric="pressure" windowSeconds={0} limits={{ high: limits?.pressure_high }} showSetpoint height={240} />
              <TrendChart samples={samples} metric="level" windowSeconds={0} limits={{ high: limits?.level_high, low: limits?.level_low }} showSetpoint height={240} />
            </div>
          ) : (
            <div className="scroll-clean max-h-[520px] overflow-auto">
              <table className="tabular w-full text-left text-t12">
                <thead className="material-thick sticky top-0 text-text-tertiary">
                  <tr>
                    {["Time", "Pressure", "True P", "Level", "True L", "Feed 1", "Feed 2", "Purge", "Product", "Trip"].map((h) => (
                      <th key={h} className="px-3 py-2 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {recent.map((s) => (
                    <tr key={s.seq} className="grouped-row text-text">
                      <td className="px-3 py-1.5 text-text-secondary">{clock(s.t)}</td>
                      <td className="px-3 py-1.5">{fmt(s.pressure, 1)}</td>
                      <td className="px-3 py-1.5 text-text-secondary">{fmt(s.pressureTrue, 1)}</td>
                      <td className="px-3 py-1.5">{fmt(s.level, 2)}</td>
                      <td className="px-3 py-1.5 text-text-secondary">{fmt(s.levelTrue, 2)}</td>
                      <td className="px-3 py-1.5">{fmt(s.f1, 0)}</td>
                      <td className="px-3 py-1.5">{fmt(s.f2, 0)}</td>
                      <td className="px-3 py-1.5">{fmt(s.purge, 0)}</td>
                      <td className="px-3 py-1.5">{fmt(s.product, 0)}</td>
                      <td className={`px-3 py-1.5 ${s.tripped ? "text-red" : "text-text-tertiary"}`}>{s.tripped ? "TRIP" : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
