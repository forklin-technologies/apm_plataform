import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { ApiError, ApiResult, ExpenseDetail } from "@/lib/api/types";
import { AUTHOR_ID, CATEGORY, MANAGER_ID, detail } from "@/test-utils/expense-fixtures";
import { SCHOOL_ID } from "@/test-utils/statement-fixtures";

const saveBlob = vi.hoisted(() => vi.fn());
vi.mock("@/lib/download", () => ({ saveBlob }));

import { ExpenseDetailPanel } from "./ExpenseDetailPanel";

const STAFF = ["expenses:read_own", "expenses:submit"];
const TREASURER = ["statement:read", "expenses:approve", "expenses:read_all", "reimbursements:register"];
const SCHOOL_ADMIN = [...TREASURER, "expenses:read_own", "expenses:submit"];
const ok = (d: ExpenseDetail): ApiResult<ExpenseDetail> => ({ ok: true, data: d });
const fail = (status: number, code: string, extra: Partial<ApiError> = {}): ApiResult<never> => ({ ok: false, error: { kind: "problem", status, code, ...extra } });

function setup(expense: ExpenseDetail, userId: string, permissions: string[]) {
  vi.spyOn(api.expenses, "get").mockResolvedValue(ok(expense));
  const onChanged = vi.fn();
  render(<ExpenseDetailPanel schoolId={SCHOOL_ID} expenseId={expense.id} userId={userId} permissions={permissions} categories={[CATEGORY]} onChanged={onChanged} />);
  return { onChanged, user: userEvent.setup() };
}

const buttons = () => screen.getAllByRole("button").map((b) => b.textContent);

beforeEach(() => saveBlob.mockReset());
afterEach(() => vi.restoreAllMocks());

