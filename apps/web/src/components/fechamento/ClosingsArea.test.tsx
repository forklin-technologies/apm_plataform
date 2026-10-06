import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { parseClosing } from "@/lib/api/closings";
import type { ApiError, ApiResult, Closing, Page } from "@/lib/api/types";
import { closingBody } from "@/test-utils/expense-fixtures";
import { SCHOOL_ID, page } from "@/test-utils/statement-fixtures";

const saveBlob = vi.hoisted(() => vi.fn());
vi.mock("@/lib/download", () => ({ saveBlob }));

import { ClosingsArea } from "./ClosingsArea";

const TREASURER = ["statement:read", "months:close", "reports:read", "expenses:read_all"];
const ADMIN = [...TREASURER, "months:reopen", "reports:read_aggregate"];
const VIEWER = ["reports:read_aggregate"];
const closing = (o: Record<string, unknown> = {}) => parseClosing(closingBody(o)) as Closing;
const ok = (items: Closing[]): ApiResult<Page<Closing>> => ({ ok: true, data: page(items) });
const problem = (status: number, code: string, extra: Partial<ApiError> = {}): ApiResult<never> => ({ ok: false, error: { kind: "problem", status, code, ...extra } });

const JAN = closing({ id: "jan", period: "2026-01" });
const DEC = closing({ id: "dec", period: "2025-12", reopened_at: "2026-10-06T16:00:00Z", reopen_reason: "Erro de lançamento" });

beforeEach(() => saveBlob.mockReset());
afterEach(() => vi.restoreAllMocks());

function setup(permissions: string[], items: Closing[] = [JAN, DEC]) {
  vi.spyOn(api.closings, "list").mockResolvedValue(ok(items));
  render(<ClosingsArea schoolId={SCHOOL_ID} permissions={permissions} />);
  return userEvent.setup();
}

