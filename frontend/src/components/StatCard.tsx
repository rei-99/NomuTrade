import type { ReactNode } from "react";

interface StatCardProps {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "pos" | "neg" | "";
}

export function StatCard({ label, value, sub, tone = "" }: StatCardProps) {
  return (
    <div className="stat-card panel">
      <div className="stat-label">{label}</div>
      <div className={`stat-value num ${tone}`}>{value}</div>
      {sub !== undefined && <div className="stat-sub">{sub}</div>}
    </div>
  );
}
