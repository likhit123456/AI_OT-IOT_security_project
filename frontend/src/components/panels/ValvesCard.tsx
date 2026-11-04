import { Section } from "@/components/ui/section";
import type { ControlRoom } from "@/hooks/useControlRoom";
import { clamp, cn, fmt } from "@/lib/utils";

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
    <Section title="Actuators">
      <ul className="divide-y divide-rule border-y border-rule">
        {VALVES.map(({ key, label }) => {
          const pos = actual?.[key] ?? null;
          const cmd = commanded?.[key] ?? null;
          const gap = pos !== null && cmd !== null && Math.abs(pos - cmd) > 8;
          return (
            <li key={key} className="flex items-center gap-4 py-3.5">
              <span className="w-16 shrink-0 text-t13 text-text">{label}</span>
              <div className="relative h-[3px] flex-1 rounded-full bg-rule">
                <div className="absolute inset-y-0 left-0 w-full origin-left rounded-full bg-text" style={{ transform: `scaleX(${clamp(pos ?? 0, 0, 100) / 100})` }} />
                {cmd !== null && <i className="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-text-3" style={{ left: `${clamp(cmd, 0, 100)}%` }} />}
              </div>
              <span className={cn("tabular w-24 shrink-0 text-right text-t12", gap ? "text-warn" : "text-text-2")}>
                {fmt(pos, 0)}%{cmd !== null && <span className="text-text-3"> / {fmt(cmd, 0)}</span>}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="mt-4 text-t12 text-text-3">Position, then the last signed command. The tick marks the command.</p>
    </Section>
  );
}
