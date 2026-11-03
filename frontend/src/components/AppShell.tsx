import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Activity, Gauge, MessageSquare, Moon, ShieldAlert, Sun, Table2, X } from "lucide-react";
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
  notice: string;
  noticeKey: number;
  onDismissNotice: () => void;
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

export function AppShell({ page, direction, onPage, connected, status, notice, noticeKey, onDismissNotice, overview, children }: AppShellProps) {
  const [scrolled, setScrolled] = useState(false);
  const [mark, setMark] = useState<{ left: number; width: number } | null>(null);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const theme = useTheme();
  const index = Math.max(0, NAV.findIndex((item) => item.id === page));
  const current = NAV[index];

  useLayoutEffect(() => {
    const measure = () => {
      const el = buttons.current[index];
      if (el) setMark({ left: el.offsetLeft, width: el.offsetWidth });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [index]);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="min-h-dvh">
      <AlarmEdge tone={connected ? status.tone : "neutral"} />
      <header className="sticky top-0 z-40 bg-background pt-safe">
        <div className="mx-auto flex h-14 max-w-[1180px] items-center justify-between gap-6 px-5 md:px-8">
          <div className="flex items-center gap-8">
            <div className="flex items-center gap-2.5">
              <Activity size={18} strokeWidth={2.25} className="text-text" />
              <span className="font-display text-t15 font-semibold tracking-[-0.01em] text-text">SCRAM</span>
            </div>
            <nav className="relative hidden h-14 items-stretch gap-1 md:flex" aria-label="Primary" data-instant={direction === "none" ? "" : undefined}>
              {NAV.map(({ id, label }, i) => (
                <button
                  key={id}
                  ref={(el) => {
                    buttons.current[i] = el;
                  }}
                  type="button"
                  aria-current={page === id ? "page" : undefined}
                  onClick={() => onPage(id)}
                  className={cn("flex items-center gap-2 px-3 text-t13 font-medium transition-colors duration-ui ease-out", page === id ? "text-text" : "text-text-3 hover:text-text-2")}
                >
                  {label}
                  <kbd className="text-t11 font-medium tracking-normal text-text-3">{i + 1}</kbd>
                </button>
              ))}
              <span
                aria-hidden
                className="slide-indicator absolute bottom-0 left-0 h-[2px] w-px origin-left bg-text"
                style={{ opacity: mark ? 1 : 0, transform: mark ? `translateX(${mark.left}px) scaleX(${mark.width})` : undefined }}
              />
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
        <div aria-hidden className="h-px bg-rule transition-opacity duration-ui ease-out" style={{ opacity: scrolled ? 1 : 0 }} />
      </header>

      {notice && (
        <div className="mx-auto mt-4 max-w-[1180px] px-5 md:px-8">
          <div key={noticeKey} className="shake flex items-center justify-between gap-3 rounded-[10px] bg-sunken px-4 py-2.5 text-t13 text-warn" role="status">
            <span>{notice}</span>
            <button type="button" onClick={onDismissNotice} aria-label="Dismiss" className="text-text-3 transition-colors duration-ui hover:text-text">
              <X size={15} />
            </button>
          </div>
        </div>
      )}

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

      <nav className="fixed inset-x-0 bottom-0 z-40 flex justify-around border-t border-rule bg-background px-2 pb-safe pt-1.5 md:hidden" aria-label="Primary">
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
