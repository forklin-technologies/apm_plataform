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
  // Mesma resposta para escola ou pedido inexistente: sem enumeracao.
  if (!school.ok || !receipt.ok) notFound();

  return (
    <PortalShell school={school.data}>
      <ReceiptView slug={slug} token={token} initial={receipt.data} />
    </PortalShell>
  );
}
