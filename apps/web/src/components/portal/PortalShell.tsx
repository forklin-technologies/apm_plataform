import Link from "next/link";
import type { PublicSchool } from "@/lib/api/types";
import { SchoolMonogram } from "./SchoolMonogram";
import { SchoolThemeScope } from "./SchoolThemeScope";

/** Moldura do portal publico. A cor da escola entra aqui, em runtime, e so aqui. */
export function PortalShell({ school, children }: { school: PublicSchool; children: React.ReactNode }) {
  return (
    <SchoolThemeScope accentColor={school.accentColor} className="min-h-dvh">
      <header className="bar-material sticky top-0 z-30 border-b border-line">
        <div className="mx-auto flex min-h-16 w-full max-w-5xl items-center gap-3 px-5 sm:px-8">
          <SchoolMonogram name={school.name} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-headline text-ink">{school.name}</p>
            <p className="truncate text-foot text-ink-2">{school.apmName}</p>
          </div>
          <Link
            href="/"
            className="inline-flex min-h-11 items-center rounded-full px-3 text-sub font-semibold text-accent-ink hover:bg-neutral-soft"
          >
            Início
          </Link>
        </div>
      </header>
      <main id="conteudo" tabIndex={-1} className="outline-none mx-auto w-full max-w-5xl px-5 pb-16 pt-6 sm:px-8 sm:pt-10">
        {children}
      </main>
    </SchoolThemeScope>
  );
}
