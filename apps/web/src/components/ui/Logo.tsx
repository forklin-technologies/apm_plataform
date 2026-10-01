/** Marca do produto: caderno quadriculado com um quadrado preenchido (a contribuicao que entra). */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" fill="var(--ink)" />
      <path
        d="M8 8h16M8 16h16M8 24h16M8 8v16M16 8v16M24 8v16"
        stroke="var(--bg)"
        strokeWidth="1.6"
        strokeLinecap="round"
        opacity=".38"
      />
      <rect x="16" y="16" width="8" height="8" fill="var(--bg)" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="inline-flex items-center gap-2.5 text-headline font-bold tracking-[-0.02em]">
      <LogoMark />
      APM Digital
    </span>
  );
}
