import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { useSpotlight } from "@/hooks/useSpotlight";

export function PlantView() {
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState("Loading 3D plant…");
  const [attempt, setAttempt] = useState(0);
  const iframe = useRef<HTMLIFrameElement>(null);
  const frame = useSpotlight<HTMLDivElement>();
  useEffect(() => {
    if (!loaded) return;
    setStatus("Loading 3D plant…");
    const timer = window.setTimeout(() => setStatus("3D startup timed out. Check the simulator and retry."), 125000);
    const receive = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== iframe.current?.contentWindow
          || event.data?.type !== "scram-unity") return;
      if (event.data.status === "ready") {
        window.clearTimeout(timer);
        setStatus("3D runtime started");
      } else if (event.data.status === "error") {
        window.clearTimeout(timer);
        setStatus(typeof event.data.detail === "string" ? event.data.detail : "3D startup failed. Retry the view.");
      }
    };
    window.addEventListener("message", receive);
    return () => { window.clearTimeout(timer); window.removeEventListener("message", receive); };
  }, [loaded, attempt]);

  return (
    <Section
      title="Live plant"
      aside={
        loaded ? (
          <Button variant="plain" size="sm" onClick={() => setLoaded(false)}>
            Unload
          </Button>
        ) : undefined
      }
    >
      <div ref={frame} className="glass spot relative aspect-[16/9] w-full overflow-hidden rounded-[20px]">
        {loaded ? (
          <iframe
            key={attempt}
            ref={iframe}
            title="GRFICS reactor"
            src="/grfics/reactor.html"
            onError={() => setStatus("3D document failed to load. Retry the view.")}
            className="absolute inset-0 h-full w-full border-0"
          />
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 px-6 text-center">
            <p className="max-w-sm text-t13 text-text-2">The 3D view downloads about 200 MB. Load it once the plant has settled.</p>
            <Button onClick={() => setLoaded(true)}>Load 3D plant</Button>
          </div>
        )}
      </div>
      {loaded && <div className="mt-2 flex items-center justify-between gap-3">
        <p role="status" className="text-t12 text-text-2">{status}</p>
        <Button variant="plain" size="sm" onClick={() => setAttempt(n => n + 1)}>Retry 3D view</Button>
      </div>}
    </Section>
  );
}
