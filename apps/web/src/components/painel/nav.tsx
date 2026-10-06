import type { ReactNode } from "react";
import { GridIcon, ReceiptIcon, ReportIcon } from "@/components/ui/icons";
import { areaHref, type Areas, type PainelArea } from "@/lib/permissions";

export interface NavItem {
  area: PainelArea;
  label: string;
  href: string;
  icon: ReactNode;
}

/**
 * Menu por perfil, a partir das permissoes da sessao (nunca de um papel fixo no cliente):
 * professora = "Minhas despesas"; gestao = Resumo, Despesas e Fechamento; viewer = Resumo e Fechamento (leitura).
 */
export function navFor(areas: Areas, period?: string): NavItem[] {
  const items: NavItem[] = [];
  if (areas.resumo) items.push({ area: "resumo", label: "Resumo", href: areaHref("resumo", { period }), icon: <GridIcon size={22} /> });
  if (areas.manageExpenses || areas.myExpenses) {
    items.push({
      area: "despesas",
      label: areas.manageExpenses ? "Despesas" : "Minhas despesas",
      href: areaHref("despesas"),
      icon: <ReceiptIcon size={22} />,
    });
  }
  if (areas.fechamento) items.push({ area: "fechamento", label: "Fechamento", href: areaHref("fechamento", { period }), icon: <ReportIcon size={22} /> });
  return items;
}
