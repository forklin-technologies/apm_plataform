import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PortalShell } from "@/components/portal/PortalShell";
import { ReceiptView } from "@/components/portal/ReceiptView";
import { api } from "@/lib/api";
import { isPlausibleToken } from "@/lib/api/token";

type Props = { params: Promise<{ slug: string; token: string }> };

export const metadata: Metadata = {
  title: "Comprovante",
  // O link e a credencial de acesso (ADR-010): nunca indexar nem repassar como referrer.
  robots: { index: false, follow: false },
};

export default async function ReceiptPage({ params }: Props) {
  const { slug, token } = await params;
  if (!isPlausibleToken(token)) notFound();

  const [school, receipt] = await Promise.all([
    api.schools.getPublicSchool(slug),
    api.contributions.getReceipt(slug, token),
  ]);
  if (!school.ok) notFound();

  // O servidor SO afirma o que a camada de dados confirma AQUI. Se ela nao conhece o token no
  // servidor (no protótipo, os pedidos moram no navegador), o HTML sai NEUTRO, sem "Pago" e sem
  // dado de pessoa, e quem responde e a camada de dados no cliente: comprovante, "ainda nao
  // confirmado" ou a mesma 404 estilizada. Com um backend real, o servidor ja devolveria o 404.
  const initial = receipt.ok ? receipt.data : null;

  return (
    <PortalShell school={school.data}>
      <ReceiptView slug={slug} token={token} initial={initial} />
    </PortalShell>
  );
}
