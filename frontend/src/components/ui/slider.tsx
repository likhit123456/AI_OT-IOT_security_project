import * as React from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";
import { cn } from "@/lib/utils";

const Slider = React.forwardRef<
  React.ElementRef<typeof SliderPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SliderPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SliderPrimitive.Root
    ref={ref}
    className={cn("relative flex h-6 w-full touch-none select-none items-center [@media(pointer:coarse)]:h-11", className)}
    {...props}
  >
    <SliderPrimitive.Track className="relative h-[3px] w-full grow overflow-hidden rounded-full bg-rule-strong">
      <SliderPrimitive.Range className="absolute h-full rounded-full bg-text" />
    </SliderPrimitive.Track>
    <SliderPrimitive.Thumb className="block h-4 w-4 rounded-full bg-text ring-4 ring-background transition-[box-shadow] duration-press focus-visible:outline-none focus-visible:ring-[var(--focus)]" />
  </SliderPrimitive.Root>
));
Slider.displayName = "Slider";

export { Slider };
