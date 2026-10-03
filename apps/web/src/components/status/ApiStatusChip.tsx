"use client";

import { RefreshIcon } from "@/components/ui/icons";
import { useApiStatus, type UseApiStatusOptions } from "@/lib/hooks/useApiStatus";

const COPY = {
  checking: { label: "Verificando a API", detail: "Consultando a API…" },
  ready: { label: "API no ar", detail: "A API respondeu e está pronta." },
  "not-ready": {
    label: "API indisponível",
    detail: "A API respondeu, mas ainda não está pronta para atender.",
  },
  unreachable: {
    label: "API indisponível",
    detail: "Não foi possível falar com a API. A interface de exemplo continua funcionando.",
  },
} as const;

/**
 * Unica integracao REAL do prototipo: GET /api/health/ready (mesma origem).
 * Se a API cair, o chip muda de estado e o resto da pagina segue de pe.
 */
export function ApiStatusChip({
  showDetail = false,
  ...options
}: UseApiStatusOptions & { showDetail?: boolean }) {
  const status = useApiStatus(options);
  const key = status.state === "unavailable" ? status.reason : status.state;
  const copy = COPY[key];
  const tone =
    status.state === "ready"
      ? "bg-ok-soft text-ok"
      : status.state === "unavailable"
        ? "bg-bad-soft text-bad"
        : "bg-neutral-soft text-ink-2";

  return (
    <div className={`flex min-h-11 items-center gap-x-2 ${showDetail ? "flex-wrap gap-y-1" : "flex-nowrap"}`}>
      <span
        role="status"
        aria-live="polite"
        data-state={status.state}
        className={`inline-flex min-h-8 items-center gap-2 rounded-full px-3 py-1.5 text-foot font-semibold leading-none ${tone}`}
      >
        <span
          aria-hidden="true"
          className={`size-2 rounded-full bg-current ${status.state === "checking" ? "pulse-dot" : ""}`}
        />
        {copy.label}
        <span className="sr-only">. {copy.detail}</span>
      </span>
      {showDetail && <p className="text-foot text-ink-2">{copy.detail}</p>}
      {status.state === "unavailable" && (
        <button
          type="button"
          onClick={status.refresh}
          className="inline-flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-full px-3 text-foot font-semibold text-accent-ink hover:bg-neutral-soft"
        >
          <RefreshIcon size={16} />
          {/* so o icone em telas bem estreitas: o chip e o botao cabem sempre na mesma linha */}
          <span className="max-[419px]:sr-only">Tentar de novo</span>
        </button>
      )}
    </div>
  );
}
