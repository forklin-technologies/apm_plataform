import type { Metadata } from "next";
import { PainelPage } from "@/components/painel/PainelPage";

export const metadata: Metadata = { title: "Alterar senha" };

export default async function Page() {
  return <PainelPage area="conta" />;
}
