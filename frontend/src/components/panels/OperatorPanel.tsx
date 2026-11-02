import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
  }, [chat.length]);

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
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
      <Card material="regular" className="flex min-h-[560px] flex-col">
        <CardHeader>
          <div>
            <CardTitle>Talk to the AI operator</CardTitle>
            <CardDescription>
              {canChat ? "Setpoint requests go to the model; valve commands become a manual override." : "The baseline rule brain only takes direct valve commands."}
            </CardDescription>
          </div>
          <Badge tone={canChat ? "info" : "neutral"}>{brain?.label ?? "No operator"}</Badge>
        </CardHeader>
        <div ref={scroller} className="scroll-clean flex-1 space-y-3 overflow-y-auto px-5 py-3">
          {chat.length === 0 && <p className="py-10 text-center text-t13 text-text-tertiary">Send an instruction to begin.</p>}
          {chat.map((message) => {
            const mine = message.role === "you";
            return (
              <div key={message.id} className={cn("flex", mine ? "justify-end" : "justify-start")}>
                <div
                  className={cn(
                    "max-w-[82%] rounded-[18px] px-4 py-2.5 text-t13",
                    mine ? "bg-blue text-white" : "material-thick text-text"
                  )}
                >
                  {!mine && <p className="mb-0.5 text-t11 font-semibold uppercase tracking-wide text-text-tertiary">{message.role}</p>}
                  <p className="whitespace-pre-wrap break-words">{message.text}</p>
                </div>
              </div>
            );
          })}
        </div>
        <div className="px-5 pb-5 pt-2">
          <div className="scroll-clean mb-3 flex gap-2 overflow-x-auto pb-1">
            {SUGGESTIONS.map((item) => (
              <Button key={item.label} variant={item.risky ? "destructive" : "tinted"} size="sm" className="shrink-0" onClick={() => submit(item.text)}>
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
            <Button onClick={() => submit(draft)} disabled={!draft.trim()} aria-label="Send">
              <Send size={16} />
              <span className="hidden sm:inline">Send</span>
            </Button>
          </div>
        </div>
      </Card>

      <div className="space-y-4">
        <Card material="regular">
          <CardHeader>
            <div>
              <CardTitle>Operator brain</CardTitle>
              <CardDescription>Switch between the trained model and the rule baseline.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <Tabs value={brain?.kind ?? "baseline"} onValueChange={(kind) => send("brain", { kind })}>
              <TabsList className="w-full">
                <TabsTrigger value="ollama" className="flex-1">Trained model</TabsTrigger>
                <TabsTrigger value="baseline" className="flex-1">Baseline rules</TabsTrigger>
              </TabsList>
            </Tabs>
            <p className="tabular mt-3 text-t11 text-text-tertiary">
              {brain?.inflight ? "Waiting on the model…" : `${brain?.calls ?? 0} model calls`}
            </p>
          </CardContent>
        </Card>

        <Card material="regular">
          <CardHeader>
            <div>
              <CardTitle>AI authority</CardTitle>
              <CardDescription>How far the supervisor may move setpoints.</CardDescription>
            </div>
            <span className="tabular text-t22 font-semibold text-text">{fmt(authority * 100, 0)}%</span>
          </CardHeader>
          <CardContent>
            <Slider min={0} max={100} step={5} value={[Math.round(authority * 100)]} onValueChange={onAuthority} aria-label="AI authority" />
            <p className="tabular mt-2 text-t11 text-text-tertiary">
              Effective {fmt((brain?.effective ?? 0) * 100, 0)}% after Keel and governance limits.
            </p>
          </CardContent>
        </Card>

        {snapshot?.note && (
          <Card material="regular">
            <CardContent className="pt-4">
              <p className="text-t11 font-semibold uppercase tracking-wide text-text-tertiary">Standing instruction</p>
              <p className="mt-1 text-t13 text-text">{snapshot.note}</p>
              <Button variant="plain" size="sm" className="mt-2 -ml-3" onClick={() => send("clear_note")}>
                Clear
              </Button>
            </CardContent>
          </Card>
        )}

        {snapshot?.manual && Object.keys(snapshot.manual).length > 0 && (
          <Card material="regular">
            <CardContent className="pt-4">
              <p className="text-t11 font-semibold uppercase tracking-wide text-text-tertiary">Manual valves</p>
              <p className="tabular mt-1 text-t13 text-text">
                {Object.entries(snapshot.manual).map(([k, v]) => `${k} ${String(v)}`).join(" · ")}
              </p>
              <Button variant="plain" size="sm" className="mt-2 -ml-3" onClick={() => send("chat", { text: "release" })}>
                Release
              </Button>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