describe("professora (autora)", () => {
  it("correcao pedida: mostra o motivo da gestao e as acoes de editar, anexar, enviar e cancelar", async () => {
    setup(detail({ status: "CORRECTION_REQUESTED", correctionReason: "Falta o CNPJ na nota" }), AUTHOR_ID, STAFF);
    expect(await screen.findByRole("note")).toHaveTextContent("A gestão pediu correção: Falta o CNPJ na nota");
    expect(screen.getByText("Correção pedida")).toBeInTheDocument();
    expect(buttons()).toEqual(expect.arrayContaining(["Editar", "Enviar para análise", "Cancelar despesa"]));
    expect(screen.getByLabelText(/Anexar outro arquivo/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Aprovar" })).not.toBeInTheDocument();
  });

  it("rascunho sem anexo: pede para anexar", async () => {
    setup(detail({ status: "DRAFT", attachments: [], attachmentsCount: 0 }), AUTHOR_ID, STAFF);
    expect(await screen.findByLabelText("Anexar a nota ou o recibo")).toBeInTheDocument();
    expect(screen.getByText("Nenhum arquivo anexado ainda.")).toBeInTheDocument();
  });

  it("enviada: sem acoes (so aguarda a gestao); aprovada, paga e recusada: so leitura", async () => {
    setup(detail({ status: "SUBMITTED" }), AUTHOR_ID, STAFF);
    expect(await screen.findByText(/aguardando a decisão da gestão/)).toBeInTheDocument();
    expect(buttons()).toEqual(["Baixar"]);
    expect(screen.queryByLabelText(/Anexar/)).not.toBeInTheDocument();
  });

  it("anexa o arquivo: manda INVOICE para a API e recarrega o detalhe; erro attachments_closed em portugues", async () => {
    const upload = vi.spyOn(api.expenses, "upload").mockResolvedValue({ ok: false, error: { kind: "problem", status: 409, code: "attachments_closed" } });
    const { user, onChanged } = setup(detail({ status: "DRAFT", attachments: [] }), AUTHOR_ID, STAFF);
    const input = await screen.findByLabelText("Anexar a nota ou o recibo");
    await user.upload(input, new File(["%PDF-1.4"], "nota.pdf", { type: "application/pdf" }));
    expect(upload).toHaveBeenCalledWith(SCHOOL_ID, expect.any(String), expect.any(File), "INVOICE");
    expect(await screen.findByRole("alert")).toHaveTextContent("só dá para anexar arquivos em rascunho");

    upload.mockResolvedValue({ ok: true, data: { id: "a2", kind: "INVOICE", fileName: "nota.pdf", contentType: "application/pdf", sizeBytes: 8, uploadedByUserId: AUTHOR_ID, createdAt: "2026-10-06T14:00:00Z" } });
    vi.spyOn(api.expenses, "get").mockResolvedValue(ok(detail({ status: "DRAFT" })));
    await user.upload(input, new File(["%PDF-1.4 outro"], "outra.pdf", { type: "application/pdf" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith("Arquivo anexado."));
  });

  it("envia para analise: mostra o que a API devolveu (enviada) e avisa a lista", async () => {
    const submit = vi.spyOn(api.expenses, "submit").mockResolvedValue(ok(detail({ status: "SUBMITTED" })));
    const { user, onChanged } = setup(detail({ status: "DRAFT" }), AUTHOR_ID, STAFF);
    await user.click(await screen.findByRole("button", { name: "Enviar para análise" }));
    expect(submit).toHaveBeenCalledWith(SCHOOL_ID, expect.any(String));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith("Despesa enviada para a análise da gestão."));
    expect(await screen.findByText("Enviada")).toBeInTheDocument();
  });

  it("envio incompleto (422): diz o que falta; 409: recarrega o detalhe", async () => {
    vi.spyOn(api.expenses, "submit").mockResolvedValueOnce(fail(422, "expense_incomplete", { fields: [{ field: "attachments", code: "required" }] }));
    const { user } = setup(detail({ status: "DRAFT" }), AUTHOR_ID, STAFF);
    await user.click(await screen.findByRole("button", { name: "Enviar para análise" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Para enviar a despesa, anexe a nota ou o recibo.");

    vi.spyOn(api.expenses, "submit").mockResolvedValueOnce(fail(409, "invalid_state"));
    const get = vi.spyOn(api.expenses, "get");
    const calls = get.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Enviar para análise" }));
    expect(await screen.findByText(/mudou de situação/)).toBeInTheDocument();
    await waitFor(() => expect(get.mock.calls.length).toBeGreaterThan(calls));
  });

  it("baixa o anexo pela API (nunca URL publica)", async () => {
    const download = vi.spyOn(api.expenses, "download").mockResolvedValue({ ok: true, data: { blob: new Blob(["%PDF"]), contentType: "application/pdf" } });
    const { user } = setup(detail({ status: "SUBMITTED" }), AUTHOR_ID, STAFF);
    await user.click(await screen.findByRole("button", { name: "Baixar nota.pdf" }));
    expect(download).toHaveBeenCalledWith(SCHOOL_ID, expect.any(String), "a1111111-1111-4111-8111-111111111111");
    expect(saveBlob).toHaveBeenCalledWith(expect.any(Blob), "nota.pdf");
    expect(document.querySelector("a[href^='http'], a[download]")).toBeNull();
  });
});

describe("gestao (tesoureira)", () => {
  it("enviada por outra pessoa: aprovar, pedir correcao e recusar; nada de editar", async () => {
    setup(detail({ status: "SUBMITTED" }), MANAGER_ID, TREASURER);
    await screen.findByRole("button", { name: "Aprovar" });
    expect(buttons()).toEqual(expect.arrayContaining(["Aprovar", "Pedir correção", "Recusar"]));
    expect(screen.queryByRole("button", { name: "Editar" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Anexar/)).not.toBeInTheDocument();
  });

  it("despesa DA PROPRIA pessoa: nao oferece aprovar, recusar nem pedir correcao, e explica", async () => {
    setup(detail({ status: "SUBMITTED" }), AUTHOR_ID, SCHOOL_ADMIN);
    expect(await screen.findByRole("note")).toHaveTextContent("Esta despesa é sua: outra pessoa da gestão precisa decidir.");
    for (const name of ["Aprovar", "Pedir correção", "Recusar"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
  });

  it("aprova pelo valor pedido (sem corpo)", async () => {
    const approve = vi.spyOn(api.expenses, "approve").mockResolvedValue(ok(detail({ status: "APPROVED" })));
    const { user, onChanged } = setup(detail({ status: "SUBMITTED" }), MANAGER_ID, TREASURER);
    await user.click(await screen.findByRole("button", { name: "Aprovar" }));
    await user.click(screen.getByRole("button", { name: "Aprovar" }));
    expect(approve).toHaveBeenCalledWith(SCHOOL_ID, expect.any(String), {});
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith("Despesa aprovada."));
    expect(screen.getByText("Reembolso pendente")).toBeInTheDocument();
  });

  it("aprovacao PARCIAL (colaborador pagou): valor menor e motivo vao para a API; valida antes", async () => {
    const approve = vi.spyOn(api.expenses, "approve").mockResolvedValue(ok(detail({ status: "APPROVED", approvedAmountCents: 17500 })));
    const { user, onChanged } = setup(detail({ status: "SUBMITTED", paidBy: "COLLABORATOR", amountCents: 20000 }), MANAGER_ID, TREASURER);
    await user.click(await screen.findByRole("button", { name: "Aprovar" }));
    await user.click(screen.getByLabelText("Um valor menor (aprovação parcial)"));
    await user.click(screen.getByRole("button", { name: "Aprovar valor menor" }));
    expect(screen.getByText(/Informe um valor menor que/)).toBeInTheDocument();
    expect(screen.getByText(/Explique a diferença/)).toBeInTheDocument();
    expect(approve).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText("Valor aprovado"), "17500");
    await user.type(screen.getByLabelText("Motivo da diferença"), "Sem o frete");
    await user.click(screen.getByRole("button", { name: "Aprovar valor menor" }));
    expect(approve).toHaveBeenCalledWith(SCHOOL_ID, expect.any(String), { approvedAmountCents: 17500, reason: "Sem o frete" });
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith("Despesa aprovada."));
  });

  it("despesa paga pela APM: so aprovacao pelo valor pedido (sem opcao parcial)", async () => {
    const { user } = setup(detail({ status: "SUBMITTED", paidBy: "APM" }), MANAGER_ID, TREASURER);
    await user.click(await screen.findByRole("button", { name: "Aprovar" }));
    expect(screen.queryByLabelText("Um valor menor (aprovação parcial)")).not.toBeInTheDocument();
  });

  it("erro da API na aprovacao (autor, 409, 422) aparece em portugues", async () => {
    const approve = vi.spyOn(api.expenses, "approve").mockResolvedValueOnce(fail(403, "self_approval_forbidden"));
    const { user } = setup(detail({ status: "SUBMITTED" }), MANAGER_ID, TREASURER);
    await user.click(await screen.findByRole("button", { name: "Aprovar" }));
    await user.click(screen.getByRole("button", { name: "Aprovar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("própria despesa");
    approve.mockResolvedValueOnce(fail(422, "partial_approval_not_allowed"));
    await user.click(screen.getByRole("button", { name: "Aprovar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("colaborador pagou");
  });

  it("recusar e pedir correcao exigem motivo (3 a 500) e o enviam", async () => {
    const reject = vi.spyOn(api.expenses, "reject").mockResolvedValue(ok(detail({ status: "REJECTED", decisionReason: "Não é da APM" })));
    const { user, onChanged } = setup(detail({ status: "SUBMITTED" }), MANAGER_ID, TREASURER);
    await user.click(await screen.findByRole("button", { name: "Recusar" }));
    await user.click(screen.getByRole("button", { name: "Recusar despesa" }));
    expect(screen.getByText("O motivo precisa ter de 3 a 500 caracteres.")).toBeInTheDocument();
    expect(reject).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("Motivo da recusa"), "Não é da APM");
    await user.click(screen.getByRole("button", { name: "Recusar despesa" }));
    expect(reject).toHaveBeenCalledWith(SCHOOL_ID, expect.any(String), "Não é da APM");
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith("Despesa recusada."));
    expect((await screen.findByText(/Motivo da decisão:/)).closest("p")).toHaveTextContent("Não é da APM");
  });

  it("pedir correcao envia o motivo", async () => {
    const correct = vi.spyOn(api.expenses, "requestCorrection").mockResolvedValue(ok(detail({ status: "CORRECTION_REQUESTED", correctionReason: "Falta o CNPJ" })));
    const { user } = setup(detail({ status: "SUBMITTED" }), MANAGER_ID, TREASURER);
    await user.click(await screen.findByRole("button", { name: "Pedir correção" }));
    await user.type(screen.getByLabelText("O que precisa ser corrigido"), "Falta o CNPJ");
    await user.click(screen.getByRole("button", { name: "Pedir correção" }));
    expect(correct).toHaveBeenCalledWith(SCHOOL_ID, expect.any(String), "Falta o CNPJ");
  });

  it("aprovada paga por colaborador: registra o reembolso com a referencia; paga pela APM: registra o pagamento", async () => {
    const reimburse = vi.spyOn(api.expenses, "reimburse").mockResolvedValue(ok(detail({ status: "PAID" })));
    const first = setup(detail({ status: "APPROVED", paidBy: "COLLABORATOR", approvedAmountCents: 17500 }), MANAGER_ID, TREASURER);
    await first.user.click(await screen.findByRole("button", { name: "Registrar reembolso" }));
    await first.user.click(screen.getByRole("button", { name: "Registrar reembolso" }));
    expect(screen.getByText(/Informe a referência do pagamento/)).toBeInTheDocument();
    await first.user.type(screen.getByLabelText("Referência do pagamento"), "PIX E2E123");
    await first.user.click(screen.getByRole("button", { name: "Registrar reembolso" }));
    expect(reimburse).toHaveBeenCalledWith(SCHOOL_ID, expect.any(String), "PIX E2E123");
    await waitFor(() => expect(first.onChanged).toHaveBeenCalledWith("Reembolso registrado."));
    expect(await screen.findByText("Paga")).toBeInTheDocument();
  });

  it("aprovada paga pela APM: 'Registrar pagamento' chama pay", async () => {
    const pay = vi.spyOn(api.expenses, "pay").mockResolvedValue(ok(detail({ status: "PAID", paidBy: "APM" })));
    const { user, onChanged } = setup(detail({ status: "APPROVED", paidBy: "APM" }), MANAGER_ID, TREASURER);
    expect(screen.queryByRole("button", { name: "Registrar reembolso" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Registrar pagamento" }));
    expect(pay).toHaveBeenCalledWith(SCHOOL_ID, expect.any(String));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith("Pagamento registrado."));
  });

  it("aprovada DA PROPRIA pessoa: nao oferece reembolso nem pagamento (so cancelar), e explica", async () => {
    setup(detail({ status: "APPROVED", paidBy: "COLLABORATOR" }), AUTHOR_ID, SCHOOL_ADMIN);
    expect(await screen.findByRole("note")).toHaveTextContent("outra pessoa da gestão precisa registrar o pagamento ou o reembolso");
    expect(screen.queryByRole("button", { name: "Registrar reembolso" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Registrar pagamento" })).not.toBeInTheDocument();
  });

  it("erro de reembolso do proprio autor vindo da API (403) mostra o texto certo", async () => {
    vi.spyOn(api.expenses, "reimburse").mockResolvedValue(fail(403, "self_reimbursement_forbidden"));
    const { user } = setup(detail({ status: "APPROVED", paidBy: "COLLABORATOR" }), MANAGER_ID, TREASURER);
    await user.click(await screen.findByRole("button", { name: "Registrar reembolso" }));
    await user.type(screen.getByLabelText("Referência do pagamento"), "REF");
    await user.click(screen.getByRole("button", { name: "Registrar reembolso" }));
    expect(await screen.findByText(/não pode registrar o seu próprio reembolso/)).toBeInTheDocument();
  });

  it("sem a permissao de registrar pagamento, a aprovada nao oferece nada", async () => {
    setup(detail({ status: "APPROVED" }), MANAGER_ID, ["statement:read", "expenses:read_all"]);
    await screen.findByText("Reembolso pendente");
    expect(buttons()).toEqual(["Baixar"]);
  });
});

describe("falhas de carga", () => {
  it("404 ao abrir o detalhe: mensagem e nada mais", async () => {
    vi.spyOn(api.expenses, "get").mockResolvedValue(fail(404, "not_found"));
    render(<ExpenseDetailPanel schoolId={SCHOOL_ID} expenseId="x" userId={MANAGER_ID} permissions={TREASURER} categories={[]} onChanged={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Não encontramos esta despesa");
  });
});
