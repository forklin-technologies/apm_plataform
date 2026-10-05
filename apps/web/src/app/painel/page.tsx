import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { PainelShell } from "@/components/painel/PainelShell";
import { SessionUnavailable } from "@/components/painel/SessionUnavailable";
import { api } from "@/lib/api";
import { getServerSession } from "@/lib/api/server";
import { parseSection } from "@/lib/painel-section";

export const metadata: Metadata = { title: "Painel" };

type Props = { searchParams: Promise<{ tipo?: string | string[] }> };

export default async function PainelPage({ searchParams }: Props) {
  const { tipo } = await searchParams;

  // REAL: o painel exige sessao. Sem ela (ou expirada, ou revogada), volta para o login.
  const auth = await getServerSession();
  if (auth.state === "anonymous") redirect("/login");
  if (auth.state === "unavailable") return <SessionUnavailable />;

  // SIMULADO: so os indicadores e as movimentacoes (ainda sem endpoint). Nao dependem do vinculo real.
  const orgs = await api.dashboard.listOrganizations();
  const exampleSchool = orgs.ok ? orgs.data[0]?.schools[0] : undefined;
  if (!orgs.ok || !exampleSchool) notFound();
  const dashboard = await api.dashboard.getDashboard(exampleSchool.id);
  if (!dashboard.ok) notFound();

  return (
    <PainelShell
      session={auth.session}
      example={{ school: exampleSchool, data: dashboard.data }}
      section={parseSection(tipo)}
    />
  );
}
