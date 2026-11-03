import { toneDot, toneText, type Tone } from "@/lib/status";
import { cn } from "@/lib/utils";

interface StatusTextProps {
  tone: Tone;
  children: React.ReactNode;
  className?: string;
}

export function StatusText({ tone, children, className }: StatusTextProps) {
  const alert = tone === "warn" || tone === "bad";
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-2 text-t13", toneText[tone], className)}>
      <span className="relative flex h-1.5 w-1.5 shrink-0">
        <i className={cn("h-1.5 w-1.5 rounded-full transition-colors duration-ui ease-out", toneDot[tone])} />
        {alert && <i key={tone} className={cn("ping absolute inset-0 rounded-full", toneDot[tone])} />}
      </span>
      <span className="truncate">{children}</span>
    </span>
  );
}
