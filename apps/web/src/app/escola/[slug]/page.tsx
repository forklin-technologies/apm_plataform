import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ContributionFlow } from "@/components/portal/ContributionFlow";
import { PortalNotice } from "@/components/portal/PortalNotice";
import { PortalShell } from "@/components/portal/PortalShell";
import { getPublicSchool } from "@/lib/api/public";
import { serverFetch } from "@/lib/api/server";
import { isPlausibleSlug } from "@/lib/api/token";
import { formatWait } from "@/lib/auth-messages";

type Props = { params: Promise<{ slug: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  if (!isPlausibleSlug(slug)) return { title: "Escola não encontrada" };
  const result = await getPublicSchool(slug, { fetchImpl: serverFetch });
  return { title: result.ok ? `Contribuir · ${result.data.name}` : "Escola não encontrada" };
}

export default async function SchoolPortalPage({ params }: Props) {
  const { slug } = await params;
  if (!isPlausibleSlug(slug)) notFound();
  // A escola vem do slug da URL, nunca de parametro do cliente (ADR-010). A API responde o mesmo 404
  // para qualquer slug que nao exista.
  const result = await getPublicSchool(slug, { fetchImpl: serverFetch });
  if (!result.ok) {
    if (result.error.status === 404) notFound();
    const limited = result.error.code === "rate_limited";
    return (
      <PortalNotice
        title={limited ? "Muitos acessos agora" : "Não foi possível abrir esta página"}
        text={
          limited
            ? `Aguarde ${formatWait(result.error.retryAfterSeconds)} e tente de novo.`
            : "Não conseguimos falar com o servidor agora. Nada foi cobrado. Tente de novo em instantes."
        }
        reloadHref={`/escola/${slug}`}
      />
    );
  }
  return (
    <PortalShell school={result.data}>
      <ContributionFlow school={result.data} />
    </PortalShell>
  );
}
