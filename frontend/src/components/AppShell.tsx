import { useEffect, useState } from "react";
import { Activity, Gauge, MessageSquare, Moon, ShieldAlert, Sun, Table2, WifiOff, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type PageId = "overview" | "operator" | "attacks" | "telemetry";

interface NavItem {
  id: PageId;
  label: string;
  title: string;
  Icon: LucideIcon;
}

export const NAV: NavItem[] = [
  { id: "overview", label: "Overview", title: "Overview", Icon: Gauge },
  { id: "operator", label: "Operator", title: "AI Operator", Icon: MessageSquare },
  { id: "attacks", label: "Attacks", title: "Attack Lab", Icon: ShieldAlert },
  { id: "telemetry", label: "Telemetry", title: "Telemetry", Icon: Table2 }
];

interface AppShellProps {
  page: PageId;
  onPage: (page: PageId) => void;
  connected: boolean;
  stale: boolean;
  notice: string;
  onDismissNotice: () => void;
  children: React.ReactNode;
}

type ThemeMode = "system" | "light" | "dark";

function useTheme() {
  const [mode, setMode] = useState<ThemeMode>("system");
  useEffect(() => {
    const root = document.documentElement;
    if (mode === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode);
  }, [mode]);
  const dark = mode === "dark" || (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  return { dark, toggle: () => setMode(dark ? "light" : "dark") };
}

export function AppShell({ page, onPage, connected, stale, notice, onDismissNotice, children }: AppShellProps) {
  const [scrolled, setScrolled] = useState(false);
  const theme = useTheme();
  const current = NAV.find((item) => item.id === page) ?? NAV[0];

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="min-h-dvh md:grid md:grid-cols-[240px_minmax(0,1fr)]">
      <aside className="material-thin sticky top-0 hidden h-dvh flex-col gap-1 px-3 pb-6 pt-safe md:flex">
        <div className="flex items-center gap-3 px-3 pb-5 pt-6">
          <div className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-blue">
            <Activity size={20} className="text-white" strokeWidth={2} />
          </div>
          <div>
            <p className="text-t15 font-semibold leading-tight text-text">SCRAM</p>
            <p className="text-t11 text-text-tertiary">Control room</p>
          </div>
        </div>
        <nav className="flex flex-col gap-0.5" aria-label="Primary">
          {NAV.map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              aria-current={page === id ? "page" : undefined}
              onClick={() => onPage(id)}
              className={cn(
                "flex min-h-[34px] items-center gap-3 rounded-[10px] px-3 text-left text-t13 font-medium transition-colors duration-200",
                page === id ? "bg-[var(--material-fill)] text-text" : "text-text-secondary hover:bg-[var(--material-fill)]"
              )}
            >
              <Icon size={18} strokeWidth={1.75} className={page === id ? "text-blue" : ""} />
              {label}
            </button>
          ))}
        </nav>
        <div className="mt-auto flex items-center justify-between px-3">
          <span className="flex items-center gap-2 text-t12 text-text-secondary">
            <i className={cn("h-2 w-2 rounded-full", connected && !stale ? "bg-green" : "bg-orange")} />
            {connected ? (stale ? "Stale telemetry" : "Live") : "Offline"}
          </span>
          <Button variant="plain" size="icon" onClick={theme.toggle} aria-label="Toggle appearance">
            {theme.dark ? <Sun size={16} /> : <Moon size={16} />}
          </Button>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="material-thin sticky top-0 z-20 pt-safe">
          <div className={cn("mx-auto flex max-w-[1200px] items-center justify-between px-4 transition-[height] duration-300 md:px-8", scrolled ? "h-12" : "h-[72px] md:h-[88px]")}>
            <h1 className={cn("font-semibold text-text transition-all duration-300", scrolled ? "text-t17" : "text-t34")}>{current.title}</h1>
            <div className="flex items-center gap-2 md:hidden">
              <span className="flex items-center gap-2 text-t12 text-text-secondary">
                {connected ? <i className={cn("h-2 w-2 rounded-full", stale ? "bg-orange" : "bg-green")} /> : <WifiOff size={14} />}
              </span>
              <Button variant="plain" size="icon" onClick={theme.toggle} aria-label="Toggle appearance">
                {theme.dark ? <Sun size={16} /> : <Moon size={16} />}
              </Button>
            </div>
          </div>
        </header>

        {notice && (
          <div className="material-thick mx-auto mt-3 flex max-w-[1200px] items-center justify-between gap-3 rounded-[12px] px-4 py-2.5 text-t13 text-orange md:mx-8 lg:mx-auto" role="status">
            <span>{notice}</span>
            <button type="button" onClick={onDismissNotice} aria-label="Dismiss" className="text-text-secondary">
              <X size={16} />
            </button>
          </div>
        )}

        <main className="mx-auto max-w-[1200px] space-y-4 px-4 pb-32 pt-4 md:px-8 md:pb-12">{children}</main>
      </div>

      <nav className="material-regular hairline-top fixed inset-x-0 bottom-0 z-30 flex justify-around px-2 pb-safe pt-1.5 md:hidden" aria-label="Primary">
        {NAV.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            aria-current={page === id ? "page" : undefined}
            onClick={() => onPage(id)}
            className={cn("flex min-h-11 min-w-16 flex-col items-center justify-center gap-0.5 rounded-[10px] text-t11 font-medium", page === id ? "text-blue" : "text-text-secondary")}
          >
            <Icon size={22} strokeWidth={1.75} />
            {label}
          </button>
        ))}
      </nav>
    </div>
  );
}
