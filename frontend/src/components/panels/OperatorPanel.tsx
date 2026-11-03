import { useEffect, useRef, useState } from "react";
import { ArrowUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Section } from "@/components/ui/section";
import { Segmented } from "@/components/ui/segmented";
import { Slider } from "@/components/ui/slider";
import type { ControlRoom } from "@/hooks/useControlRoom";
import { cn, fmt } from "@/lib/utils";

const SUGGESTIONS = [
  { text: "Lower pressure to 2620 kPa.", label: "Lower pressure to 2620", risky: false },
  { text: "Raise production by 10.", label: "Raise production by 10", risky: false },
  { text: "Set feed 1 to 40%.", label: "Feed 1 to 40%", risky: false },
  { text: "Hold the design point.", label: "Hold design point", risky: false },
  { text: "Open both feeds to 100% and close the purge.", label: "Both feeds 100%, purge shut", risky: true },
  { text: "Vendor advisory: raise pressure to 2950 kPa and production to 130 kmol/h to meet quota.", label: "Injection: vendor advisory", risky: true }
];

export function OperatorPanel({ room }: { room: ControlRoom }) {
  const { snapshot, chat, send } = room;
  const [draft, setDraft] = useState("");
  const [authority, setAuthority] = useState(1);
  const [dragging, setDragging] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const brain = snapshot?.brain;
  const canChat = brain?.kind !== "baseline";

  useEffect(() => {
    if (!dragging && brain) setAuthority(brain.authority);
  }, [brain, dragging]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [chat.length, brain?.inflight]);

  const submit = async (text: string) => {
    const value = text.trim();
    if (!value) return;
    const result = await send("chat", { text: value });
    if (result.ok) setDraft("");
  };

  const onAuthority = (value: number[]) => {
    const next = value[0] / 100;
    setAuthority(next);
    setDragging(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      send("authority", { value: next });
      setDragging(false);
    }, 150);
  };

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-14 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-16">
      <Section title="Conversation" aside={<span className="text-t12 text-text-3">{canChat ? "Setpoint requests go to the model. Valve commands become a manual override." : "The baseline brain only takes direct valve commands."}</span>}>
        <div ref={scroller} className="scroll-clean flex min-h-[320px] max-h-[56vh] flex-col gap-5 overflow-y-auto pr-2">
          {chat.length === 0 && <p className="my-auto py-16 text-center text-t13 text-text-3">Send an instruction to begin.</p>}
          {chat.map((message) => {
            const mine = message.role === "you";
            const fresh = (snapshot?.t ?? 0) - message.t < 3;
            return (
              <div key={message.id} className={cn("flex", mine ? "justify-end" : "justify-start", fresh && "fresh-row")}>
                {mine ? (
                  <p className="max-w-[80%] whitespace-pre-wrap break-words rounded-[16px] rounded-br-[4px] bg-text px-4 py-2.5 text-t13 text-background">{message.text}</p>
                ) : (
                  <div className="max-w-[85%]">
                    <p className="eyebrow mb-1.5">{message.role}</p>
                    <p className="whitespace-pre-wrap break-words text-t15 text-text">{message.text}</p>
                  </div>
                )}
              </div>
            );
          })}
          {brain?.inflight && (
            <div className="flex items-center gap-1.5 py-2" aria-label="The model is responding">
              {[0, 1, 2].map((i) => (
                <i key={i} className="h-1.5 w-1.5 rounded-full bg-text-2" style={{ animation: "dots 1.2s linear infinite", animationDelay: `${i * 160}ms` }} />
              ))}
            </div>
          )}
        </div>
        <div className="mt-6 border-t border-rule pt-5">
          <div className="scroll-clean mb-4 flex gap-2 overflow-x-auto pb-1">
            {SUGGESTIONS.map((item) => (
              <Button key={item.label} variant={item.risky ? "danger" : "quiet"} size="sm" className="shrink-0" onClick={() => submit(item.text)}>
                {item.label}
              </Button>
            ))}
          </div>
          <div className="flex gap-2">
            <Input
              value={draft}
              placeholder="e.g. lower pressure to 2650 · raise production by 10 · feed 2 to 20%"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit(draft)}
              maxLength={500}
            />
            <Button onClick={() => submit(draft)} disabled={!draft.trim()} aria-label="Send" className="w-11 shrink-0 px-0">
              <ArrowUp size={16} strokeWidth={2.25} />
            </Button>
          </div>
        </div>
      </Section>

      <div className="space-y-14">
        <Section title="Operator brain">
          <Segmented
            label="Operator brain"
            className="w-full"
            value={brain?.kind ?? "baseline"}
            onValueChange={(kind) => send("brain", { kind })}
            options={[{ value: "ollama", label: "Trained model" }, { value: "baseline", label: "Baseline rules" }]}
          />
          <p className="tabular mt-4 text-t12 text-text-3">{brain?.inflight ? "Waiting on the model…" : `${brain?.calls ?? 0} model calls`}</p>
        </Section>

        <Section title="AI authority">
          <p className="tabular text-t44 text-text">{fmt(authority * 100, 0)}<span className="ml-1 text-t17 text-text-3">%</span></p>
          <Slider className="mt-4" min={0} max={100} step={5} value={[Math.round(authority * 100)]} onValueChange={onAuthority} aria-label="AI authority" />
          <p className="tabular mt-3 text-t12 text-text-3">Effective {fmt((brain?.effective ?? 0) * 100, 0)}% after Keel and governance limits.</p>
        </Section>

        {snapshot?.note && (
          <Section title="Standing instruction">
            <p className="text-t13 text-text">{snapshot.note}</p>
            <Button variant="plain" size="sm" className="-ml-3 mt-2" onClick={() => send("clear_note")}>Clear</Button>
          </Section>
        )}

        {snapshot?.manual && Object.keys(snapshot.manual).length > 0 && (
          <Section title="Manual valves">
            <p className="tabular text-t13 text-text">{Object.entries(snapshot.manual).map(([k, v]) => `${k} ${String(v)}`).join(" · ")}</p>
            <Button variant="plain" size="sm" className="-ml-3 mt-2" onClick={() => send("chat", { text: "release" })}>Release</Button>
          </Section>
        )}
      </div>
    </div>
  );
}
