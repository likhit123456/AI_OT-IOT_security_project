import * as React from "react";
import { cn } from "@/lib/utils";

interface HoldButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "onClick"> {
  onConfirm: () => void;
  holdMs?: number;
}

export function HoldButton({ onConfirm, holdMs = 1200, className, children, disabled, ...props }: HoldButtonProps) {
  const timer = React.useRef<ReturnType<typeof setTimeout>>();
  const [holding, setHolding] = React.useState(false);

  const start = () => {
    if (disabled) return;
    setHolding(true);
    timer.current = setTimeout(() => {
      setHolding(false);
      onConfirm();
    }, holdMs);
  };

  const cancel = () => {
    clearTimeout(timer.current);
    setHolding(false);
  };

  React.useEffect(() => () => clearTimeout(timer.current), []);

  return (
    <button
      type="button"
      disabled={disabled}
      onPointerDown={start}
      onPointerUp={cancel}
      onPointerLeave={cancel}
      onPointerCancel={cancel}
      onKeyDown={(e) => (e.key === " " || e.key === "Enter") && !e.repeat && start()}
      onKeyUp={cancel}
      onBlur={cancel}
      className={cn(
        "press relative inline-flex min-h-[34px] select-none items-center justify-center gap-2 overflow-hidden rounded-[10px] bg-bad/[0.06] px-4 py-2 text-t13 font-medium text-bad ring-1 ring-inset ring-bad/35 backdrop-blur-md transition-transform duration-press ease-out active:scale-[0.97] disabled:pointer-events-none disabled:opacity-35 [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:text-t17",
        className
      )}
      {...props}
    >
      <span
        aria-hidden
        className="hold-fill absolute inset-0 bg-bad/20"
        style={{
          clipPath: holding ? "inset(0 0 0 0)" : "inset(0 100% 0 0)",
          transition: holding ? `clip-path ${holdMs}ms linear` : "clip-path 200ms var(--ease-out)"
        }}
      />
      <span className="relative flex items-center gap-2">{children}</span>
    </button>
  );
}
