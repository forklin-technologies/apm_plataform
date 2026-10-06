import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { ApiResult, ExpenseSummary, Page } from "@/lib/api/types";
import { AUTHOR_ID, CATEGORY, MANAGER_ID, detail, summary } from "@/test-utils/expense-fixtures";
import { SCHOOL_ID, page } from "@/test-utils/statement-fixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), refresh: vi.fn(), push: vi.fn() }) }));

import { ExpensesArea } from "./ExpensesArea";
import { ExpensesBoard } from "./ExpensesBoard";

const ok = (items: ExpenseSummary[], next: string | null = null): ApiResult<Page<ExpenseSummary>> => ({ ok: true, data: page(items, next) });
const STAFF = ["expenses:read_own", "expenses:submit"];
const TREASURER = ["statement:read", "expenses:approve", "expenses:read_all", "reimbursements:register"];
const SCHOOL_ADMIN = [...TREASURER, "expenses:read_own", "expenses:submit"];

afterEach(() => vi.restoreAllMocks());

function stubCategories() {
  vi.spyOn(api.expenses, "categories").mockResolvedValue({ ok: true, data: [CATEGORY] });
}

describe("Minhas despesas (professora)", () => {
  it("lista as proprias despesas com o status em portugues; abre o detalhe sob demanda", async () => {
    stubCategories();
    const list = vi.spyOn(api.expenses, "list").mockResolvedValue(ok([
      summary({ id: "e1", referenceCode: 1, status: "DRAFT", description: "Rascunho A" }),
      summary({ id: "e2", referenceCode: 2, status: "CORRECTION_REQUESTED", description: "Corrigir B" }),
      summary({ id: "e3", referenceCode: 3, status: "APPROVED", paidBy: "COLLABORATOR", description: "Aprovada C", approvedAmountCents: 17500 }),
      summary({ id: "e4", referenceCode: 4, status: "PAID", description: "Paga D" }),
      summary({ id: "e5", referenceCode: 5, status: "REJECTED", description: "Recusada E" }),
    ]));
    const get = vi.spyOn(api.expenses, "get").mockResolvedValue({ ok: true, data: detail({ id: "e2", status: "CORRECTION_REQUESTED", correctionReason: "Falta o CNPJ" }) });
    render(<ExpensesBoard schoolId={SCHOOL_ID} userId={AUTHOR_ID} permissions={STAFF} scope="mine" />);

    const items = await screen.findByRole("list", { name: "Minhas despesas" });
    expect(list).toHaveBeenCalledWith(SCHOOL_ID, { status: undefined, limit: 50 });
    for (const label of ["Rascunho", "Correção pedida", "Reembolso pendente", "Paga", "Recusada"]) {
      expect(within(items).getByText(label)).toBeInTheDocument();
    }
    expect(within(items).getByText(/pedido: R\$\s200,00/)).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();

    await userEvent.setup().click(screen.getByRole("button", { name: "Abrir detalhes da despesa nº 2" }));
    expect(await screen.findByText(/A gestão pediu correção:/)).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith(SCHOOL_ID, "e2");
  });

  it("vazio, 403 e 'Nova despesa'", async () => {
    stubCategories();
    vi.spyOn(api.expenses, "list").mockResolvedValue(ok([]));
    const { unmount } = render(<ExpensesBoard schoolId={SCHOOL_ID} userId={AUTHOR_ID} permissions={STAFF} scope="mine" />);
    expect(await screen.findByRole("heading", { name: "Você ainda não enviou despesas" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Nova despesa" }));
    expect(screen.getByRole("heading", { name: "Nova despesa" })).toBeInTheDocument();
    unmount();

    vi.spyOn(api.expenses, "list").mockResolvedValue({ ok: false, error: { kind: "problem", status: 403, code: "permission_denied" } });
    render(<ExpensesBoard schoolId={SCHOOL_ID} userId={AUTHOR_ID} permissions={STAFF} scope="mine" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("O seu perfil não vê as despesas");
  });

  it("quem le a escola toda (diretor) ve em 'minhas' so as proprias", async () => {
    stubCategories();
    vi.spyOn(api.expenses, "list").mockResolvedValue(ok([summary({ id: "m", description: "Minha", submittedByUserId: AUTHOR_ID }), summary({ id: "o", description: "De outra pessoa", submittedByUserId: MANAGER_ID })]));
    render(<ExpensesBoard schoolId={SCHOOL_ID} userId={AUTHOR_ID} permissions={SCHOOL_ADMIN} scope="mine" />);
    expect(await screen.findByText("Minha")).toBeInTheDocument();
    expect(screen.queryByText("De outra pessoa")).not.toBeInTheDocument();
  });
});

describe("Fila da gestao", () => {
  it("comeca em 'Aguardando aprovacao' (filtro na API) e troca de filtro", async () => {
    const list = vi.spyOn(api.expenses, "list").mockResolvedValue(ok([summary({ description: "Na fila" })]));
    render(<ExpensesBoard schoolId={SCHOOL_ID} userId={MANAGER_ID} permissions={TREASURER} scope="queue" />);
    expect(await screen.findByText("Na fila")).toBeInTheDocument();
    expect(list).toHaveBeenLastCalledWith(SCHOOL_ID, { status: "SUBMITTED", limit: 50 });
    expect(screen.getByRole("button", { name: "Aguardando aprovação" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "Nova despesa" })).not.toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "Aprovadas (a pagar)" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(SCHOOL_ID, { status: "APPROVED", limit: 50 }));
    await userEvent.setup().click(screen.getByRole("button", { name: "Todas" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(SCHOOL_ID, { status: undefined, limit: 50 }));
  });

  it("carregar mais segue o cursor e acrescenta", async () => {
    const list = vi.spyOn(api.expenses, "list")
      .mockResolvedValueOnce(ok([summary({ id: "a", description: "Primeira" })], "c2"))
      .mockResolvedValueOnce(ok([summary({ id: "b", description: "Segunda" })], null));
    render(<ExpensesBoard schoolId={SCHOOL_ID} userId={MANAGER_ID} permissions={TREASURER} scope="queue" />);
    await screen.findByText("Primeira");
    await userEvent.setup().click(screen.getByRole("button", { name: "Carregar mais" }));
    expect(await screen.findByText("Segunda")).toBeInTheDocument();
    expect(list).toHaveBeenLastCalledWith(SCHOOL_ID, { status: "SUBMITTED", limit: 50, cursor: "c2" });
    expect(screen.queryByRole("button", { name: "Carregar mais" })).not.toBeInTheDocument();
  });

  it("depois de uma acao a despesa pode sair do filtro: a mensagem fica na lista (nao se perde com o painel)", async () => {
    const list = vi.spyOn(api.expenses, "list")
      .mockResolvedValueOnce(ok([summary({ description: "Vai sair da fila" })]))
      .mockResolvedValue(ok([]));
    vi.spyOn(api.expenses, "get").mockResolvedValue({ ok: true, data: detail() });
    vi.spyOn(api.expenses, "approve").mockResolvedValue({ ok: true, data: detail({ status: "APPROVED" }) });
    render(<ExpensesBoard schoolId={SCHOOL_ID} userId={MANAGER_ID} permissions={TREASURER} scope="queue" />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Abrir detalhes/ }));
    await user.click(await screen.findByRole("button", { name: "Aprovar" }));
    await user.click(screen.getByRole("button", { name: "Aprovar" }));
    expect(await screen.findByText("Despesa aprovada.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Vai sair da fila")).not.toBeInTheDocument());
    expect(screen.getByText("Despesa aprovada.")).toBeInTheDocument();
    expect(list.mock.calls.length).toBeGreaterThan(1);
  });

  it("marca como 'sua' a despesa que a pessoa enviou e abre o detalhe da fila com as acoes", async () => {
    vi.spyOn(api.expenses, "list").mockResolvedValue(ok([summary({ description: "De colega" })]));
    vi.spyOn(api.expenses, "get").mockResolvedValue({ ok: true, data: detail() });
    render(<ExpensesBoard schoolId={SCHOOL_ID} userId={MANAGER_ID} permissions={TREASURER} scope="queue" />);
    await userEvent.setup().click(await screen.findByRole("button", { name: /Abrir detalhes/ }));
    expect(await screen.findByRole("button", { name: "Aprovar" })).toBeInTheDocument();
  });
});

describe("ExpensesArea (por perfil)", () => {
  it("professora: so 'Minhas despesas'; tesoureira: so a fila; diretor: abas; viewer: sem acesso", async () => {
    stubCategories();
    vi.spyOn(api.expenses, "list").mockResolvedValue(ok([]));
    const staff = render(<ExpensesArea schoolId={SCHOOL_ID} userId={AUTHOR_ID} permissions={STAFF} tab="fila" />);
    expect(await screen.findByRole("heading", { name: "Você ainda não enviou despesas" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Despesas" })).not.toBeInTheDocument();
    staff.unmount();

    const treasurer = render(<ExpensesArea schoolId={SCHOOL_ID} userId={MANAGER_ID} permissions={TREASURER} tab="fila" />);
    expect(await screen.findByRole("group", { name: "Filtrar por situação" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nova despesa" })).not.toBeInTheDocument();
    treasurer.unmount();

    const admin = render(<ExpensesArea schoolId={SCHOOL_ID} userId={MANAGER_ID} permissions={SCHOOL_ADMIN} tab="minhas" />);
    const tabs = screen.getByRole("navigation", { name: "Despesas" });
    expect(within(tabs).getByRole("link", { name: "Minhas despesas" })).toHaveAttribute("href", "/painel/despesas?aba=minhas");
    expect(within(tabs).getByRole("link", { name: "Fila de análise" })).toHaveAttribute("href", "/painel/despesas");
    expect(await screen.findByRole("button", { name: "Nova despesa" })).toBeInTheDocument();
    admin.unmount();

    render(<ExpensesArea schoolId={SCHOOL_ID} userId={MANAGER_ID} permissions={["reports:read_aggregate"]} tab="fila" />);
    expect(screen.getByRole("note")).toHaveTextContent("não tem acesso às despesas");
  });
});
