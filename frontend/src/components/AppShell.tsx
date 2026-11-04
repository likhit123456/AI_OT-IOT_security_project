import { useEffect, useRef, useState } from "react";
import { Activity, Gauge, MessageSquare, Moon, ShieldAlert, Sun, Table2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { AlarmEdge } from "@/components/AlarmEdge";
import { Button } from "@/components/ui/button";
import { StatusText } from "@/components/ui/status-text";
import type { PlantStatus } from "@/lib/status";
import { cn } from "@/lib/utils";

export type PageId = "overview" | "operator" | "attacks" | "telemetry";
export type Direction = "forward" | "back" | "none";

interface NavItem {
  id: PageId;
  label: string;
  title: string;
  subtitle: string;
  Icon: LucideIcon;
}

export const NAV: NavItem[] = [
  { id: "overview", label: "Overview", title: "Overview", subtitle: "Live plant state and every protection layer in one view.", Icon: Gauge },
  { id: "operator", label: "Operator", title: "AI operator", subtitle: "Instruct the supervisor and set how much authority it holds.", Icon: MessageSquare },
  { id: "attacks", label: "Attacks", title: "Attack lab", subtitle: "Inject faults and attacks, then watch each layer respond.", Icon: ShieldAlert },
  { id: "telemetry", label: "Telemetry", title: "Telemetry", subtitle: "Retained samples, summary statistics and export.", Icon: Table2 }
];

interface AppShellProps {
  page: PageId;
  direction: Direction;
  onPage: (page: PageId) => void;
  connected: boolean;
  status: PlantStatus;
  overview: React.ReactNode;
  children: React.ReactNode;
}

type ThemeMode = "system" | "light" | "dark";

function useTheme() {
  const [mode, setMode] = useState<ThemeMode>("system");
  const first = useRef(true);
  useEffect(() => {
    const root = document.documentElement;
    if (!first.current) {
      root.setAttribute("data-switching", "");
      window.setTimeout(() => root.removeAttribute("data-switching"), 320);
    }
    first.current = false;
    if (mode === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode);
  }, [mode]);
  const dark = mode === "dark" || (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  return { dark, toggle: () => setMode(dark ? "light" : "dark") };
}

export function AppShell({ page, direction, onPage, connected, status, overview, children }: AppShellProps) {
  const [scrolled, setScrolled] = useState(false);
  const theme = useTheme();
  const index = Math.max(0, NAV.findIndex((item) => item.id === page));
  const current = NAV[index];

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="min-h-dvh overflow-x-clip">
      <AlarmEdge tone={connected ? status.tone : "neutral"} />
      <header className="sticky top-0 z-40 pt-safe">
        <div
          aria-hidden
          className="bar-glass pointer-events-none absolute inset-0 transition-opacity duration-ui ease-out"
          style={{ opacity: scrolled ? 1 : 0 }}
        />
        <div className="relative mx-auto flex h-16 max-w-[1180px] items-center justify-between gap-6 px-5 md:px-8">
          <div className="flex items-center gap-8">
            <div className="flex items-center gap-2.5">
              <Activity size={18} strokeWidth={2.25} className="text-text" />
              <span className="font-display text-t15 font-semibold tracking-[-0.01em] text-text">SCRAM</span>
            </div>
            <nav
              className="capsule relative hidden w-[440px] grid-flow-col auto-cols-fr rounded-full p-1 md:grid"
              aria-label="Primary"
              data-instant={direction === "none" ? "" : undefined}
            >
              <span
                aria-hidden
                className="slide-indicator capsule-thumb pointer-events-none absolute bottom-1 left-1 top-1 rounded-full"
                style={{ width: "calc((100% - 8px) / 4)", transform: `translateX(${index * 100}%)` }}
              />
              {NAV.map(({ id, label }) => (
                <button
                  key={id}
                  type="button"
                  aria-current={page === id ? "page" : undefined}
                  onClick={() => onPage(id)}
                  className={cn(
                    "relative flex h-8 items-center justify-center gap-2 rounded-full px-3 text-t13 font-medium transition-colors duration-ui ease-out",
                    page === id ? "text-text" : "text-text-3 hover:text-text-2"
                  )}
                >
                  {label}
                </button>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-4">
            <StatusText tone={connected ? status.tone : "neutral"} className="text-t12 font-medium max-[380px]:[&>span]:hidden">
              {status.title}
            </StatusText>
            <Button variant="plain" size="icon" onClick={theme.toggle} aria-label="Toggle appearance">
              {theme.dark ? <Sun size={16} /> : <Moon size={16} />}
            </Button>
          </div>
        </div>
        <div aria-hidden className="relative h-px bg-rule transition-opacity duration-ui ease-out" style={{ opacity: scrolled ? 1 : 0 }} />
      </header>

      <main className="mx-auto max-w-[1180px] px-5 pb-32 pt-10 md:px-8 md:pb-24 md:pt-14">
        {page !== "overview" && (
          <div key={page} className={cn(direction === "forward" && "page-forward", direction === "back" && "page-back")}>
            <div className="mb-12">
              <p className="eyebrow mb-3">0{index + 1} / {current.label}</p>
              <h1 className="text-t44 text-text md:text-t56">{current.title}</h1>
              <p className="mt-3 max-w-xl text-t15 text-text-2">{current.subtitle}</p>
            </div>
            <div className="space-y-14">{children}</div>
          </div>
        )}
        {overview}
      </main>

      <nav className="bar-glass fixed inset-x-0 bottom-0 z-40 flex justify-around border-t border-rule px-2 pb-safe pt-1.5 md:hidden" aria-label="Primary">
        {NAV.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            aria-current={page === id ? "page" : undefined}
            onClick={() => onPage(id)}
            className={cn(
              "press flex min-h-11 min-w-16 flex-col items-center justify-center gap-0.5 rounded-[10px] text-t11 font-medium normal-case tracking-normal transition-[transform,color] duration-press ease-out active:scale-[0.97]",
              page === id ? "text-text" : "text-text-3"
            )}
          >
            <Icon size={21} strokeWidth={1.75} />
            {label}
          </button>
        ))}
      </nav>
    </div>
  );
}
