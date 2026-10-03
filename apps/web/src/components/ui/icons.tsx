import type { SVGProps } from "react";

/** Icones proprios (tracos simples, 24px). Nenhum asset de terceiros. */
type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 20, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const CheckIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </Svg>
);
export const CopyIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect x="9" y="9" width="11" height="11" rx="2.5" />
    <path d="M5 15V6.5A1.5 1.5 0 0 1 6.5 5H15" />
  </Svg>
);
export const ChevronRightIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9.5 5.5L16 12l-6.5 6.5" />
  </Svg>
);
export const ChevronDownIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 9.5l6 6 6-6" />
  </Svg>
);
export const ChevronLeftIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M14.5 5.5L8 12l6.5 6.5" />
  </Svg>
);
export const LockIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect x="5" y="10.5" width="14" height="9.5" rx="2.5" />
    <path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" />
  </Svg>
);
export const ClockIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Svg>
);
export const GridIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect x="4.5" y="4.5" width="6" height="6" rx="1.5" />
    <rect x="13.5" y="4.5" width="6" height="6" rx="1.5" />
    <rect x="4.5" y="13.5" width="6" height="6" rx="1.5" />
    <rect x="13.5" y="13.5" width="6" height="6" rx="1.5" />
  </Svg>
);
/** Contribuicao: entrada de dinheiro. */
export const InflowIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4.5v10M7.5 10.5L12 15l4.5-4.5" />
    <path d="M5 19.5h14" />
  </Svg>
);
/** Despesa: saida de dinheiro. */
export const OutflowIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M7 17L17 7M9 7h8v8" />
  </Svg>
);
/** Reembolso: recibo do gasto de quem pagou do proprio bolso. */
export const ReceiptIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 3.5h12v17l-2.4-1.6-2 1.6-1.6-1.6-1.6 1.6-2-1.6L6 20.5z" />
    <path d="M9 8.5h6M9 12h6" />
  </Svg>
);
/** Devolucao (estorno): volta ao pagador. */
export const UndoIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 4.5L4.5 9 9 13.5" />
    <path d="M4.5 9H14.5a5 5 0 0 1 0 10H10" />
  </Svg>
);
export const SchoolIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3.5 9.5L12 5l8.5 4.5" />
    <path d="M5.5 10.5V18M18.5 10.5V18M9.5 18v-4.5h5V18M3.5 18.5h17" />
  </Svg>
);
export const RefreshIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" />
    <path d="M19.5 4.5v4h-4" />
  </Svg>
);
export const InfoIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5M12 8h.01" />
  </Svg>
);
export const SettingsIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 7h9M18 7h1M5 17h1M10 17h9" />
    <circle cx="16" cy="7" r="2" />
    <circle cx="8" cy="17" r="2" />
  </Svg>
);
export const ReportIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 19.5V5M5 19.5h14" />
    <path d="M9 16v-4M13 16V8M17 16v-6" />
  </Svg>
);
