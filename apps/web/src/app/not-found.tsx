import type { Metadata } from "next";
import { NotFoundView } from "@/components/ui/NotFoundView";

export const metadata: Metadata = { title: "Página não encontrada" };

export default function NotFound() {
  return (
    <NotFoundView
      title="Não encontramos esta página"
      text="O endereço pode ter mudado ou estar digitado de outro jeito. Volte ao início para seguir de onde parou."
    />
  );
}
