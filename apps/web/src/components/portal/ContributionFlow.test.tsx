import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { ApiError, ApiResult, CreatedContribution } from "@/lib/api/types";
import { SCHOOL, TOKEN, state } from "@/test-utils/public-fixtures";
import { ContributionFlow } from "./ContributionFlow";

const created: CreatedContribution = { ...state(), token: TOKEN };
const okCreated: ApiResult<CreatedContribution> = { ok: true, data: created };
const fail = (error: ApiError): ApiResult<CreatedContribution> => ({ ok: false, error });
const problem = (code: string, status: number, extra: Partial<ApiError> = {}) => fail({ kind: "problem", status, code, ...extra });
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

beforeEach(() => {
  // o passo do Pix consulta o estado; aqui ele nao muda
  vi.spyOn(api.public, "contribution").mockResolvedValue({ ok: true, data: state() });
});
afterEach(() => vi.restoreAllMocks());

async function toReview(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("radio", { name: /20,00/ }));
  await user.click(screen.getByRole("button", { name: "Continuar" }));
  await user.click(screen.getByRole("button", { name: "Continuar" })); // dados opcionais em branco
  expect(await screen.findByRole("heading", { name: "Confira antes de pagar" })).toBeInTheDocument();
}

const submit = () => screen.getByRole("button", { name: /Gerar Pix de/ });

