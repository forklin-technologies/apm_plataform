import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { PainelShell } from "@/components/painel/PainelShell";
import { SessionUnavailable } from "@/components/painel/SessionUnavailable";
import { getServerSession } from "@/lib/api/server";
import { parsePeriod, parseSection } from "@/lib/painel-section";

export const metadata: Metadata = { title: "Painel" };

type Props = { searchParams: Promise<{ tipo?: string | string[]; mes?: string | string[] }> };

export default async function PainelPage({ searchParams }: Props) {
  const { tipo, mes } = await searchParams;

  // O painel exige sessao. Sem ela (ou expirada, ou revogada), volta para o login.
  const auth = await getServerSession();
  if (auth.state === "anonymous") redirect("/login");
  if (auth.state === "unavailable") return <SessionUnavailable />;

  // Os numeros (resumo, extrato, pendencias) sao buscados no navegador, na API real, para a escola do
  // vinculo ativo da sessao. O servidor so garante a sessao.
  return <PainelShell session={auth.session} section={parseSection(tipo)} period={parsePeriod(mes)} />;
}
