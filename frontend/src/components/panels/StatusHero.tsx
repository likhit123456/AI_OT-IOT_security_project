import { ShieldAlert, ShieldCheck, ShieldQuestion, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { ControlRoom } from "@/hooks/useControlRoom";
import { derivePlantStatus, toneText, toneTint } from "@/lib/status";
import { clock, fmt } from "@/lib/utils";

export function StatusHero({ room }: { room: ControlRoom }) {
  const { snapshot, connected, send } = room;
  const status = derivePlantStatus(snapshot, connected);
  const sis = snapshot?.sis;
  const Icon = status.tone === "good" ? ShieldCheck : status.tone === "neutral" ? ShieldQuestion : status.tone === "warn" ? Zap : ShieldAlert;

  return (
    <Card material="regular" className="relative overflow-hidden">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{ background: `radial-gradient(120% 140% at 0% 0%, ${toneTint[status.tone]} 0%, transparent 60%)` }}
      />
      <div className="relative flex flex-col gap-5 p-5 md:flex-row md:items-center md:justify-between md:p-6">
        <div className="flex items-start gap-4">
          <div className="material-thick flex h-14 w-14 shrink-0 items-center justify-center rounded-[16px]">
            <Icon className={toneText[status.tone]} size={28} strokeWidth={1.75} />
          </div>
          <div className="min-w-0">
            <p className="text-t12 font-medium uppercase tracking-wide text-text-tertiary">Plant status</p>
            <h2 className={`text-t34 font-semibold ${toneText[status.tone]}`}>{status.title}</h2>
            <p className="mt-1 max-w-xl text-t13 text-text-secondary">{status.detail}</p>
          </div>
        </div>
        <div className="flex flex-col items-start gap-3 md:items-end">
          <div className="flex flex-wrap gap-2">
            <Badge tone="info">{snapshot?.brain?.label ?? "No operator"}</Badge>
            <Badge tone="neutral" className="tabular">Authority {fmt((snapshot?.brain?.effective ?? 0) * 100, 0)}%</Badge>
            <Badge tone="neutral" className="tabular">T+ {clock(snapshot?.t ?? 0)}</Badge>
          </div>
          {sis?.tripped && (
            <div className="flex items-center gap-3">
              {sis.auto_rearm_in !== null && (
                <span className="tabular text-t12 text-text-secondary">
                  {sis.lockout ? "Auto re-arm locked out" : `Auto re-arm in ${fmt(sis.auto_rearm_in, 0)} s`}
                </span>
              )}
              <Button variant="primary" size="sm" disabled={!sis.can_reset} onClick={() => send("reset_interlock")}>
                Re-arm SIS
              </Button>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
