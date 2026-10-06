import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { ApiResult, ContributionState } from "@/lib/api/types";
import { PAYLOAD, TOKEN, TXID, state } from "@/test-utils/public-fixtures";
import { PixPayment } from "./PixPayment";

const base = state();
const ok = (s: ContributionState): ApiResult<ContributionState> => ({ ok: true, data: s });
const paid = state({ status: "PAID", charge: { ...base.charge!, status: "PAID" } });

function setup(fetchState: () => Promise<ApiResult<ContributionState>>, initial: ContributionState = base) {
  const onRestart = vi.fn();
  render(<PixPayment slug="demo-aurora" token={TOKEN} initial={initial} fetchState={fetchState} pollMs={15} onRestart={onRestart} />);
  return { onRestart };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PixPayment: o status vem so da API", () => {
  it("enquanto a API devolve PENDING, nunca aparece 'pago', mesmo apos interacoes", async () => {
    const fetchState = vi.fn().mockResolvedValue(ok(base));
    setup(fetchState);
    const user = userEvent.setup();
    expect(screen.getByText("Aguardando pagamento")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copiar código" }));
    await waitFor(() => expect(fetchState.mock.calls.length).toBeGreaterThan(3));
    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Ver comprovante" })).not.toBeInTheDocument();
  });

  it("mostra a confirmacao e o link do comprovante somente quando a contribuicao vem PAID", async () => {
    let current = base;
    setup(() => Promise.resolve(ok(current)));
    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
    current = paid;
    expect(await screen.findByRole("heading", { name: "Pagamento confirmado" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver comprovante" })).toHaveAttribute("href", `/escola/demo-aurora/pedido/${TOKEN}`);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Pagamento confirmado" })).toHaveFocus());
  });

  it("cobranca PAID com a contribuicao ainda pendente NAO e pagamento confirmado", async () => {
    const racing = state({ charge: { ...base.charge!, status: "PAID" } });
    const fetchState = vi.fn().mockResolvedValue(ok(racing));
    setup(fetchState);
    await waitFor(() => expect(fetchState.mock.calls.length).toBeGreaterThan(2));
    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
  });

  it("status desconhecido da API nunca vira pago: a consulta falha na validacao e a tela segue aguardando", async () => {
    const weird = { ok: false, error: { kind: "invalid-response", status: 200 } } as ApiResult<ContributionState>;
    const fetchState = vi.fn().mockResolvedValue(weird);
    setup(fetchState);
    await waitFor(() => expect(fetchState.mock.calls.length).toBeGreaterThan(2));
    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
    expect(screen.getByText("Aguardando pagamento")).toBeInTheDocument();
  });

  it("REVIEW_REQUIRED: 'em analise pela escola', NAO confirmada, sem comprovante", async () => {
    setup(() => Promise.resolve(ok(state({ status: "REVIEW_REQUIRED", charge: { ...base.charge!, status: "REVIEW_REQUIRED" } }))));
    expect(await screen.findByRole("heading", { name: "Pagamento em análise pela escola" })).toBeInTheDocument();
    expect(screen.getByText(/não está confirmada/)).toBeInTheDocument();
    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Ver comprovante" })).not.toBeInTheDocument();
  });

  it("QR expirado: 'Gerar novo QR' pede um novo QR a API e volta a aguardar com o novo codigo", async () => {
    const expired = state({ charge: { ...base.charge!, status: "EXPIRED" } });
    const fresh = state({ charge: { ...base.charge!, emvPayload: "PIX-SANDBOX:novotxid1234567890:2000" } });
    const renew = vi.spyOn(api.public, "renewCharge").mockResolvedValue(ok(fresh));
    let current = expired;
    setup(() => Promise.resolve(ok(current)), expired);
    expect(await screen.findByRole("heading", { name: "Este QR expirou" })).toBeInTheDocument();
    current = fresh;
    await userEvent.setup().click(screen.getByRole("button", { name: "Gerar novo QR" }));
    expect(renew).toHaveBeenCalledWith("demo-aurora", TOKEN);
    expect(await screen.findByRole("heading", { name: "Pague com Pix" })).toBeInTheDocument();
    expect((screen.getByLabelText("Pix copia e cola") as HTMLTextAreaElement).value).toBe("PIX-SANDBOX:novotxid1234567890:2000");
  });

  it("falha ao gerar novo QR: texto em portugues, sem sair da tela", async () => {
    const expired = state({ charge: { ...base.charge!, status: "EXPIRED" } });
    vi.spyOn(api.public, "renewCharge").mockResolvedValue({ ok: false, error: { kind: "problem", status: 409, code: "contribution_closed" } });
    setup(() => Promise.resolve(ok(expired)), expired);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Gerar novo QR" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("não está mais aguardando pagamento");
    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
  });

  it("contribuicao encerrada (cancelada/expirada): oferece recomecar", async () => {
    const { onRestart } = setup(() => Promise.resolve(ok(state({ status: "CANCELLED", charge: { ...base.charge!, status: "CANCELLED" } }))));
    expect(await screen.findByRole("heading", { name: "Esta contribuição foi encerrada" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Começar de novo" }));
    expect(onRestart).toHaveBeenCalled();
  });

  it("a contagem regressiva chegando a zero nao expira nem paga: continua aguardando a API", () => {
    const past = state({ charge: { ...base.charge!, expiresAt: new Date(Date.now() - 1000).toISOString() } });
    setup(() => Promise.resolve(ok(past)), past);
    expect(screen.getAllByText("Prazo encerrado").length).toBeGreaterThan(0);
    expect(screen.getByText(/Conferindo com o banco/)).toBeInTheDocument();
    expect(screen.queryByText("Este QR expirou")).not.toBeInTheDocument();
    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
  });

  it("falha de rede nao quebra: avisa e continua tentando", async () => {
    let calls = 0;
    setup(() => {
      calls += 1;
      return Promise.resolve(calls < 3 ? ({ ok: false, error: { kind: "network" } } as ApiResult<ContributionState>) : ok(base));
    });
    expect(await screen.findByText(/Sem conexão para atualizar/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/Sem conexão para atualizar/)).not.toBeInTheDocument());
    expect(calls).toBeGreaterThanOrEqual(3);
  });

  it("contribuicao inexistente (404) mostra mensagem e oferece recomecar", async () => {
    const { onRestart } = setup(() => Promise.resolve({ ok: false, error: { kind: "problem", status: 404, code: "not_found" } }));
    expect(await screen.findByRole("heading", { name: "Não encontramos este Pix" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Começar de novo" }));
    expect(onRestart).toHaveBeenCalled();
  });
});

function stubClipboard(writeText: () => Promise<void> | void) {
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
}

describe("PixPayment: QR, copia e cola e simulacao do sandbox", () => {
  it("desenha o QR no navegador a partir do emv_payload (sem imagem externa)", () => {
    setup(() => Promise.resolve(ok(base)));
    const qr = screen.getByRole("img", { name: /QR Code do Pix/ });
    expect(qr.tagName.toLowerCase()).toBe("svg");
    expect(qr.querySelector("path")?.getAttribute("d")?.length).toBeGreaterThan(100);
    expect(document.querySelector("img")).toBeNull();
  });

  it("copia o codigo, confirma visualmente e anuncia para leitores de tela", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);
    setup(() => Promise.resolve(ok(base)));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copiar código" }));
    });
    expect(writeText).toHaveBeenCalledWith(PAYLOAD);
    expect(await screen.findByRole("button", { name: "Código copiado" })).toBeInTheDocument();
    expect(screen.getByText("Código copiado. Cole no app do seu banco.")).toBeInTheDocument();
  });

  it("sem permissao de area de transferencia, seleciona o texto e orienta o usuario", async () => {
    stubClipboard(() => Promise.reject(new Error("negado")));
    setup(() => Promise.resolve(ok(base)));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copiar código" }));
    });
    expect(await screen.findByText(/O navegador não permitiu copiar/)).toBeInTheDocument();
  });

  it("payload PIX-SANDBOX: mostra o botao de simular e avisa que e teste", () => {
    setup(() => Promise.resolve(ok(base)));
    expect(screen.getByRole("button", { name: "Simular pagamento (somente desenvolvimento)" })).toBeInTheDocument();
    expect(screen.getByText(/Pix de teste \(sandbox\)/)).toBeInTheDocument();
  });

  it("payload de Pix real (nao PIX-SANDBOX): NAO existe botao de simular", () => {
    const real = state({ charge: { ...base.charge!, emvPayload: "00020126580014br.gov.bcb.pix0136abc520400005303986540520.005802BR5909APM6009SAO PAULO62070503***6304ABCD" } });
    setup(() => Promise.resolve(ok(real)), real);
    expect(screen.queryByRole("button", { name: /Simular pagamento/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/sandbox/)).not.toBeInTheDocument();
  });

  it("simular chama o sandbox com o txid do payload e NAO marca pago sozinho: so a proxima consulta da API muda a tela", async () => {
    const sandboxPay = vi.spyOn(api.public, "sandboxPay").mockResolvedValue({ ok: true, data: { status: "PAID" } });
    let current = base;
    const fetchState = vi.fn(() => Promise.resolve(ok(current)));
    setup(fetchState);
    await userEvent.setup().click(screen.getByRole("button", { name: "Simular pagamento (somente desenvolvimento)" }));
    expect(sandboxPay).toHaveBeenCalledWith(TXID);
    // a API ainda diz pendente: a tela continua aguardando
    await waitFor(() => expect(fetchState).toHaveBeenCalled());
    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
    current = paid;
    expect(await screen.findByRole("heading", { name: "Pagamento confirmado" })).toBeInTheDocument();
  });

  it("falha na simulacao: aviso, sem pago", async () => {
    vi.spyOn(api.public, "sandboxPay").mockResolvedValue({ ok: false, error: { kind: "problem", status: 404, code: "not_found" } });
    setup(() => Promise.resolve(ok(base)));
    await userEvent.setup().click(screen.getByRole("button", { name: "Simular pagamento (somente desenvolvimento)" }));
    expect(await screen.findByText(/Não foi possível simular/)).toBeInTheDocument();
    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
  });
});
