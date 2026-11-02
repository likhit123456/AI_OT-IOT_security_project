import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[12px] font-semibold transition-[background-color,opacity,transform] duration-200 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40 [@media(pointer:coarse)]:min-h-11",
  {
    variants: {
      variant: {
        primary: "bg-blue text-white hover:opacity-90",
        bordered: "bg-transparent text-blue ring-1 ring-inset ring-blue hover:bg-blue/10",
        tinted: "bg-[var(--material-fill)] text-text hover:bg-[var(--material-thin)]",
        plain: "bg-transparent text-blue hover:bg-blue/10",
        destructive: "bg-[var(--material-fill)] text-red hover:bg-red/10"
      },
      size: {
        default: "min-h-[34px] px-5 py-2.5 text-t13 [@media(pointer:coarse)]:text-t17",
        sm: "min-h-[28px] px-3 py-1.5 text-t12 [@media(pointer:coarse)]:text-t15",
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
