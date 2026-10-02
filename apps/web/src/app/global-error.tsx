"use client";

import { ErrorView } from "@/components/ui/ErrorView";
import "./globals.css";

/** Erro no proprio layout raiz: precisa trazer <html> e <body>, e o idioma certo. */
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="pt-BR">
      <head>
        <title>Algo deu errado · APM Digital</title>
        <meta name="robots" content="noindex, nofollow" />
      </head>
      <body>
        <ErrorView reset={reset} digest={error.digest} />
      </body>
    </html>
  );
}
