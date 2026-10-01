import Link from "next/link";
import { ReceiptCard } from "@/components/portal/ReceiptCard";
import { SchoolThemeScope } from "@/components/portal/SchoolThemeScope";
import { ApiStatusChip } from "@/components/status/ApiStatusChip";
import { ChevronRightIcon } from "@/components/ui/icons";
import { Wordmark } from "@/components/ui/Logo";
import { PrototypeBadge } from "@/components/ui/PrototypeBadge";
import { api, DEMO_RECEIPT_TOKEN } from "@/lib/api";

const DEMOS = [
  {
    href: "/apm/escola-exemplo",
    title: "Contribuir como família",
    text: "Portal da Escola Exemplo, da escolha do valor ao comprovante.",
  },
  {
    href: "/painel",
    title: "Painel da tesouraria",
    text: "Indicadores do mês, movimentações e troca de escola.",
  },
  {
    href: "/apm/escola-horizonte",
    title: "Outra escola, outra cor",
    text: "Colégio Horizonte, com campos de identificação diferentes.",
  },
  {
    href: `/apm/escola-exemplo/pedido/${DEMO_RECEIPT_TOKEN}`,
    title: "Comprovante de exemplo",
    text: "O recibo que a família recebe depois de pagar.",
  },
  {
    href: "/login",
    title: "Entrar",
    text: "Tela de acesso. A autenticação chega na Fase 1.",
  },
] as const;

export default async function HomePage() {
  const [school, receipt] = await Promise.all([
    api.schools.getPublicSchool("escola-exemplo"),
    api.contributions.getReceipt("escola-exemplo", DEMO_RECEIPT_TOKEN),
  ]);

  return (
    <div className="paper-grid min-h-dvh">
      <header className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-3 px-5 pb-2 pt-5 sm:px-8 sm:pt-7">
        <Wordmark />
        <ApiStatusChip />
      </header>

      <main id="conteudo" tabIndex={-1} className="outline-none mx-auto w-full max-w-6xl px-5 pb-16 pt-8 sm:px-8 lg:pt-16">
        <div className="grid gap-12 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.8fr)] lg:items-start lg:gap-16">
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

          {school.ok && receipt.ok && (
            <SchoolThemeScope accentColor={school.data.accentColor} className="mx-auto w-full max-w-[26rem] lg:mt-6">
              <div className="lg:rotate-[1.6deg]">
                <ReceiptCard receipt={receipt.data} />
              </div>
              <p className="mx-auto mt-6 max-w-[34ch] text-center text-sub text-ink-2">
                Cada escola tem a sua cor. O dinheiro vai direto para a conta da APM, e a plataforma não guarda valores.
              </p>
            </SchoolThemeScope>
          )}
        </div>
      </main>

      <footer className="mx-auto w-full max-w-6xl px-5 pb-10 sm:px-8">
        <p className="max-w-[60ch] text-foot text-ink-2">
          Versão de demonstração. Só o status da API é real: contribuição, Pix, comprovante e painel usam dados de
          exemplo, e o Pix exibido nunca é um pagamento de verdade.
        </p>
      </footer>
    </div>
  );
}
