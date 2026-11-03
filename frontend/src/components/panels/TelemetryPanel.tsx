import { useMemo, useState } from "react";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { Segmented } from "@/components/ui/segmented";
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
    <div className="space-y-14">
      <div className="grid grid-cols-1 border-t border-rule sm:grid-cols-2">
        {([["Pressure", "kPa", stats.pressure, 0], ["Inventory", "%", stats.level, 1]] as const).map(([label, unit, st, digits], i) => (
          <div key={label} className={`py-6 ${i === 1 ? "border-t border-rule sm:border-l sm:border-t-0 sm:pl-6" : "sm:pr-6"}`}>
            <p className="eyebrow">{label} · retained window</p>
            <div className="tabular mt-4 flex flex-wrap gap-x-8 gap-y-2">
              {([["Min", st?.min], ["Avg", st?.avg], ["Max", st?.max]] as const).map(([k, v]) => (
                <div key={k}>
                  <p className="text-t11 uppercase tracking-[0.08em] text-text-3">{k}</p>
                  <p className="text-t28 text-text">{fmt(v, digits)}<span className="ml-1 text-t13 text-text-3">{unit}</span></p>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <Section
        title={`Telemetry · ${samples.length} samples`}
        aside={
          <div className="flex items-center gap-4">
            <Segmented label="View" value={view} onValueChange={setView} options={[{ value: "charts", label: "Charts" }, { value: "data", label: "Data" }]} />
            <Button variant="quiet" size="sm" onClick={exportCsv} disabled={!samples.length}>
              <Download size={14} />
              CSV
            </Button>
          </div>
        }
      >
        {view === "charts" ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-10 xl:grid-cols-2">
            <TrendChart samples={samples} metric="pressure" windowSeconds={0} limits={{ high: limits?.pressure_high }} showSetpoint height={260} />
            <TrendChart samples={samples} metric="level" windowSeconds={0} limits={{ high: limits?.level_high, low: limits?.level_low }} showSetpoint height={260} />
          </div>
        ) : (
          <div className="scroll-clean max-h-[520px] overflow-auto">
            <table className="tabular w-full text-left text-t12">
              <thead className="sticky top-0 bg-background text-text-3">
                <tr className="border-b border-rule">
                  {["Time", "Pressure", "Safety P", "Level", "Safety L", "Feed 1", "Feed 2", "Purge", "Product", "Trip"].map((h) => (
                    <th key={h} className="px-3 py-2.5 text-t11 font-medium uppercase tracking-[0.08em]">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {recent.map((s) => (
                  <tr key={s.seq} className="text-text">
                    <td className="px-3 py-2 text-text-3">{clock(s.t)}</td>
                    <td className="px-3 py-2">{fmt(s.pressure, 1)}</td>
                    <td className="px-3 py-2 text-text-3">{fmt(s.pressureTrue, 1)}</td>
                    <td className="px-3 py-2">{fmt(s.level, 2)}</td>
                    <td className="px-3 py-2 text-text-3">{fmt(s.levelTrue, 2)}</td>
                    <td className="px-3 py-2">{fmt(s.f1, 0)}</td>
                    <td className="px-3 py-2">{fmt(s.f2, 0)}</td>
                    <td className="px-3 py-2">{fmt(s.purge, 0)}</td>
                    <td className="px-3 py-2">{fmt(s.product, 0)}</td>
                    <td className={`px-3 py-2 ${s.tripped ? "text-bad" : "text-text-3"}`}>{s.tripped ? "TRIP" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}
