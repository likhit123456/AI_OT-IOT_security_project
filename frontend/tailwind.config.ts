import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

const config: Config = {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "var(--bg)",
        text: "var(--text)",
        "text-secondary": "var(--text-secondary)",
        "text-tertiary": "var(--text-tertiary)",
        grouped: "var(--surface-grouped)",
        blue: "var(--system-blue)",
        green: "var(--system-green)",
        red: "var(--system-red)",
        orange: "var(--system-orange)",
        purple: "var(--system-purple)",
        yellow: "var(--system-yellow)"
      },
      fontFamily: {
        text: ["var(--font-text)"],
        display: ["var(--font-display)"],
        mono: ["var(--font-mono)"]
      },
      fontSize: {
        t11: ["11px", { lineHeight: "13px", letterSpacing: "0.06px" }],
        t12: ["12px", { lineHeight: "16px", letterSpacing: "0" }],
        t13: ["13px", { lineHeight: "18px", letterSpacing: "-0.08px" }],
        t15: ["15px", { lineHeight: "20px", letterSpacing: "-0.24px" }],
        t17: ["17px", { lineHeight: "22px", letterSpacing: "-0.41px" }],
        t22: ["22px", { lineHeight: "28px", letterSpacing: "0.35px" }],
        t28: ["28px", { lineHeight: "34px", letterSpacing: "0.36px" }],
        t34: ["34px", { lineHeight: "41px", letterSpacing: "0.37px" }],
        t44: ["44px", { lineHeight: "52px", letterSpacing: "0.35px" }],
        t56: ["56px", { lineHeight: "64px", letterSpacing: "0.35px" }]
      },
      spacing: { 1.5: "6px", 11: "44px", 14: "56px", 20: "80px" },
      borderRadius: { sm: "10px", DEFAULT: "12px", md: "12px", lg: "14px", xl: "16px", "2xl": "20px" },
      backdropBlur: { material: "30px" },
      backdropSaturate: { material: "1.8" }
    }
  },
  plugins: [animate]
};

export default config;
