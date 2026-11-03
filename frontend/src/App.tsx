import { useEffect, useRef, useState } from "react";
import { AppShell, NAV, type Direction, type PageId } from "@/components/AppShell";
import { Reveal } from "@/components/Reveal";
import { AttacksPanel } from "@/components/panels/AttacksPanel";
import { EventLog } from "@/components/panels/EventLog";
import { MetricTiles } from "@/components/panels/MetricTiles";
import { OperatorPanel } from "@/components/panels/OperatorPanel";
import { PlantView } from "@/components/panels/PlantView";
import { ProcessChart } from "@/components/panels/ProcessChart";
import { ProtectionLayers } from "@/components/panels/ProtectionLayers";
import { StatusHero } from "@/components/panels/StatusHero";
import { TelemetryPanel } from "@/components/panels/TelemetryPanel";
import { ValvesCard } from "@/components/panels/ValvesCard";
import { useControlRoom } from "@/hooks/useControlRoom";
import { derivePlantStatus } from "@/lib/status";

function readHash(): PageId {
  const id = window.location.hash.replace("#", "");
  return NAV.some((item) => item.id === id) ? (id as PageId) : "overview";
}

const TEXT_INPUTS = new Set(["text", "number", "search", "email", "url", "password", "tel"]);

function isTyping(target: EventTarget | null) {
  const el = target as HTMLElement | null;
  if (!el) return false;
  if (el.tagName === "INPUT") return TEXT_INPUTS.has((el as HTMLInputElement).type);
  return el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
}

const order = (id: PageId) => NAV.findIndex((item) => item.id === id);

export default function App() {
  const room = useControlRoom();
  const [page, setPage] = useState<PageId>(readHash);
  const [direction, setDirection] = useState<Direction>("none");
  const pageRef = useRef(page);
  const overviewRef = useRef<HTMLDivElement>(null);
  pageRef.current = page;

  const go = (next: PageId, viaKeyboard: boolean) => {
    const from = pageRef.current;
    if (next === from) return;
    setDirection(viaKeyboard ? "none" : order(next) > order(from) ? "forward" : "back");
    window.location.hash = next;
    setPage(next);
    window.scrollTo({ top: 0 });
  };

  useEffect(() => {
    const onHash = () => setPage(readHash());
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || isTyping(event.target)) return;
      const item = NAV[Number(event.key) - 1];
      if (item) go(item.id, true);
    };
    window.addEventListener("hashchange", onHash);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  useEffect(() => {
    const el = overviewRef.current;
    if (page !== "overview" || direction === "none" || !el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const offset = direction === "forward" ? 10 : -10;
    el.animate(
      [{ opacity: 0, transform: `translateX(${offset}px)` }, { opacity: 1, transform: "translateX(0)" }],
      { duration: 200, easing: "cubic-bezier(0.23, 1, 0.32, 1)" }
    );
  }, [page, direction]);

  const status = derivePlantStatus(room.snapshot, room.connected);

  return (
    <AppShell
      page={page}
      direction={direction}
      onPage={(next) => go(next, false)}
      connected={room.connected}
      status={status}
      notice={room.notice}
      noticeKey={room.noticeKey}
      onDismissNotice={room.clearNotice}
      overview={
        <div ref={overviewRef} hidden={page !== "overview"} className="space-y-14">
          <Reveal index={0}><StatusHero room={room} /></Reveal>
          <Reveal index={1}><MetricTiles room={room} /></Reveal>
          <Reveal index={2}><ProcessChart room={room} /></Reveal>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-14 lg:grid-cols-2 lg:gap-16">
            <Reveal index={3}><ProtectionLayers room={room} /></Reveal>
            <Reveal index={4}><ValvesCard room={room} /></Reveal>
          </div>
          <Reveal index={5}><PlantView /></Reveal>
          <Reveal index={6}><EventLog room={room} /></Reveal>
        </div>
      }
    >
      {page === "operator" && <OperatorPanel room={room} />}
      {page === "attacks" && (
        <>
          <AttacksPanel room={room} />
          <EventLog room={room} limit={20} />
        </>
      )}
      {page === "telemetry" && <TelemetryPanel room={room} />}
    </AppShell>
  );
}
