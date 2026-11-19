import { useMemo } from "react";

interface SparklineProps {
  values: (number | null)[];
  height?: number;
}

export function Sparkline({ values, height = 24 }: SparklineProps) {
  const path = useMemo(() => {
    const data = values.filter((v): v is number => v !== null && Number.isFinite(v));
    if (data.length < 2) return "";
    const lo = Math.min(...data);
    const hi = Math.max(...data);
    const span = Math.max(hi - lo, 1e-6);
    return data
      .map((v, i) => `${i === 0 ? "M" : "L"}${((i / (data.length - 1)) * 100).toFixed(2)},${(height - 2 - ((v - lo) / span) * (height - 4)).toFixed(2)}`)
      .join("");
  }, [values, height]);

  if (!path) return <div style={{ height }} />;

  return (
    <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" width="100%" height={height} aria-hidden>
      <path d={path} fill="none" stroke="var(--text-3)" strokeWidth="1" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
