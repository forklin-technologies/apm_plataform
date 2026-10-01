/** Marcador obrigatorio: tudo que nao e a API real e dado de exemplo. */
export function PrototypeBadge({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full bg-warn-soft px-3 py-1.5 text-foot font-semibold leading-none text-warn ${className}`}
    >
      <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
      Protótipo · dados de exemplo
    </span>
  );
}
