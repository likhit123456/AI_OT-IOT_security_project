import { cn } from "@/lib/utils";

interface SectionProps {
  title: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export function Section({ title, aside, children, className }: SectionProps) {
  return (
    <section className={cn("border-t border-rule pt-5", className)}>
      <header className="mb-5 flex min-h-[28px] flex-wrap items-center justify-between gap-3">
        <h2 className="eyebrow !font-medium">{title}</h2>
        {aside}
      </header>
      {children}
    </section>
  );
}
