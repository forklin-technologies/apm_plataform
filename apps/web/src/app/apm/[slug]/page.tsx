import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ContributionFlow } from "@/components/portal/ContributionFlow";
import { PortalShell } from "@/components/portal/PortalShell";
import { api } from "@/lib/api";

type Props = { params: Promise<{ slug: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const result = await api.schools.getPublicSchool(slug);
  return { title: result.ok ? `Contribuir · ${result.data.name}` : "Escola não encontrada" };
}

export default async function SchoolPortalPage({ params }: Props) {
  const { slug } = await params;
  // A escola vem do slug da URL, nunca de parametro do cliente (ADR-010).
  const result = await api.schools.getPublicSchool(slug);
  if (!result.ok) notFound();
  return (
    <PortalShell school={result.data}>
      <ContributionFlow school={result.data} />
    </PortalShell>
  );
}
