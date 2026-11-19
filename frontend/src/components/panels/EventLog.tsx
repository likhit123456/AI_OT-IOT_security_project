import { Section } from "@/components/ui/section";
import type { ControlRoom } from "@/hooks/useControlRoom";
import { clock, cn } from "@/lib/utils";

const KIND_COLOR: Record<string, string> = {
  attack: "text-bad",
  trip: "text-bad",
  error: "text-bad",
  gate: "text-warn",
  keel: "text-warn",
  monitor: "text-warn"
};

export function EventLog({ room, limit = 40 }: { room: ControlRoom; limit?: number }) {
  const items = [...room.events].reverse().slice(0, limit);
  const now = room.snapshot?.t ?? 0;

  return (
    <Section title="Event log">
      <ul className="scroll-clean max-h-[440px] divide-y divide-rule overflow-y-auto border-y border-rule">
        {items.length === 0 && <li className="py-10 text-center text-t13 text-text-3">No events yet.</li>}
        {items.map((event) => (
          <li key={event.id} className={cn("flex gap-4 py-3", now - event.t < 3 && "fresh-row")}>
            <span className="tabular w-12 shrink-0 font-mono text-t12 text-text-3">{clock(event.t)}</span>
            <span className={cn("w-20 shrink-0 text-t11 font-medium uppercase tracking-[0.08em]", KIND_COLOR[event.kind] ?? "text-text-3")}>{event.kind}</span>
            <span className="min-w-0 flex-1 break-words text-t13 text-text">{event.msg}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
}
