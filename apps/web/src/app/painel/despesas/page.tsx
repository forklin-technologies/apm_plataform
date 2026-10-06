import type { Metadata } from "next";
import { PainelPage } from "@/components/painel/PainelPage";

export const metadata: Metadata = { title: "Despesas" };

type Props = { searchParams: Promise<{ aba?: string | string[] }> };

export default async function Page({ searchParams }: Props) {
  const { aba } = await searchParams;
  return <PainelPage area="despesas" aba={aba} />;
}
