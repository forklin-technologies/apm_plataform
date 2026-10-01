import type { StatusTone } from "@/lib/status";

const tones: Record<StatusTone, string> = {
  neutral: "bg-neutral-soft text-ink-2",
  success: "bg-ok-soft text-ok",
  warning: "bg-warn-soft text-warn",
  danger: "bg-bad-soft text-bad",
  info: "bg-info-soft text-info",
};

export function StatusPill({ tone, children }: { tone: StatusTone; children: React.ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-1 text-foot font-semibold leading-none ${tones[tone]}`}
    >
      {children}
    </span>
  );
}
