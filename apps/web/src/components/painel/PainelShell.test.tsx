import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { parseSession } from "@/lib/api/auth";
import type { ApiError, ApiResult, Session } from "@/lib/api/types";
import { SCHOOL_MEMBERSHIP, sessionBody } from "@/test-utils/auth-fixtures";

const router = vi.hoisted(() => ({ replace: vi.fn(), refresh: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/components/status/ApiStatusChip", () => ({ ApiStatusChip: () => null }));
vi.mock("@/components/despesas/ExpensesArea", () => ({ ExpensesArea: () => <div data-testid="expenses-area" /> }));
vi.mock("./PainelData", () => ({
  PainelData: (p: { schoolId: string; section: string; period?: string; permissions: string[] }) => (
    <div data-testid="data">{`${p.schoolId}|${p.section}|${p.period ?? ""}|${p.permissions.join(",")}`}</div>
  ),
}));

import { PainelShell } from "./PainelShell";

const session = (overrides: Record<string, unknown> = {}) => parseSession(sessionBody(overrides)) as Session;
const problem = (code: string, status: number): ApiResult<never> => ({
  ok: false,
  error: { kind: "problem", status, code } as ApiError,
});

function renderShell(s: Session = session(), props: { area?: "resumo" | "despesas" | "fechamento"; period?: string } = {}) {
  return render(<PainelShell session={s} area={props.area ?? "resumo"} period={props.period} />);
}

beforeEach(() => {
  router.replace.mockReset();
  router.refresh.mockReset();
});
afterEach(() => vi.restoreAllMocks());

describe("PainelShell com sessao real", () => {
  it("mostra o nome, o papel e o vinculo reais; vinculo da organizacao inteira nao tem painel de escola", () => {
    renderShell();
    expect(screen.getAllByText("Pessoa de Teste").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Administrador da organização").length).toBeGreaterThan(0);
    const switcher = screen.getAllByRole("button", { name: /Rede Teste/ })[0]!;
    expect(switcher).toHaveTextContent("Toda a organização");
    expect(screen.queryByTestId("data")).not.toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("Esta conta é da organização inteira");
    expect(screen.queryByText(/Protótipo/)).not.toBeInTheDocument();
  });

  it("vinculo de escola: o titulo e a escola real e os numeros vem da escola DO VINCULO ATIVO (id da sessao)", () => {
    const s = session({ active_membership: { ...SCHOOL_MEMBERSHIP, permissions: ["statement:read", "reports:read"] } });
    renderShell(s, { period: "2026-09" });
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Resumo");
    expect(screen.getAllByText(/Escola Teste/).length).toBeGreaterThan(0);
    expect(screen.getByTestId("data")).toHaveTextContent(`${SCHOOL_MEMBERSHIP.school.id}|resumo|2026-09|statement:read,reports:read`);
  });

  it("o menu muda por perfil, a partir das permissoes da sessao", () => {
    const names = () => screen.getAllByRole("link").map((l) => l.textContent);
    const staff = session({ active_membership: { ...SCHOOL_MEMBERSHIP, role: "staff", permissions: ["expenses:read_own", "expenses:submit"] } });
    const { unmount } = renderShell(staff, { area: "despesas" });
    expect(screen.getAllByRole("link", { name: "Minhas despesas" }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: "Resumo" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Fechamento" })).not.toBeInTheDocument();
    unmount();

    const treasurer = session({ active_membership: { ...SCHOOL_MEMBERSHIP, permissions: ["statement:read", "expenses:approve", "expenses:read_all", "months:close"] } });
    const t = renderShell(treasurer);
    expect(names()).toEqual(expect.arrayContaining(["Resumo", "Despesas", "Fechamento"]));
    expect(screen.queryByRole("link", { name: "Minhas despesas" })).not.toBeInTheDocument();
    t.unmount();

    const viewer = session({ active_membership: { ...SCHOOL_MEMBERSHIP, role: "viewer", permissions: ["reports:read_aggregate"] } });
    renderShell(viewer);
    expect(names()).toEqual(expect.arrayContaining(["Resumo", "Fechamento"]));
    expect(screen.queryByRole("link", { name: "Despesas" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Contribuições" })).not.toBeInTheDocument();
  });

  it("os links do menu carregam o mes escolhido (resumo e fechamento)", () => {
    renderShell(session({ active_membership: { ...SCHOOL_MEMBERSHIP, permissions: ["statement:read", "expenses:read_all"] } }), { period: "2026-09" });
    expect(screen.getAllByRole("link", { name: "Resumo" })[0]).toHaveAttribute("href", "/painel?mes=2026-09");
    expect(screen.getAllByRole("link", { name: "Fechamento" })[0]).toHaveAttribute("href", "/painel/fechamento?mes=2026-09");
    expect(screen.getAllByRole("link", { name: "Despesas" })[0]).toHaveAttribute("href", "/painel/despesas");
  });

  it("o seletor lista os vinculos reais agrupados e troca de contexto pelo id do vinculo", async () => {
    const switchContext = vi.spyOn(api.auth, "switchContext").mockResolvedValue({ ok: true, data: session() });
    renderShell();
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: /Rede Teste/ })[0]!);
    const group = screen.getByRole("group", { name: "Rede Teste" });
    expect(within(group).getByRole("button", { name: /Toda a organização/ })).toHaveAttribute("aria-current", "true");
    await user.click(within(group).getByRole("button", { name: /Escola Teste/ }));

    expect(switchContext).toHaveBeenCalledTimes(1);
    expect(switchContext).toHaveBeenCalledWith(SCHOOL_MEMBERSHIP.membership_id);
    await waitFor(() => expect(router.refresh).toHaveBeenCalled());
  });

  it("trocar para o vinculo que ja esta ativo nao chama a API", async () => {
    const switchContext = vi.spyOn(api.auth, "switchContext");
    renderShell();
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: /Rede Teste/ })[0]!);
    await user.click(within(screen.getByRole("group", { name: "Rede Teste" })).getByRole("button", { name: /Toda a organização/ }));
    expect(switchContext).not.toHaveBeenCalled();
  });

  it("context_not_allowed: mostra o texto em portugues e NAO recarrega", async () => {
    vi.spyOn(api.auth, "switchContext").mockResolvedValue(problem("context_not_allowed", 403));
    renderShell();
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: /Rede Teste/ })[0]!);
    await user.click(screen.getByRole("button", { name: /Escola Teste/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Você não tem acesso a esse vínculo");
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("sessao perdida ao trocar: volta para /login", async () => {
    vi.spyOn(api.auth, "switchContext").mockResolvedValue(problem("session_revoked", 401));
    renderShell();
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: /Rede Teste/ })[0]!);
    await user.click(screen.getByRole("button", { name: /Escola Teste/ }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
  });

  it("Sair chama POST /auth/logout e vai para /login", async () => {
    const logout = vi.spyOn(api.auth, "logout").mockResolvedValue({ ok: true, data: true });
    renderShell();
    await userEvent.setup().click(screen.getAllByRole("button", { name: "Sair" })[0]!);
    expect(logout).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
  });

  it("Sair que falha (csrf, rede) avisa e fica na pagina: a sessao pode continuar viva", async () => {
    vi.spyOn(api.auth, "logout").mockResolvedValue(problem("csrf_failed", 403));
    renderShell();
    await userEvent.setup().click(screen.getAllByRole("button", { name: "Sair" })[0]!);
    expect(await screen.findByRole("alert")).toHaveTextContent("Recarregue a página");
    expect(router.replace).not.toHaveBeenCalled();
    expect(screen.getAllByRole("button", { name: "Sair" })[0]).toBeEnabled();
  });
});

describe("sessao sem vinculo ativo (varios vinculos, nenhum escolhido)", () => {
  it("mostra a escolha e nenhum dado do painel; escolher chama POST /auth/context", async () => {
    const switchContext = vi.spyOn(api.auth, "switchContext").mockResolvedValue({ ok: true, data: session() });
    renderShell(session({ active_membership: null }));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Onde você vai atuar?");
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(screen.queryByText("Movimentações recentes")).not.toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: /Escola Teste/ }));
    expect(switchContext).toHaveBeenCalledWith(SCHOOL_MEMBERSHIP.membership_id);
    await waitFor(() => expect(router.refresh).toHaveBeenCalled());
  });

  it("conta sem nenhum vinculo ativo: aviso claro e so a opcao de sair", () => {
    renderShell(session({ active_membership: null, memberships: [] }));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Sem vínculo ativo");
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sair" })).toBeInTheDocument();
  });

  it("os dois vinculos aparecem com o papel de cada um", () => {
    renderShell(session({ active_membership: null }));
    expect(screen.getByRole("button", { name: /Toda a organização/ })).toHaveTextContent("Administrador da organização");
    expect(screen.getByRole("button", { name: /Escola Teste/ })).toHaveTextContent("Tesouraria");
  });
});
