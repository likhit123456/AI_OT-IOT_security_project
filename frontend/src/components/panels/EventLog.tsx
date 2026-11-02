import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { ControlRoom } from "@/hooks/useControlRoom";
import type { Tone } from "@/lib/status";
import { clock } from "@/lib/utils";

const KIND_TONE: Record<string, Tone> = {
  attack: "bad",
  trip: "bad",
  error: "bad",
  gate: "warn",
  keel: "warn",
  monitor: "warn",
  governance: "info",
  brain: "info",
  plant: "good"
};

export function EventLog({ room, limit = 40, className = "" }: { room: ControlRoom; limit?: number; className?: string }) {
  const items = [...room.events].reverse().slice(0, limit);

  return (
    <Card material="regular" className={className}>
      <CardHeader>
        <div>
          <CardTitle>Event log</CardTitle>
          <CardDescription>Newest first. Attacks, gate decisions, monitor flags and trips.</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="scroll-clean max-h-[420px] overflow-y-auto pt-0">
        {items.length === 0 && <p className="py-6 text-center text-t13 text-text-tertiary">No events yet.</p>}
        {items.map((event) => (
          <div key={event.id} className="grouped-row flex gap-3 py-2.5">
            <span className="tabular w-11 shrink-0 pt-0.5 text-t11 text-text-tertiary">{clock(event.t)}</span>
            <div className="min-w-0 flex-1">
              <Badge tone={KIND_TONE[event.kind] ?? "neutral"} className="mb-1">{event.kind}</Badge>
              <p className="break-words text-t13 text-text">{event.msg}</p>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
