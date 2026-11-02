import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { ControlRoom } from "@/hooks/useControlRoom";
import { clamp, fmt } from "@/lib/utils";

const VALVES = [
  { key: "f1", label: "Feed 1" },
  { key: "f2", label: "Feed 2" },
  { key: "purge", label: "Purge" },
  { key: "product", label: "Product" }
] as const;

export function ValvesCard({ room }: { room: ControlRoom }) {
  const actual = room.snapshot?.valves;
  const commanded = room.snapshot?.commanded;

  return (
    <Card material="regular">
      <CardHeader>
        <div>
          <CardTitle>Actuators</CardTitle>
          <CardDescription>Position against the last signed command.</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 pt-1">
        {VALVES.map(({ key, label }) => {
          const pos = actual?.[key] ?? null;
          const cmd = commanded?.[key] ?? null;
          const gap = pos !== null && cmd !== null && Math.abs(pos - cmd) > 8;
          return (
            <div key={key}>
              <div className="mb-1.5 flex items-baseline justify-between">
                <span className="text-t13 font-medium text-text">{label}</span>
                <span className="tabular text-t12 text-text-secondary">
                  {fmt(pos, 0)}%{cmd !== null && <span className={gap ? "text-orange" : "text-text-tertiary"}> · cmd {fmt(cmd, 0)}%</span>}
                </span>
              </div>
              <div className="relative h-2 rounded-full bg-[var(--material-fill)]">
                <div
                  className="h-full rounded-full transition-[width] duration-500"
                  style={{
                    width: `${clamp(pos ?? 0, 0, 100)}%`,
                    background: "linear-gradient(90deg, rgba(10,132,255,0.55), var(--system-blue))"
                  }}
                />
                {cmd !== null && (
                  <i
                    className="absolute top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-text"
                    style={{ left: `${clamp(cmd, 0, 100)}%` }}
                  />
                )}
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
