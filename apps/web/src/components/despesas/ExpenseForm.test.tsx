import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { ApiResult, Attachment, ExpenseDetail } from "@/lib/api/types";
import { CATEGORY, detail } from "@/test-utils/expense-fixtures";
import { SCHOOL_ID } from "@/test-utils/statement-fixtures";
import { ExpenseForm, dayInSchoolZone } from "./ExpenseForm";

const CATEGORIES = [CATEGORY, { id: "c2", key: "services", name: "Serviços" }];
const ok = (d: ExpenseDetail): ApiResult<ExpenseDetail> => ({ ok: true, data: d });
const att: Attachment = { id: "a", kind: "INVOICE", fileName: "nota.pdf", contentType: "application/pdf", sizeBytes: 9, uploadedByUserId: "u", createdAt: "2026-10-06T10:00:00Z" };

function setup(props: Partial<React.ComponentProps<typeof ExpenseForm>> = {}) {
  const onSaved = vi.fn();
  const onCancel = vi.fn();
  render(<ExpenseForm schoolId={SCHOOL_ID} categories={CATEGORIES} onSaved={onSaved} onCancel={onCancel} {...props} />);
  return { onSaved, onCancel, user: userEvent.setup() };
}

async function fillBasics(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Valor"), "1500");
  await user.type(screen.getByLabelText("Descrição"), "Cola e papel");
}

afterEach(() => vi.restoreAllMocks());

