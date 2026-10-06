import type { Metadata } from "next";
import { PainelPage } from "@/components/painel/PainelPage";

export const metadata: Metadata = { title: "Painel" };

type Props = { searchParams: Promise<{ tipo?: string | string[]; mes?: string | string[] }> };

export default async function Page({ searchParams }: Props) {
  const { tipo, mes } = await searchParams;
  return <PainelPage area="resumo" tipo={tipo} mes={mes} />;
}
