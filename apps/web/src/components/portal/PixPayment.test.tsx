import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiResult, PixCharge } from "@/lib/api/types";
import { PixPayment } from "./PixPayment";

const NOW = Date.now();
const base: PixCharge = {
  token: "tok_test_000000000001",
  status: "PENDING",
  amountCents: 12000,
  payload: "PROTOTIPO.NAO-E-PIX.NAO-PAGUE.tok_test_000000000001.VALOR-12000-CENTAVOS",
  createdAt: new Date(NOW).toISOString(),
  expiresAt: new Date(NOW + 10 * 60 * 1000).toISOString(),
  paidAt: null,
};

const ok = (charge: PixCharge): ApiResult<PixCharge> => ({ ok: true, data: charge });

function setup(fetchCharge: () => Promise<ApiResult<PixCharge>>, extra: Partial<React.ComponentProps<typeof PixPayment>> = {}) {
  const onNewCharge = vi.fn();
  const onRestart = vi.fn();
  render(
    <PixPayment
      slug="escola-exemplo"
      charge={base}
      fetchCharge={fetchCharge}
      pollMs={15}
      onNewCharge={onNewCharge}
      onRestart={onRestart}
      {...extra}
    />,
  );
  return { onNewCharge, onRestart };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PixPayment: o status vem so da camada de dados", () => {
  it("enquanto a camada devolve PENDING, nunca aparece 'pago', mesmo apos interacoes", async () => {
    const fetchCharge = vi.fn().mockResolvedValue(ok(base));
    setup(fetchCharge);
    const user = userEvent.setup();

    expect(screen.getByText("Aguardando pagamento")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copiar código" }));
    await waitFor(() => expect(fetchCharge.mock.calls.length).toBeGreaterThan(3));

    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Ver comprovante" })).not.toBeInTheDocument();
    expect(screen.getByText("Aguardando pagamento")).toBeInTheDocument();
  });

  it("mostra a confirmacao somente quando a camada devolve PAID", async () => {
    let current: PixCharge = base;
    setup(() => Promise.resolve(ok(current)));

    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
    current = { ...base, status: "PAID", paidAt: new Date().toISOString() };

    expect(await screen.findByRole("heading", { name: "Pagamento confirmado" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver comprovante" })).toHaveAttribute(
      "href",
      "/apm/escola-exemplo/pedido/tok_test_000000000001",
    );
    // acessibilidade: o foco vai para o titulo e o status e anunciado
    await waitFor(() => expect(screen.getByRole("heading", { name: "Pagamento confirmado" })).toHaveFocus());
    expect(screen.getByRole("status", { name: "" })).toBeDefined();
  });

  it("status desconhecido devolvido pela camada de dados nunca vira pago", async () => {
    const weird = { ...base, status: "SETTLED" } as unknown as PixCharge;
    const fetchCharge = vi.fn().mockResolvedValue(ok(weird));
    setup(fetchCharge);
    await waitFor(() => expect(fetchCharge.mock.calls.length).toBeGreaterThan(2));
    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
    expect(screen.getByText("Status indisponível")).toBeInTheDocument();
  });

  it("EXPIRED: oferece gerar novo Pix e delega a criacao a camada de dados", async () => {
    const { onNewCharge } = setup(() => Promise.resolve(ok({ ...base, status: "EXPIRED" })));
    expect(await screen.findByRole("heading", { name: "Este Pix expirou" })).toBeInTheDocument();
    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Gerar novo Pix" }));
    expect(onNewCharge).toHaveBeenCalledTimes(1);
  });

  it("a contagem regressiva chegando a zero nao expira nem paga: continua aguardando a camada de dados", async () => {
    const past = { ...base, expiresAt: new Date(Date.now() - 1000).toISOString() };
    const fetchCharge = vi.fn().mockResolvedValue(ok(past));
    render(
      <PixPayment slug="escola-exemplo" charge={past} fetchCharge={fetchCharge} pollMs={15} onNewCharge={vi.fn()} onRestart={vi.fn()} />,
    );
    expect(screen.getAllByText("Prazo encerrado").length).toBeGreaterThan(0);
    expect(screen.getByText(/Conferindo com o banco/)).toBeInTheDocument();
    expect(screen.queryByText("Este Pix expirou")).not.toBeInTheDocument();
    expect(screen.queryByText("Pagamento confirmado")).not.toBeInTheDocument();
  });

  it("falha de rede nao quebra: avisa e continua tentando", async () => {
    let calls = 0;
    setup(() => {
      calls += 1;
      return Promise.resolve(calls < 3 ? ({ ok: false, error: { kind: "network" } } as ApiResult<PixCharge>) : ok(base));
    });
    expect(await screen.findByText(/Sem conexão para atualizar/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/Sem conexão para atualizar/)).not.toBeInTheDocument());
    expect(calls).toBeGreaterThanOrEqual(3);
  });

  it("cobranca inexistente (404) mostra mensagem e oferece recomecar", async () => {
    const { onRestart } = setup(() => Promise.resolve({ ok: false, error: { kind: "not-found", status: 404 } }));
    expect(await screen.findByRole("heading", { name: "Não encontramos este Pix" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Começar de novo" }));
    expect(onRestart).toHaveBeenCalled();
  });
});

function stubClipboard(writeText: () => Promise<void> | void) {
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
}

describe("PixPayment: copia e cola e marcacao de prototipo", () => {
  it("copia o codigo, confirma visualmente e anuncia para leitores de tela", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);
    setup(() => Promise.resolve(ok(base)));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copiar código" }));
    });
    expect(writeText).toHaveBeenCalledWith(base.payload);
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

  it("deixa claro que o Pix e de exemplo e o payload nao parece um Pix real", () => {
    setup(() => Promise.resolve(ok(base)));
    expect(screen.getByText(/Pix de exemplo para demonstração/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /QR Code de exemplo.*Não é um Pix real/ })).toBeInTheDocument();
    const code = screen.getByLabelText("Pix copia e cola") as HTMLTextAreaElement;
    expect(code.value).toMatch(/^PROTOTIPO\.NAO-E-PIX/);
    expect(code.value).not.toMatch(/^000201/);
    expect(code.readOnly).toBe(true);
  });
});
