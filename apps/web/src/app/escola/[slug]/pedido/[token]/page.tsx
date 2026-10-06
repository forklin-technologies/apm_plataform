import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PortalNotice } from "@/components/portal/PortalNotice";
import { PortalShell } from "@/components/portal/PortalShell";
import { ReceiptView } from "@/components/portal/ReceiptView";
import { getPublicSchool, getReceipt } from "@/lib/api/public";
import { serverFetch } from "@/lib/api/server";
import { isPlausibleSlug, isPlausibleToken } from "@/lib/api/token";

type Props = { params: Promise<{ slug: string; token: string }> };

export const metadata: Metadata = {
  title: "Comprovante",
  // O link e a credencial de acesso (ADR-010): nunca indexar nem repassar como referrer.
  robots: { index: false, follow: false },
};

export default async function ReceiptPage({ params }: Props) {
  const { slug, token } = await params;
  if (!isPlausibleSlug(slug) || !isPlausibleToken(token)) notFound();

  const [school, receipt] = await Promise.all([
    getPublicSchool(slug, { fetchImpl: serverFetch }),
    getReceipt(slug, token, { fetchImpl: serverFetch }),
  ]);
  // A API responde o MESMO 404 para escola ruim e para token inexistente, de outra escola ou malformado.
  if ((!school.ok && school.error.status === 404) || (!receipt.ok && receipt.error.status === 404)) notFound();
  if (!school.ok) {
    return (
      <PortalNotice
        title="Não foi possível abrir o comprovante"
        text="Não conseguimos falar com o servidor agora. Tente de novo em instantes."
        reloadHref={`/escola/${slug}/pedido/${token}`}
      />
    );
  }

  // O servidor SO afirma o que a API confirmou AQUI (200). 409 = ainda nao confirmado. Qualquer outra
  // falha deixa o HTML NEUTRO ("Conferindo seu comprovante"): sem "Pago", sem valor, sem dado de pessoa.
  const initial = receipt.ok ? { receipt: receipt.data } : receipt.error.status === 409 ? { unconfirmed: true as const } : null;

  return (
    <PortalShell school={school.data}>
      <ReceiptView slug={slug} token={token} initial={initial} />
    </PortalShell>
  );
}
