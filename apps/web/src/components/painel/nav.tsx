import type { ReactNode } from "react";
import { GridIcon } from "@/components/ui/icons";
import { sectionHref, type PainelSection } from "@/lib/painel-section";
import { KIND_META } from "./kinds";

export interface NavItem {
  section: PainelSection;
  label: string;
  href: string;
  icon: ReactNode;
}

export const NAV_ITEMS: NavItem[] = [
  { section: "resumo", label: "Resumo", href: sectionHref("resumo"), icon: <GridIcon size={22} /> },
  ...(["CONTRIBUTION", "EXPENSE", "REIMBURSEMENT", "REFUND"] as const).map((kind) => ({
    section: kind,
    label: KIND_META[kind].plural,
    href: sectionHref(kind),
    icon: KIND_META[kind].icon,
  })),
];
