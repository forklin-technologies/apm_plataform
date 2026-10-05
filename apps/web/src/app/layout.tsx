import type { Metadata, Viewport } from "next";
import { connection } from "next/server";
import { MotionGate } from "@/components/ui/MotionGate";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "APM Digital", template: "%s · APM Digital" },
  description:
    "Contribuições da APM por Pix, com comprovante, e a prestação de contas da tesouraria no mesmo lugar.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f3f4f6" },
    { media: "(prefers-color-scheme: dark)", color: "#0f1115" },
  ],
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // A CSP usa um nonce por requisicao (src/proxy.ts): as paginas precisam ser dinamicas.
  await connection();
  return (
    <html lang="pt-BR">
      <body>
        <a
          href="#conteudo"
          className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-ink focus:px-4 focus:py-3 focus:text-sub focus:font-semibold focus:text-bg"
        >
          Ir para o conteúdo
        </a>
        <MotionGate />
        {children}
      </body>
    </html>
  );
}
