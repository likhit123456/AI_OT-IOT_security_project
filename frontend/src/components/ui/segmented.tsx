import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

interface SegmentedOption {
  value: string;
  label: string;
}

interface SegmentedProps {
  value: string;
  onValueChange: (value: string) => void;
  options: SegmentedOption[];
  className?: string;
  label?: string;
}

export function Segmented({ value, onValueChange, options, className, label }: SegmentedProps) {
  const index = Math.max(0, options.findIndex((o) => o.value === value));

  return (
    <TabsPrimitive.Root value={value} onValueChange={onValueChange}>
      <TabsPrimitive.List
        aria-label={label}
        className={cn("capsule relative inline-grid rounded-full p-[3px]", className)}
        style={{ gridAutoFlow: "column", gridAutoColumns: "1fr" }}
      >
        <span
          aria-hidden
          className="slide-indicator capsule-thumb pointer-events-none absolute bottom-[3px] left-[3px] top-[3px] rounded-full"
          style={{ width: `calc((100% - 6px) / ${options.length})`, transform: `translateX(${index * 100}%)` }}
        />
        {options.map((option) => (
          <TabsPrimitive.Trigger
            key={option.value}
            value={option.value}
            className={cn(
              "relative inline-flex min-h-[26px] items-center justify-center whitespace-nowrap rounded-full px-3.5 text-t12 font-medium transition-colors duration-ui ease-out [@media(pointer:coarse)]:min-h-10 [@media(pointer:coarse)]:text-t15",
              option.value === value ? "text-text" : "text-text-3 hover:text-text-2"
            )}
          >
            {option.label}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
    </TabsPrimitive.Root>
  );
}