describe("ContributionFlow (portal real)", () => {
  it("so mostra os campos que a escola pede (HIDDEN nao aparece)", async () => {
    render(<ContributionFlow school={SCHOOL} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("radio", { name: /20,00/ }));
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    expect(screen.getByLabelText(/Nome do responsável/)).toBeInTheDocument();
    expect(screen.getByLabelText(/E-mail/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Telefone/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Nome do aluno/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Turma/)).not.toBeInTheDocument();
  });

  it("sem campos visiveis, pula direto da escolha para a revisao", async () => {
    const hidden = { ...SCHOOL, identification: { guardianName: "HIDDEN", studentName: "HIDDEN", classroom: "HIDDEN", contributorEmail: "HIDDEN", contributorPhone: "HIDDEN" } as const };
    render(<ContributionFlow school={hidden} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("radio", { name: /20,00/ }));
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    expect(await screen.findByRole("heading", { name: "Confira antes de pagar" })).toBeInTheDocument();
  });

  it("cria a contribuicao com UMA Idempotency-Key (uuid), gera o QR e nunca grava nada em storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const create = vi.spyOn(api.public, "createContribution").mockResolvedValue(okCreated);
    render(<ContributionFlow school={SCHOOL} />);
    const user = userEvent.setup();
    await toReview(user);
    await user.click(submit());

    expect(create).toHaveBeenCalledTimes(1);
    const [slug, input, key] = create.mock.calls[0]!;
    expect(slug).toBe("demo-aurora");
    expect(input).toEqual({ amountCents: 2000, identification: {} });
    expect(key).toMatch(UUID);
    expect(await screen.findByRole("heading", { name: "Pague com Pix" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /QR Code do Pix/ })).toBeInTheDocument();
    expect(setItem).not.toHaveBeenCalled();
    expect(window.location.href).not.toContain(key);
  });

  it("falha de rede e nova tentativa com os MESMOS dados: reaproveita a mesma chave", async () => {
    const create = vi
      .spyOn(api.public, "createContribution")
      .mockResolvedValueOnce(fail({ kind: "network" }))
      .mockResolvedValueOnce(okCreated);
    render(<ContributionFlow school={SCHOOL} />);
    const user = userEvent.setup();
    await toReview(user);
    await user.click(submit());
    expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível falar com o servidor");
    await user.click(submit());
    await screen.findByRole("heading", { name: "Pague com Pix" });
    expect(create).toHaveBeenCalledTimes(2);
    expect(create.mock.calls[1]![2]).toBe(create.mock.calls[0]![2]);
  });

  it("o usuario muda o valor depois de uma falha: chave NOVA", async () => {
    const create = vi
      .spyOn(api.public, "createContribution")
      .mockResolvedValueOnce(fail({ kind: "network" }))
      .mockResolvedValueOnce(okCreated);
    render(<ContributionFlow school={SCHOOL} />);
    const user = userEvent.setup();
    await toReview(user);
    await user.click(submit());
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Alterar o valor" }));
    await user.click(screen.getByRole("radio", { name: /30,00/ }));
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    await user.click(submit());
    await screen.findByRole("heading", { name: "Pague com Pix" });
    expect(create.mock.calls[1]![1]).toEqual({ amountCents: 3000, identification: {} });
    expect(create.mock.calls[1]![2]).not.toBe(create.mock.calls[0]![2]);
    expect(create.mock.calls[1]![2]).toMatch(UUID);
  });

  it("o usuario muda os dados depois de uma falha: chave NOVA", async () => {
    const create = vi
      .spyOn(api.public, "createContribution")
      .mockResolvedValueOnce(fail({ kind: "network" }))
      .mockResolvedValueOnce(okCreated);
    render(<ContributionFlow school={SCHOOL} />);
    const user = userEvent.setup();
    await toReview(user);
    await user.click(submit());
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Voltar" })); // da revisao para os dados
    await user.type(screen.getByLabelText(/Nome do responsável/), "Maria Teste");
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    await user.click(submit());
    await screen.findByRole("heading", { name: "Pague com Pix" });
    expect(create.mock.calls[1]![1]).toEqual({ amountCents: 2000, identification: { guardianName: "Maria Teste" } });
    expect(create.mock.calls[1]![2]).not.toBe(create.mock.calls[0]![2]);
  });

  it("422 de campo: volta aos dados e mostra o erro em portugues AO LADO do campo", async () => {
    vi.spyOn(api.public, "createContribution").mockResolvedValue(
      problem("validation_error", 422, { fields: [{ field: "contributor_email", code: "invalid" }] }),
    );
    render(<ContributionFlow school={SCHOOL} />);
    const user = userEvent.setup();
    await toReview(user);
    await user.click(submit());
    expect(await screen.findByRole("heading", { name: "Quem está contribuindo?" })).toBeInTheDocument();
    expect(screen.getByText("Confira este campo.")).toBeInTheDocument();
    expect(screen.getByLabelText(/E-mail/)).toHaveAttribute("aria-invalid", "true");
  });

  it("422 do valor: volta ao valor com o erro", async () => {
    vi.spyOn(api.public, "createContribution").mockResolvedValue(
      problem("validation_error", 422, { fields: [{ field: "amount_cents", code: "out_of_range" }] }),
    );
    render(<ContributionFlow school={SCHOOL} />);
    const user = userEvent.setup();
    await toReview(user);
    await user.click(submit());
    expect(await screen.findByRole("heading", { name: "Quanto você quer contribuir?" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("não está dentro do que a escola aceita");
  });

  it("429: mostra o tempo de espera e nao troca de tela", async () => {
    vi.spyOn(api.public, "createContribution").mockResolvedValue(problem("rate_limited", 429, { retryAfterSeconds: 120 }));
    render(<ContributionFlow school={SCHOOL} />);
    const user = userEvent.setup();
    await toReview(user);
    await user.click(submit());
    expect(await screen.findByRole("alert")).toHaveTextContent("Aguarde 2 minutos");
    expect(screen.getByRole("heading", { name: "Confira antes de pagar" })).toBeInTheDocument();
  });

  it("depois de criar, 'Fazer outra contribuicao' gera uma chave nova", async () => {
    const create = vi.spyOn(api.public, "createContribution").mockResolvedValue(okCreated);
    vi.spyOn(api.public, "contribution").mockResolvedValue({ ok: true, data: { ...state({ status: "PAID" }), charge: { ...state().charge!, status: "PAID" } } });
    render(<ContributionFlow school={SCHOOL} />);
    const user = userEvent.setup();
    await toReview(user);
    await user.click(submit());
    await user.click(await screen.findByRole("button", { name: "Fazer outra contribuição" }, { timeout: 3000 }));
    await toReview(user);
    await user.click(submit());
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create.mock.calls[1]![2]).not.toBe(create.mock.calls[0]![2]);
  });
});
