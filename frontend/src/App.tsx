import { useEffect, useState } from "react";
import { AppShell, NAV, type PageId } from "@/components/AppShell";
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

function readHash(): PageId {
  const id = window.location.hash.replace("#", "");
  return NAV.some((item) => item.id === id) ? (id as PageId) : "overview";
}

export default function App() {
  const room = useControlRoom();
  const [page, setPage] = useState<PageId>(readHash);

  useEffect(() => {
    const onHash = () => setPage(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const navigate = (next: PageId) => {
    window.location.hash = next;
    setPage(next);
    window.scrollTo({ top: 0 });
  };

  return (
    <AppShell
      page={page}
      onPage={navigate}
      connected={room.connected}
      stale={room.stale}
      notice={room.notice}
      onDismissNotice={room.clearNotice}
    >
      {page === "overview" && (
        <>
          <StatusHero room={room} />
          <MetricTiles room={room} />
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
            <ProcessChart room={room} />
            <ProtectionLayers room={room} />
          </div>
          <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
            <ValvesCard room={room} />
            <PlantView />
          </div>
          <EventLog room={room} />
        </>
      )}
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
