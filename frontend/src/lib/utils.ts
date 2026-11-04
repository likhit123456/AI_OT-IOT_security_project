import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: ["t11", "t12", "t13", "t15", "t17", "t22", "t28", "t34", "t44", "t56", "t72"] }]
    }
  }
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmt(value: number | null | undefined, digits = 1, fallback = "—") {
  if (value === null || value === undefined || !Number.isFinite(value)) return fallback;
  return value.toFixed(digits);
}

export function clamp(value: number, low: number, high: number) {
  return Math.min(high, Math.max(low, value));
}

export function clock(seconds: number) {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}
