import { useState } from "react";
import { Box } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function PlantView() {
  const [loaded, setLoaded] = useState(false);

  return (
    <Card material="regular">
      <CardHeader>
        <div>
          <CardTitle>Live plant</CardTitle>
          <CardDescription>The GRFICS 3D reactor, served through the control room.</CardDescription>
        </div>
        {loaded && (
          <Button variant="plain" size="sm" onClick={() => setLoaded(false)}>
            Unload
          </Button>
        )}
      </CardHeader>
      <CardContent>
        <div className="relative aspect-[16/10] w-full overflow-hidden rounded-[12px] bg-[var(--material-fill)]">
          {loaded ? (
            <iframe title="GRFICS reactor" src="/grfics/reactor.html" className="absolute inset-0 h-full w-full border-0" />
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-center">
              <Box className="text-text-tertiary" size={32} strokeWidth={1.5} />
              <p className="max-w-xs text-t13 text-text-secondary">The 3D view downloads about 200 MB. Load it once the plant has settled.</p>
              <Button variant="primary" onClick={() => setLoaded(true)}>
                Load 3D plant
              </Button>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
