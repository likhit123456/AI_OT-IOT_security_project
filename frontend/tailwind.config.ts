import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  future: { hoverOnlyWhenSupported: true },
  theme: {
    extend: {
      colors: {
        background: "var(--bg)",
        sunken: "var(--bg-sunken)",
        text: "var(--text)",
        "text-2": "var(--text-2)",
        "text-3": "var(--text-3)",
        rule: "var(--rule)",
        "rule-strong": "var(--rule-strong)",
        ok: "rgb(var(--ok-rgb) / <alpha-value>)",
        warn: "rgb(var(--warn-rgb) / <alpha-value>)",
        bad: "rgb(var(--bad-rgb) / <alpha-value>)"
      },
      fontFamily: {
        text: ["var(--font-text)"],
        display: ["var(--font-display)"],
        mono: ["var(--font-mono)"]
      },
      fontSize: {
        t11: ["11px", { lineHeight: "14px", letterSpacing: "0.08em" }],
        t12: ["12px", { lineHeight: "16px", letterSpacing: "0.01em" }],
        t13: ["13px", { lineHeight: "18px", letterSpacing: "0" }],
        t15: ["15px", { lineHeight: "22px", letterSpacing: "-0.01em" }],
        t17: ["17px", { lineHeight: "24px", letterSpacing: "-0.014em" }],
        t22: ["22px", { lineHeight: "28px", letterSpacing: "-0.018em" }],
        t28: ["28px", { lineHeight: "34px", letterSpacing: "-0.022em" }],
        t34: ["34px", { lineHeight: "40px", letterSpacing: "-0.026em" }],
        t44: ["44px", { lineHeight: "48px", letterSpacing: "-0.03em" }],
        t56: ["56px", { lineHeight: "58px", letterSpacing: "-0.034em" }],
        t72: ["72px", { lineHeight: "72px", letterSpacing: "-0.04em" }]
      },
      borderRadius: { DEFAULT: "10px", md: "10px", lg: "12px", xl: "16px" },
      transitionTimingFunction: {
        out: "var(--ease-out)",
        "in-out": "var(--ease-in-out)",
        drawer: "var(--ease-drawer)"
      },
      transitionDuration: { press: "160ms", ui: "200ms", move: "250ms" }
    }
  },
  plugins: []
};

export default config;
