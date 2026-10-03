import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PainelShell } from "@/components/painel/PainelShell";
import { api } from "@/lib/api";
import { parseSection } from "@/lib/painel-section";

export const metadata: Metadata = { title: "Painel" };

type Props = { searchParams: Promise<{ tipo?: string | string[] }> };

export default async function PainelPage({ searchParams }: Props) {
  const { tipo } = await searchParams;
  const orgs = await api.dashboard.listOrganizations();
  const firstSchool = orgs.ok ? orgs.data[0]?.schools[0] : undefined;
  if (!orgs.ok || !firstSchool) notFound();

  const dashboard = await api.dashboard.getDashboard(firstSchool.id);
  if (!dashboard.ok) notFound();

  return (
    <PainelShell
      organizations={orgs.data}
      initialSchoolId={firstSchool.id}
      initialData={dashboard.data}
      section={parseSection(tipo)}
    />
  );
}
