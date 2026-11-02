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
    <SliderPrimitive.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-[var(--material-fill)]">
      <SliderPrimitive.Range className="absolute h-full rounded-full bg-blue" />
    </SliderPrimitive.Track>
    <SliderPrimitive.Thumb className="block h-5 w-5 rounded-full bg-white shadow-[0_1px_4px_rgba(0,0,0,0.35)] transition-shadow focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--focus-halo)]" />
  </SliderPrimitive.Root>
));
Slider.displayName = "Slider";

export { Slider };
