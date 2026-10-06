import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { ApiResult } from "@/lib/api/types";
import { SCHOOL_ID, SUMMARY, entry, page, pending } from "@/test-utils/statement-fixtures";
import { PainelData } from "./PainelData";

const TREASURER = ["statement:read", "contributions:record_cash", "reports:read"];
const VIEWER = ["reports:read_aggregate"];
const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, data });
const fail = (status: number, code: string): ApiResult<never> => ({ ok: false, error: { kind: "problem", status, code } });

function setup(props: Partial<React.ComponentProps<typeof PainelData>> = {}) {
  const onPeriodChange = vi.fn();
  render(<PainelData schoolId={SCHOOL_ID} section="resumo" period={undefined} permissions={TREASURER} onPeriodChange={onPeriodChange} {...props} />);
  return { onPeriodChange };
}

afterEach(() => vi.restoreAllMocks());

describe("PainelData: resumo real", () => {
  it("busca resumo e pendencias da escola do vinculo; mostra os DOIS saldos, o principal e o secundario rotulado", async () => {
    const summary = vi.spyOn(api.statement, "summary").mockResolvedValue(ok(SUMMARY));
    const pend = vi.spyOn(api.statement, "pending").mockResolvedValue(ok(page([pending(), pending({ transactionId: "p2", referenceCode: 7, kind: "CONTRIBUTION", direction: "IN", status: "REVIEW_REQUIRED", section: "REVIEW", amountCents: 4000 })])));
    setup();
    expect(await screen.findByText(/Saldo em caixa em outubro de 2026/)).toBeInTheDocument();
    expect(summary).toHaveBeenCalledWith(SCHOOL_ID, undefined);
    expect(pend).toHaveBeenCalledWith(SCHOOL_ID, { limit: 100 });
    // principal (grande) e secundario (rotulado)
    expect(screen.getByText("R$ 684,10")).toBeInTheDocument();
    expect(screen.getByText(/Após reembolsos pendentes:/)).toHaveTextContent("R$ 639,10");
    expect(screen.getByText(/menos R\$ 45,00 de reembolsos ainda a pagar/)).toBeInTheDocument();
    // pendencias fora do saldo, agrupadas
    expect(await screen.findByRole("heading", { name: "Pendências (fora do saldo)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "A pagar" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Pagamento em análise" })).toBeInTheDocument();
    expect(screen.getByText("Reembolso nº 16")).toBeInTheDocument();
    expect(screen.getByText(/Ainda não é contribuição confirmada/)).toBeInTheDocument();
  });

  it("o mes da URL vai para a API e o seletor de mes avisa o painel", async () => {
    const summary = vi.spyOn(api.statement, "summary").mockResolvedValue(ok(SUMMARY));
    vi.spyOn(api.statement, "pending").mockResolvedValue(ok(page([])));
    const { onPeriodChange } = setup({ period: "2026-09" });
    await screen.findByText(/Saldo em caixa/);
    expect(summary).toHaveBeenCalledWith(SCHOOL_ID, "2026-09");
    await userEvent.setup().selectOptions(screen.getByLabelText("Mês"), "2026-08");
    expect(onPeriodChange).toHaveBeenCalledWith("2026-08");
    expect(await screen.findByText("Nenhuma pendência")).toBeInTheDocument();
  });

  it("viewer: so o resumo; NAO chama lancamentos nem pendencias (nenhum 403 a tratar) e explica", async () => {
    vi.spyOn(api.statement, "summary").mockResolvedValue(ok(SUMMARY));
    const pend = vi.spyOn(api.statement, "pending");
    const entries = vi.spyOn(api.statement, "entries");
    setup({ permissions: VIEWER });
    expect(await screen.findByText(/Saldo em caixa/)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("vê só os totais do mês");
    expect(pend).not.toHaveBeenCalled();
    expect(entries).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /Registrar contribuição em dinheiro/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Baixar PDF das contribuições do mês" })).toBeInTheDocument();
  });

  it("uma rota que responde 403 vira aviso e a tela continua de pe", async () => {
    vi.spyOn(api.statement, "summary").mockResolvedValue(ok(SUMMARY));
    vi.spyOn(api.statement, "pending").mockResolvedValue(fail(403, "permission_denied"));
    setup();
    expect(await screen.findByText(/Saldo em caixa/)).toBeInTheDocument();
    expect(await screen.findByText(/não vê as pendências/)).toBeInTheDocument();
  });

  it("resumo 403: aviso; erro de rede no resumo: texto em portugues", async () => {
    vi.spyOn(api.statement, "summary").mockResolvedValue(fail(403, "permission_denied"));
    vi.spyOn(api.statement, "pending").mockResolvedValue(ok(page([])));
    const { unmount } = render(<PainelData schoolId={SCHOOL_ID} section="resumo" period={undefined} permissions={TREASURER} onPeriodChange={() => {}} />);
    expect(await screen.findByText(/não tem acesso aos números desta escola/)).toBeInTheDocument();
    unmount();
    vi.spyOn(api.statement, "summary").mockResolvedValue({ ok: false, error: { kind: "network" } });
    setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível falar com o servidor");
  });

  it("link do PDF aponta para o mes mostrado", async () => {
    vi.spyOn(api.statement, "summary").mockResolvedValue(ok(SUMMARY));
    vi.spyOn(api.statement, "pending").mockResolvedValue(ok(page([])));
    setup();
    const link = await screen.findByRole("link", { name: "Baixar PDF das contribuições do mês" });
    expect(link).toHaveAttribute("href", `/api/v1/schools/${SCHOOL_ID}/reports/contributions.pdf?period=2026-10`);
  });

  it("registrar contribuicao em dinheiro: so com contributions:record_cash", async () => {
    vi.spyOn(api.statement, "summary").mockResolvedValue(ok(SUMMARY));
    vi.spyOn(api.statement, "pending").mockResolvedValue(ok(page([])));
    setup({ permissions: ["statement:read", "reports:read"] });
    await screen.findByText(/Saldo em caixa/);
    expect(screen.queryByRole("button", { name: /Registrar contribuição em dinheiro/ })).not.toBeInTheDocument();
  });

  it("depois de registrar uma contribuicao em dinheiro, os numeros sao recarregados", async () => {
    const summary = vi.spyOn(api.statement, "summary").mockResolvedValue(ok(SUMMARY));
    vi.spyOn(api.statement, "pending").mockResolvedValue(ok(page([])));
    vi.spyOn(api.statement, "recordCash").mockResolvedValue(ok({ id: "i", referenceCode: "APM-000042", status: "PAID", amountCents: 5000, method: "CASH" }));
    setup();
    await screen.findByText(/Saldo em caixa/);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Registrar contribuição em dinheiro" }));
    await user.type(screen.getByLabelText("Valor"), "5000");
    await user.click(screen.getByRole("button", { name: "Registrar" }));
    expect(await screen.findByText(/APM-000042 registrada: R\$ 50,00/)).toBeInTheDocument();
    await waitFor(() => expect(summary).toHaveBeenCalledTimes(2));
  });
});

describe("PainelData: lancamentos por tipo", () => {
  it("lista os lancamentos do tipo com sinal, valor, contraparte, data local e status em portugues", async () => {
    vi.spyOn(api.statement, "summary").mockResolvedValue(ok(SUMMARY));
    const entries = vi.spyOn(api.statement, "entries").mockResolvedValue(
      ok(page([entry(), entry({ transactionId: "t2", referenceCode: 25, kind: "CONTRIBUTION", originLabel: null, amountCents: 2000, runningBalanceCents: 44710 })])),
    );
    setup({ section: "CONTRIBUTION", period: "2026-10" });
    const list = await screen.findByRole("list", { name: "Contribuições" });
    expect(entries).toHaveBeenCalledWith(SCHOOL_ID, { period: "2026-10", kind: "CONTRIBUTION", limit: 50 });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText(/Maria Teste/)).toBeInTheDocument();
    expect(within(list).getByText(/Contribuinte anônimo/)).toBeInTheDocument();
    expect(within(list).getAllByText("Pago")).toHaveLength(2);
    expect(within(list).getAllByText(/06 out/)).toHaveLength(2);
    expect(within(list).getAllByText(/Entrada de/)).toHaveLength(2);
    expect(within(list).getAllByText(/saldo R\$/).length).toBe(2);
  });

  it("carregar mais: segue o cursor e acrescenta; o botao some na ultima pagina", async () => {
    vi.spyOn(api.statement, "summary").mockResolvedValue(ok(SUMMARY));
    const entries = vi
      .spyOn(api.statement, "entries")
      .mockResolvedValueOnce(ok(page([entry()], "cursor-2")))
      .mockResolvedValueOnce(ok(page([entry({ transactionId: "t9", referenceCode: 99 })], null)));
    setup({ section: "CONTRIBUTION" });
    const list = await screen.findByRole("list", { name: "Contribuições" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    await userEvent.setup().click(screen.getByRole("button", { name: "Carregar mais" }));
    await waitFor(() => expect(within(screen.getByRole("list", { name: "Contribuições" })).getAllByRole("listitem")).toHaveLength(2));
    expect(entries).toHaveBeenLastCalledWith(SCHOOL_ID, { period: undefined, kind: "CONTRIBUTION", limit: 50, cursor: "cursor-2" });
    expect(screen.queryByRole("button", { name: "Carregar mais" })).not.toBeInTheDocument();
  });

  it("sem lancamentos: estado vazio em portugues", async () => {
    vi.spyOn(api.statement, "summary").mockResolvedValue(ok(SUMMARY));
    vi.spyOn(api.statement, "entries").mockResolvedValue(ok(page([])));
    setup({ section: "EXPENSE" });
    expect(await screen.findByRole("heading", { name: "Nenhuma despesa neste mês" })).toBeInTheDocument();
  });

  it("403 nos lancamentos: aviso, sem quebrar; falha ao carregar mais: aviso e o botao continua", async () => {
    vi.spyOn(api.statement, "summary").mockResolvedValue(ok(SUMMARY));
    vi.spyOn(api.statement, "entries").mockResolvedValueOnce(fail(403, "permission_denied"));
    const { unmount } = render(<PainelData schoolId={SCHOOL_ID} section="CONTRIBUTION" period={undefined} permissions={TREASURER} onPeriodChange={() => {}} />);
    expect(await screen.findByText(/não vê os lançamentos/)).toBeInTheDocument();
    unmount();

    vi.spyOn(api.statement, "entries")
      .mockResolvedValueOnce(ok(page([entry()], "c2")))
      .mockResolvedValueOnce({ ok: false, error: { kind: "network" } });
    setup({ section: "CONTRIBUTION" });
    await userEvent.setup().click(await screen.findByRole("button", { name: "Carregar mais" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível falar com o servidor");
    expect(screen.getByRole("button", { name: "Carregar mais" })).toBeEnabled();
  });
});
