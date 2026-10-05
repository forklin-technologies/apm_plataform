import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { parseSession } from "@/lib/api/auth";
import type { Session } from "@/lib/api/types";
import { sessionBody } from "@/test-utils/auth-fixtures";

const auth = vi.hoisted(() => ({ current: { state: "anonymous" } as unknown, calls: 0 }));
vi.mock("@/lib/api/server", () => ({
  getServerSession: async () => {
    auth.calls += 1;
    return auth.current;
  },
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), refresh: vi.fn(), push: vi.fn() }) }));

import AcceptInvitationPage from "./page";

const TOKEN = "t".repeat(43);

async function html(token: string | string[] | undefined) {
  return renderToStaticMarkup(await AcceptInvitationPage({ searchParams: Promise.resolve({ token }) }));
}

beforeEach(() => {
  auth.current = { state: "anonymous" };
  auth.calls = 0;
});

describe("/accept-invitation no servidor", () => {
  it("sem token, com token repetido ou fora do formato: aviso e nenhum formulario, sem consultar a sessao", async () => {
    for (const token of [undefined, [TOKEN, TOKEN], "curto", `${TOKEN}<script>`]) {
      const out = await html(token);
      expect(out).toContain("incompleto ou inválido");
      expect(out).not.toContain("<form");
    }
    expect(auth.calls).toBe(0);
  });

  it("token plausivel e sem sessao: formulario de pessoa nova", async () => {
    const out = await html(TOKEN);
    expect(out).toContain("Criar conta e aceitar");
    expect(out).toContain("Nome completo");
  });

  it("token plausivel e com sessao: oferece aceitar na conta logada", async () => {
    const session = parseSession(sessionBody()) as Session;
    auth.current = { state: "authenticated", session };
    const out = await html(TOKEN);
    expect(out).toContain("Você está conectado como");
    expect(out).toContain("Pessoa de Teste");
  });
});
