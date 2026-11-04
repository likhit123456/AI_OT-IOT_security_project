import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { useSpotlight } from "@/hooks/useSpotlight";
import { cn } from "@/lib/utils";

export function PlantView() {
  const [loaded, setLoaded] = useState(false);
  const [ready, setReady] = useState(false);
  const frame = useSpotlight<HTMLDivElement>();

  return (
    <Section
      title="Live plant"
      aside={
        loaded ? (
          <Button variant="plain" size="sm" onClick={() => { setLoaded(false); setReady(false); }}>
            Unload
          </Button>
        ) : undefined
      }
    >
      <div ref={frame} className="glass spot relative aspect-[16/9] w-full overflow-hidden rounded-[20px]">
        {loaded ? (
          <iframe
            title="GRFICS reactor"
            src="/grfics/reactor.html"
            onLoad={() => setReady(true)}
            className={cn("absolute inset-0 h-full w-full border-0 transition-opacity duration-move ease-out", ready ? "opacity-100" : "opacity-0")}
          />
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 px-6 text-center">
            <p className="max-w-sm text-t13 text-text-2">The 3D view downloads about 200 MB. Load it once the plant has settled.</p>
            <Button onClick={() => setLoaded(true)}>Load 3D plant</Button>
          </div>
        )}
      </div>
    </Section>
  );
}
