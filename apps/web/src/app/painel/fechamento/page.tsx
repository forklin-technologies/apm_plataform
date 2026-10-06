import type { Metadata } from "next";
import { PainelPage } from "@/components/painel/PainelPage";

export const metadata: Metadata = { title: "Fechamento" };

type Props = { searchParams: Promise<{ mes?: string | string[] }> };

export default async function Page({ searchParams }: Props) {
  const { mes } = await searchParams;
  return <PainelPage area="fechamento" mes={mes} />;
}
