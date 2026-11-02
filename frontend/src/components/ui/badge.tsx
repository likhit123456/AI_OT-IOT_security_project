import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-t11 font-semibold", {
  variants: {
    tone: {
      neutral: "bg-[var(--material-fill)] text-text-secondary",
      good: "bg-green/15 text-green",
      warn: "bg-orange/15 text-orange",
      bad: "bg-red/15 text-red",
      info: "bg-blue/15 text-blue"
    }
  },
  defaultVariants: { tone: "neutral" }
});

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
