import { useRef } from "react";
import { cn } from "@/lib/utils";

let booted = false;
if (typeof window !== "undefined") {
  window.setTimeout(() => {
    booted = true;
  }, 1100);
}

export function isBooted() {
  return booted;
}

interface RevealProps {
  index?: number;
  className?: string;
  children: React.ReactNode;
}

export function Reveal({ index = 0, className, children }: RevealProps) {
  const animate = useRef(!booted).current;
  return (
    <div className={cn(animate && "reveal", className)} style={animate ? ({ "--i": index } as React.CSSProperties) : undefined}>
      {children}
    </div>
  );
}
