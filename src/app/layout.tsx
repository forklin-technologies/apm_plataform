import type { ReactNode } from "react";
import type { Viewport } from "next";
import "./globals.css";

export const metadata = { title: "Contribua com a APM", description: "Contribuições e campanhas da APM" };
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>
        <a className="pular-conteudo" href="#conteudo">Pular para o conteúdo</a>
        {children}
      </body>
    </html>
  );
}
