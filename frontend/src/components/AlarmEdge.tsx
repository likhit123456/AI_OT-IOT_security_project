import type { Tone } from "@/lib/status";
import { cn } from "@/lib/utils";

function Bar({ active, className }: { active: boolean; className: string }) {
  return (
    <div
      aria-hidden
      className={cn("absolute inset-x-0 top-0 h-[2px] origin-left", className)}
      style={{ transform: `scaleX(${active ? 1 : 0})`, transition: `transform ${active ? "300ms" : "200ms"} var(--ease-out)` }}
    />
  );
}

export function AlarmEdge({ tone }: { tone: Tone }) {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-x-0 top-0 z-50 h-[2px]">
      <Bar active={tone === "warn"} className="bg-warn" />
      <Bar active={tone === "bad"} className="bg-bad" />
    </div>
  );
}