describe("ExpenseForm", () => {
  it("nunca pede o nome de quem envia (vem da sessao) e comeca na primeira categoria e na data de hoje", () => {
    setup();
    expect(screen.queryByLabelText(/nome/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Categoria")).toHaveValue(CATEGORY.id);
    expect(screen.getByLabelText("Data da despesa")).toHaveValue(dayInSchoolZone());
    expect(screen.getByText(/não pedimos o seu nome/)).toBeInTheDocument();
  });

  it("salva rascunho: cria na escola, sem enviar, com paid_by escolhido e sem campos vazios", async () => {
    const create = vi.spyOn(api.expenses, "create").mockResolvedValue(ok(detail({ id: "novo", status: "DRAFT" })));
    const submit = vi.spyOn(api.expenses, "submit");
    const { user, onSaved } = setup();
    await fillBasics(user);
    await user.click(screen.getByLabelText("Foi paga pela APM"));
    await user.click(screen.getByRole("button", { name: "Salvar rascunho" }));
    expect(create).toHaveBeenCalledWith(SCHOOL_ID, {
      amountCents: 1500, occurredAt: dayInSchoolZone(), categoryId: CATEGORY.id, description: "Cola e papel",
      vendor: undefined, purchaseReason: undefined, paymentMethod: undefined, paidBy: "APM",
    });
    expect(submit).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalledWith("novo", "Rascunho salvo.");
  });

  it("salvar e enviar: cria, anexa o arquivo (INVOICE) e envia, nessa ordem", async () => {
    const order: string[] = [];
    vi.spyOn(api.expenses, "create").mockImplementation(async () => (order.push("create"), ok(detail({ id: "novo", status: "DRAFT" }))));
    const upload = vi.spyOn(api.expenses, "upload").mockImplementation(async () => (order.push("upload"), { ok: true, data: att }));
    const submit = vi.spyOn(api.expenses, "submit").mockImplementation(async () => (order.push("submit"), ok(detail({ status: "SUBMITTED" }))));
    const { user, onSaved } = setup();
    await fillBasics(user);
    await user.type(screen.getByLabelText("Motivo da compra"), "Aula de artes");
    await user.selectOptions(screen.getByLabelText("Forma de pagamento"), "PIX");
    await user.upload(screen.getByLabelText(/Nota ou recibo/), new File(["%PDF-1.4"], "nota.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Salvar e enviar para análise" }));
    expect(order).toEqual(["create", "upload", "submit"]);
    expect(upload).toHaveBeenCalledWith(SCHOOL_ID, "novo", expect.any(File), "INVOICE");
    expect(submit).toHaveBeenCalledWith(SCHOOL_ID, "novo");
    expect(onSaved).toHaveBeenCalledWith("novo", "Despesa enviada para a análise da gestão.");
  });

  it("para enviar exige anexo, motivo e forma de pagamento: avisa e nao chama a API", async () => {
    const create = vi.spyOn(api.expenses, "create");
    const { user } = setup();
    await fillBasics(user);
    await user.click(screen.getByRole("button", { name: "Salvar e enviar para análise" }));
    expect(screen.getByText(/Informe o motivo da compra/)).toBeInTheDocument();
    expect(screen.getByText("Informe a forma de pagamento.")).toBeInTheDocument();
    expect(screen.getByText("Anexe a nota ou o recibo para enviar.")).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it("valor, descricao e categoria: valida no cliente", async () => {
    const create = vi.spyOn(api.expenses, "create");
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Salvar rascunho" }));
    expect(screen.getByText("Informe o valor.")).toBeInTheDocument();
    expect(screen.getByText("Descreva a despesa.")).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it("422 da API: erro ao lado do campo, em portugues (categoria indisponivel)", async () => {
    vi.spyOn(api.expenses, "create").mockResolvedValue({ ok: false, error: { kind: "problem", status: 422, code: "validation_error", fields: [{ field: "category_id", code: "not_available" }] } });
    const { user, onSaved } = setup();
    await fillBasics(user);
    await user.click(screen.getByRole("button", { name: "Salvar rascunho" }));
    expect(await screen.findByText("Escolha uma categoria da lista.")).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("falha no upload: o rascunho fica salvo e a tela avisa (nao perde o que foi digitado)", async () => {
    vi.spyOn(api.expenses, "create").mockResolvedValue(ok(detail({ id: "novo", status: "DRAFT" })));
    vi.spyOn(api.expenses, "upload").mockResolvedValue({ ok: false, error: { kind: "problem", status: 415, code: "attachment_type_not_allowed" } });
    const submit = vi.spyOn(api.expenses, "submit");
    const { user, onSaved } = setup();
    await fillBasics(user);
    await user.upload(screen.getByLabelText(/Nota ou recibo/), new File(["x"], "nota.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "Salvar rascunho" }));
    expect(submit).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalledWith("novo", expect.stringContaining("Rascunho salvo, mas o arquivo não foi enviado."));
    expect(onSaved.mock.calls[0]![1]).toContain("PNG, JPEG, WebP ou PDF");
  });

  it("arquivo maior que 10 MB e recusado no cliente", async () => {
    const create = vi.spyOn(api.expenses, "create");
    const { user } = setup();
    await fillBasics(user);
    const big = new File(["x"], "grande.pdf", { type: "application/pdf" });
    Object.defineProperty(big, "size", { value: 11 * 1024 * 1024 });
    await user.upload(screen.getByLabelText(/Nota ou recibo/), big);
    await user.click(screen.getByRole("button", { name: "Salvar rascunho" }));
    expect(screen.getByRole("alert")).toHaveTextContent("limite é de 10 MB");
    expect(create).not.toHaveBeenCalled();
  });

  it("editar: preenche com a despesa, usa PATCH e nao oferece 'quem pagou'", async () => {
    const update = vi.spyOn(api.expenses, "update").mockResolvedValue(ok(detail()));
    const { user, onSaved } = setup({ expense: detail({ status: "CORRECTION_REQUESTED", description: "Antiga", amountCents: 20000 }) });
    expect(screen.getByLabelText("Descrição")).toHaveValue("Antiga");
    expect(screen.queryByText("Quem pagou")).not.toBeInTheDocument();
    await user.clear(screen.getByLabelText("Descrição"));
    await user.type(screen.getByLabelText("Descrição"), "Nova descrição");
    await user.click(screen.getByRole("button", { name: "Salvar rascunho" }));
    expect(update).toHaveBeenCalledWith(SCHOOL_ID, detail().id, expect.objectContaining({ description: "Nova descrição", amountCents: 20000 }));
    expect(onSaved).toHaveBeenCalled();
  });

  it("nao escreve nada no console nem em storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const spies = [vi.spyOn(console, "error"), vi.spyOn(console, "warn"), vi.spyOn(console, "log")];
    vi.spyOn(api.expenses, "create").mockResolvedValue({ ok: false, error: { kind: "network" } });
    const { user } = setup();
    await fillBasics(user);
    await user.click(screen.getByRole("button", { name: "Salvar rascunho" }));
    await screen.findByRole("alert");
    expect(setItem).not.toHaveBeenCalled();
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });
});
