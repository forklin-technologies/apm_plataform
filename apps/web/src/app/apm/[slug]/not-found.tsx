import type { Metadata } from "next";
import { NotFoundView } from "@/components/ui/NotFoundView";

export const metadata: Metadata = { title: "Escola não encontrada" };

export default function SchoolNotFound() {
  return (
    <NotFoundView
      title="Não encontramos esta página da APM"
      text="Confira o endereço que a escola enviou. Se o link veio de uma mensagem, peça à tesouraria da APM para enviar de novo."
    />
  );
}
