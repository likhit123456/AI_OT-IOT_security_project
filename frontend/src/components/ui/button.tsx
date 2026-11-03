import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "press inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[10px] font-medium transition-[transform,background-color,color,opacity] duration-press ease-out active:scale-[0.97] disabled:pointer-events-none disabled:opacity-35 [@media(pointer:coarse)]:min-h-11",
  {
    variants: {
      variant: {
        primary: "bg-text text-background hover:opacity-85",
        quiet: "bg-transparent text-text ring-1 ring-inset ring-rule-strong hover:bg-sunken",
        plain: "bg-transparent text-text-2 hover:text-text",
        danger: "bg-transparent text-bad ring-1 ring-inset ring-bad/35 hover:bg-bad/10"
      },
      size: {
        default: "min-h-[34px] px-4 py-2 text-t13 [@media(pointer:coarse)]:text-t17",
        sm: "min-h-[28px] px-3 py-1 text-t12 [@media(pointer:coarse)]:text-t15",
        icon: "h-8 w-8 p-0 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11"
      }
    },
    defaultVariants: { variant: "primary", size: "default" }
  }
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type = "button", ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp ref={ref} type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
