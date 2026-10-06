import Link from "next/link";
import { ApiStatusChip } from "@/components/status/ApiStatusChip";
import { ChevronRightIcon } from "@/components/ui/icons";
import { Wordmark } from "@/components/ui/Logo";
import { PrototypeBadge } from "@/components/ui/PrototypeBadge";

const DEMOS = [
  {
    href: "/escola/demo-aurora",
    title: "Contribuir como família",
    text: "Portal da Escola Aurora (demo): do valor ao QR Code do Pix e ao comprovante.",
  },
  {
    href: "/escola/demo-horizonte",
    title: "Outra escola",
    text: "Escola Horizonte (demo), com a sua própria configuração.",
  },
  {
    href: "/login",
    title: "Entrar na tesouraria",
    text: "Painel da escola com o extrato do mês, para quem cuida das contas.",
  },
] as const;

export default function HomePage() {
  return (
    <div className="paper-grid min-h-dvh">
      {/* Cabecalho de altura fixa: no celular o chip tem a propria linha (reservada), entao a API fora do ar
            ou voltando nunca empurra o titulo. */}
      <header className="mx-auto flex w-full max-w-6xl flex-col gap-1 px-5 pb-2 pt-5 sm:flex-row sm:items-center sm:justify-between sm:gap-6 sm:px-8 sm:pt-7">
        <Wordmark />
        <ApiStatusChip />
      </header>

      <main id="conteudo" tabIndex={-1} className="outline-none mx-auto w-full max-w-6xl px-5 pb-16 pt-8 sm:px-8 lg:pt-16">
        <div className="grid gap-12">
          <div>
            <PrototypeBadge />
            <h1 className="mt-5 max-w-[17ch] text-balance text-hero tracking-[-0.03em] sm:max-w-[20ch] sm:text-[3.25rem] lg:text-[3.75rem]">
              Contribuir com a APM leva um minuto. Prestar contas, também.
            </h1>
            <p className="mt-5 max-w-[46ch] text-body text-ink-2 sm:text-[1.1875rem]">
              Famílias pagam por Pix pelo celular, sem criar conta. A tesouraria acompanha cada contribuição, despesa e
              reembolso no mesmo lugar.
            </p>

            <h2 className="mb-3 mt-10 text-headline text-ink">Veja funcionando</h2>
            <ul className="overflow-hidden rounded-[var(--r-lg)] bg-surface shadow-[0_0_0_1px_var(--line)]">
              {DEMOS.map((demo, index) => (
                <li key={demo.href} className={index > 0 ? "border-t border-line" : ""}>
                  <Link
                    href={demo.href}
                    className="group flex min-h-[4.5rem] items-center gap-4 px-5 py-3.5 outline-offset-[-3px] active:bg-neutral-soft"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block text-body font-semibold leading-snug text-ink">{demo.title}</span>
                      <span className="mt-0.5 block text-sub text-ink-2">{demo.text}</span>
                    </span>
                    <ChevronRightIcon size={18} className="shrink-0 text-ink-3" />
                  </Link>
                </li>
              ))}
            </ul>
          </div>

        </div>
      </main>

      <footer className="mx-auto w-full max-w-6xl px-5 pb-10 sm:px-8">
        <p className="max-w-[60ch] text-foot text-ink-2">
          Versão de demonstração com dados de exemplo. Em desenvolvimento o Pix é um sandbox: nenhum pagamento
          de verdade é feito.
        </p>
      </footer>
    </div>
  );
}
