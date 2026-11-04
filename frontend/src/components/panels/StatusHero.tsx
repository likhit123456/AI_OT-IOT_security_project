import { useRef } from "react";
import { Button } from "@/components/ui/button";
import { isBooted } from "@/components/Reveal";
import type { ControlRoom } from "@/hooks/useControlRoom";
import { derivePlantStatus, toneText, type Tone } from "@/lib/status";
import { clock, cn, fmt } from "@/lib/utils";

const GLOW: Record<Tone, string> = {
  good: "var(--glow-good)",
  warn: "var(--glow-warn)",
  bad: "var(--glow-bad)",
  info: "var(--glow-quiet)",
  neutral: "var(--glow-quiet)"
};

const TONES: Tone[] = ["good", "warn", "bad", "info", "neutral"];

export function StatusHero({ room }: { room: ControlRoom }) {
  const { snapshot, connected, send } = room;
  const status = derivePlantStatus(snapshot, connected);
  const sis = snapshot?.sis;
  const wasBooted = useRef(isBooted());
  const introTitle = useRef<string | null>(status.title);
  if (introTitle.current !== null && introTitle.current !== status.title) introTitle.current = null;
  const intro = introTitle.current !== null && !wasBooted.current;

  return (
    <section className="relative isolate">
      {TONES.map((tone) => (
        <div
          key={tone}
          aria-hidden
          className="pointer-events-none absolute -left-24 -top-32 -z-10 h-[460px] w-[760px] max-w-[140vw] transition-opacity duration-move ease-in-out"
          style={{ opacity: status.tone === tone ? 1 : 0, background: `radial-gradient(closest-side, ${GLOW[tone]}, transparent)` }}
        />
      ))}
      <p className="eyebrow flex flex-wrap items-center gap-x-3 gap-y-1">
        <span>Plant status</span>
        <span className="tabular">T+ {clock(snapshot?.t ?? 0)}</span>
        <span className="normal-case tracking-normal">{snapshot?.brain?.label ?? "No operator"}</span>
        <span className="tabular">Authority {fmt((snapshot?.brain?.effective ?? 0) * 100, 0)}%</span>
      </p>
      <h1 className="mt-6 text-t56 md:text-t72">
        <span className="mask-line">
          <span key={status.title} className={cn(intro ? "mask-rise" : "swap-in", "block transition-colors duration-move ease-in-out", toneText[status.tone])}>
            {status.title}
          </span>
        </span>
      </h1>
      <p className="mt-6 max-w-2xl text-t17 text-text-2">{status.detail}</p>
      {sis?.tripped && (
        <div className="mt-8 flex flex-wrap items-center gap-4">
          <Button disabled={!sis.can_reset} onClick={() => send("reset_interlock")}>
            Re-arm SIS
          </Button>
          {sis.auto_rearm_in !== null && (
            <span className="tabular text-t13 text-text-2">{sis.lockout ? "Auto re-arm locked out" : `Auto re-arm in ${fmt(sis.auto_rearm_in, 0)} s`}</span>
          )}
        </div>
      )}
    </section>
  );
}
