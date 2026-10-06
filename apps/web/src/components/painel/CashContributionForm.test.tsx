import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { ApiResult, CashContributionResult } from "@/lib/api/types";
import { SCHOOL_ID } from "@/test-utils/statement-fixtures";
import { CashContributionForm } from "./CashContributionForm";

const RESULT: CashContributionResult = { id: "i", referenceCode: "APM-000042", status: "PAID", amountCents: 5000, method: "CASH" };
const ok: ApiResult<CashContributionResult> = { ok: true, data: RESULT };
const fail = (error: Extract<ApiResult<never>, { ok: false }>["error"]): ApiResult<CashContributionResult> => ({ ok: false, error });
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function setup() {
  const onRecorded = vi.fn();
  const onClose = vi.fn();
  render(<CashContributionForm schoolId={SCHOOL_ID} onRecorded={onRecorded} onClose={onClose} />);
  return { onRecorded, onClose, user: userEvent.setup() };
}

afterEach(() => vi.restoreAllMocks());

describe("CashContributionForm", () => {
  it("registra com Idempotency-Key (uuid), na escola do vinculo, e avisa o painel", async () => {
    const record = vi.spyOn(api.statement, "recordCash").mockResolvedValue(ok);
    const { onRecorded, user } = setup();
    await user.type(screen.getByLabelText("Valor"), "5000");
    await user.selectOptions(screen.getByLabelText("Forma"), "TRANSFER");
    await user.selectOptions(screen.getByLabelText("Tipo de entrada"), "donation");
    await user.type(screen.getByLabelText(/Nome do responsável/), "João Dinheiro");
    await user.click(screen.getByRole("button", { name: "Registrar" }));

    expect(record).toHaveBeenCalledTimes(1);
    const [schoolId, input, key] = record.mock.calls[0]!;
    expect(schoolId).toBe(SCHOOL_ID);
    expect(input).toEqual({ amountCents: 5000, method: "TRANSFER", categoryKey: "donation", identification: { guardianName: "João Dinheiro" } });
    expect(key).toMatch(UUID);
    expect(await screen.findByRole("status")).toHaveTextContent("Contribuição APM-000042 registrada: R$ 50,00.");
    expect(onRecorded).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Valor")).toHaveValue(""); // pronto para a proxima
  });

  it("valor vazio: avisa e nao chama a API", async () => {
    const record = vi.spyOn(api.statement, "recordCash");
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Registrar" }));
    expect(screen.getByText("Informe o valor.")).toBeInTheDocument();
    expect(record).not.toHaveBeenCalled();
  });

  it("falha de rede e repeticao com os mesmos dados: a MESMA chave; mudou o valor: chave nova", async () => {
    const record = vi
      .spyOn(api.statement, "recordCash")
      .mockResolvedValueOnce(fail({ kind: "network" }))
      .mockResolvedValueOnce(fail({ kind: "network" }))
      .mockResolvedValueOnce(ok);
    const { user } = setup();
    await user.type(screen.getByLabelText("Valor"), "5000");
    await user.click(screen.getByRole("button", { name: "Registrar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nada foi registrado");
    await user.click(screen.getByRole("button", { name: "Registrar" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(2));
    expect(record.mock.calls[1]![2]).toBe(record.mock.calls[0]![2]);
    await user.type(screen.getByLabelText("Valor"), "0"); // 50,00 -> 500,00
    await user.click(screen.getByRole("button", { name: "Registrar" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(3));
    expect(record.mock.calls[2]![2]).not.toBe(record.mock.calls[0]![2]);
  });

  it("403 e 422: textos em portugues e erro ao lado do campo", async () => {
    vi.spyOn(api.statement, "recordCash")
      .mockResolvedValueOnce(fail({ kind: "problem", status: 403, code: "permission_denied" }))
      .mockResolvedValueOnce(fail({ kind: "problem", status: 422, code: "validation_error", fields: [{ field: "guardian_name", code: "invalid" }, { field: "amount_cents", code: "out_of_range" }] }));
    const { user } = setup();
    await user.type(screen.getByLabelText("Valor"), "5000");
    await user.click(screen.getByRole("button", { name: "Registrar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Você não tem permissão para registrar contribuições");
    await user.click(screen.getByRole("button", { name: "Registrar" }));
    expect(await screen.findByText("Confira o valor da contribuição.")).toBeInTheDocument();
    expect(screen.getByText("Confira este campo.")).toBeInTheDocument();
  });

  it("nunca grava nada em storage nem escreve a chave no console", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const spies = [vi.spyOn(console, "error"), vi.spyOn(console, "warn"), vi.spyOn(console, "log")];
    vi.spyOn(api.statement, "recordCash").mockResolvedValue(fail({ kind: "network" }));
    const { user } = setup();
    await user.type(screen.getByLabelText("Valor"), "5000");
    await user.click(screen.getByRole("button", { name: "Registrar" }));
    await screen.findByRole("alert");
    expect(setItem).not.toHaveBeenCalled();
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });
});