describe("lista e detalhe", () => {
  it("lista por mes (mais recente primeiro) com o estado: fechado ou reaberto", async () => {
    setup(TREASURER);
    const list = await screen.findByRole("list", { name: "Fechamentos mensais" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("janeiro de 2026");
    expect(rows[0]).toHaveTextContent("Fechado");
    expect(rows[1]).toHaveTextContent("dezembro de 2025");
    expect(rows[1]).toHaveTextContent("Reaberto");
  });

  it("detalhe: os DOIS saldos, entradas e saidas, diferenca do banco e o codigo de verificacao", async () => {
    const user = setup(TREASURER);
    await user.click(await screen.findByRole("button", { name: "Abrir o fechamento de janeiro de 2026" }));
    const detail = screen.getByTestId("closing-detail");
    expect(detail).toHaveTextContent("Saldo inicial");
    expect(detail).toHaveTextContent("R$ 100,00");
    expect(detail).toHaveTextContent("Saldo em caixa no fechamento");
    expect(detail).toHaveTextContent("R$ 345,00");
    expect(detail).toHaveTextContent("Após reembolsos pendentes");
    expect(detail).toHaveTextContent("R$ 300,00");
    expect(detail).toHaveTextContent("Diferença (banco menos caixa): R$ 5,00");
    expect(detail).toHaveTextContent("98da742317c7d693");
  });

  it("fechamento reaberto: sem PDF e sem verificar, e explica", async () => {
    const user = setup(ADMIN, [DEC]);
    await user.click(await screen.findByRole("button", { name: "Abrir o fechamento de dezembro de 2025" }));
    expect(screen.getByRole("note")).toHaveTextContent("não tem PDF nem verificação");
    expect(screen.getByRole("note")).toHaveTextContent("Erro de lançamento");
    for (const name of ["Baixar PDF do extrato mensal", "Verificar", "Reabrir mês"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
  });

  it("vazio e 403", async () => {
    setup(TREASURER, []);
    expect(await screen.findByRole("heading", { name: "Nenhum mês fechado ainda" })).toBeInTheDocument();
  });

  it("403 na lista: aviso, sem quebrar", async () => {
    vi.spyOn(api.closings, "list").mockResolvedValue(problem(403, "permission_denied"));
    render(<ClosingsArea schoolId={SCHOOL_ID} permissions={VIEWER} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("não vê os fechamentos");
  });
});

describe("PDF e verificacao", () => {
  it("baixa o PDF de um fechamento ativo pela API", async () => {
    const pdf = vi.spyOn(api.closings, "pdf").mockResolvedValue({ ok: true, data: { blob: new Blob(["%PDF-1.4"]), contentType: "application/pdf" } });
    const user = setup(TREASURER, [JAN]);
    await user.click(await screen.findByRole("button", { name: "Abrir o fechamento de janeiro de 2026" }));
    await user.click(screen.getByRole("button", { name: "Baixar PDF do extrato mensal" }));
    expect(pdf).toHaveBeenCalledWith(SCHOOL_ID, "jan");
    expect(saveBlob).toHaveBeenCalledWith(expect.any(Blob), "extrato-mensal-2026-01.pdf");
    expect(await screen.findByRole("status")).toHaveTextContent("PDF do extrato mensal gerado.");
  });

  it("409 closing_reopened ao baixar (estado que mudou): mostra que nao ha PDF; relatorio que nao bate", async () => {
    const pdf = vi.spyOn(api.closings, "pdf").mockResolvedValueOnce(problem(409, "closing_reopened"));
    const user = setup(TREASURER, [JAN]);
    await user.click(await screen.findByRole("button", { name: "Abrir o fechamento de janeiro de 2026" }));
    await user.click(screen.getByRole("button", { name: "Baixar PDF do extrato mensal" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("reaberto e não tem PDF");
    pdf.mockResolvedValueOnce(problem(409, "closing_mismatch"));
    await user.click(screen.getByRole("button", { name: "Baixar PDF do extrato mensal" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("não bate com os números");
    expect(saveBlob).not.toHaveBeenCalled();
  });

  it("verificar: confere e avisa quando nao confere", async () => {
    const verify = vi.spyOn(api.closings, "verify").mockResolvedValueOnce({ ok: true, data: { closingId: "jan", verified: true, entriesHash: "a".repeat(64) } });
    const user = setup(TREASURER, [JAN]);
    await user.click(await screen.findByRole("button", { name: "Abrir o fechamento de janeiro de 2026" }));
    await user.click(screen.getByRole("button", { name: "Verificar" }));
    expect(verify).toHaveBeenCalledWith(SCHOOL_ID, "jan");
    expect(await screen.findByRole("status")).toHaveTextContent("Verificado");
    verify.mockResolvedValueOnce({ ok: true, data: { closingId: "jan", verified: false, entriesHash: "a".repeat(64) } });
    await user.click(screen.getByRole("button", { name: "Verificar" }));
    expect(await screen.findByText(/Não confere/)).toBeInTheDocument();
  });
});

describe("fechar mes", () => {
  it("fecha o mes escolhido com o saldo do banco em centavos e mostra o fechamento novo", async () => {
    const create = vi.spyOn(api.closings, "create").mockResolvedValue({ ok: true, data: closing({ id: "novo", period: "2026-02" }) });
    const user = setup(TREASURER, []);
    await screen.findByRole("heading", { name: "Nenhum mês fechado ainda" });
    await user.selectOptions(screen.getByLabelText("Mês a fechar"), "2026-02");
    await user.type(screen.getByLabelText(/Saldo informado pelo banco/), "35000");
    await user.click(screen.getByRole("button", { name: "Fechar mês" }));
    expect(create).toHaveBeenCalledWith(SCHOOL_ID, { period: "2026-02", bankBalanceReportedCents: 35000 });
    expect(await screen.findByText("Mês fechado: fevereiro de 2026.")).toBeInTheDocument();
  });

  it("saldo do banco e opcional: sem ele, so o mes", async () => {
    const create = vi.spyOn(api.closings, "create").mockResolvedValue({ ok: true, data: closing() });
    const user = setup(TREASURER, []);
    await screen.findByLabelText("Mês a fechar");
    await user.click(screen.getByRole("button", { name: "Fechar mês" }));
    expect(create).toHaveBeenCalledWith(SCHOOL_ID, { period: expect.stringMatching(/^\d{4}-\d{2}$/) });
  });

  it("o formulario sugere o mes seguinte ao ultimo fechamento ativo", async () => {
    setup(TREASURER, [JAN]);
    await screen.findByLabelText("Mês a fechar");
    expect(screen.getByLabelText("Mês a fechar")).toHaveValue("2026-02");
  });

  it("fora de ordem (409): mostra o proximo mes; mes que nao terminou; erro nao quebra a tela", async () => {
    const create = vi.spyOn(api.closings, "create").mockResolvedValueOnce(problem(409, "closing_out_of_sequence", { detailPeriod: "2026-02" }));
    const user = setup(TREASURER, []);
    await user.click(await screen.findByRole("button", { name: "Fechar mês" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("O próximo mês a fechar é fevereiro de 2026.");
    create.mockResolvedValueOnce(problem(409, "period_not_ended"));
    await user.click(screen.getByRole("button", { name: "Fechar mês" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ainda não terminou");
    create.mockResolvedValueOnce(problem(403, "permission_denied"));
    await user.click(screen.getByRole("button", { name: "Fechar mês" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("não tem permissão");
  });
});

describe("reabrir (so com months:reopen)", () => {
  it("quem nao tem months:reopen (tesoureira, diretor) nao ve o botao", async () => {
    const user = setup(TREASURER, [JAN]);
    await user.click(await screen.findByRole("button", { name: "Abrir o fechamento de janeiro de 2026" }));
    expect(screen.queryByRole("button", { name: "Reabrir mês" })).not.toBeInTheDocument();
  });

  it("com a permissao: exige motivo de 10 a 500 caracteres e reabre", async () => {
    const reopen = vi.spyOn(api.closings, "reopen").mockResolvedValue({ ok: true, data: closing({ reopened_at: "2026-10-06T16:00:00Z" }) });
    const user = setup(ADMIN, [JAN]);
    await user.click(await screen.findByRole("button", { name: "Abrir o fechamento de janeiro de 2026" }));
    await user.click(screen.getByRole("button", { name: "Reabrir mês" }));
    await user.type(screen.getByLabelText("Motivo da reabertura"), "curto");
    await user.click(screen.getByRole("button", { name: "Reabrir o fechamento" }));
    expect(screen.getByText("Explique o motivo com 10 a 500 caracteres.")).toBeInTheDocument();
    expect(reopen).not.toHaveBeenCalled();
    await user.clear(screen.getByLabelText("Motivo da reabertura"));
    await user.type(screen.getByLabelText("Motivo da reabertura"), "Lançamento duplicado no mês");
    await user.click(screen.getByRole("button", { name: "Reabrir o fechamento" }));
    expect(reopen).toHaveBeenCalledWith(SCHOOL_ID, "jan", "Lançamento duplicado no mês");
    expect(await screen.findByText("Fechamento de janeiro de 2026 reaberto.")).toBeInTheDocument();
  });

  it("reabrir um que nao e o ultimo: texto da API em portugues", async () => {
    vi.spyOn(api.closings, "reopen").mockResolvedValue(problem(409, "closing_not_latest"));
    const user = setup(ADMIN, [JAN]);
    await user.click(await screen.findByRole("button", { name: "Abrir o fechamento de janeiro de 2026" }));
    await user.click(screen.getByRole("button", { name: "Reabrir mês" }));
    await user.type(screen.getByLabelText("Motivo da reabertura"), "Lançamento duplicado no mês");
    await user.click(screen.getByRole("button", { name: "Reabrir o fechamento" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Só dá para reabrir o último fechamento");
  });
});

describe("viewer (so leitura)", () => {
  it("ve a lista e baixa o PDF, mas nao fecha, nao reabre e nao verifica", async () => {
    const user = setup(VIEWER, [JAN]);
    expect(await screen.findByRole("note")).toHaveTextContent("Fechar o mês é da tesouraria e da direção");
    expect(screen.queryByRole("button", { name: "Fechar mês" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Abrir o fechamento de janeiro de 2026" }));
    expect(screen.getByRole("button", { name: "Baixar PDF do extrato mensal" })).toBeInTheDocument();
    for (const name of ["Verificar", "Reabrir mês"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("closing-detail")).toBeInTheDocument());
  });
});
