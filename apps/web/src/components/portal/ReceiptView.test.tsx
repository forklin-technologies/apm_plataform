import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Component, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiResult, Receipt } from "@/lib/api/types";
import { RECEIPT, TOKEN } from "@/test-utils/public-fixtures";

const getReceipt = vi.fn<(slug: string, token: string) => Promise<ApiResult<Receipt>>>();

vi.mock("@/lib/api", () => ({
  api: { public: { receipt: (...a: [string, string]) => getReceipt(...a) } },
}));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
}));

import { ReceiptView } from "./ReceiptView";

class Boundary extends Component<{ children: ReactNode }, { failed: string | null }> {
  override state = { failed: null as string | null };
  static getDerivedStateFromError(error: Error) {
    return { failed: error.message };
  }
  override render() {
    return this.state.failed ? <p>404:{this.state.failed}</p> : this.props.children;
  }
}

const renderView = (initial: React.ComponentProps<typeof ReceiptView>["initial"] = null) =>
  render(
    <Boundary>
      <ReceiptView slug="demo-aurora" token={TOKEN} initial={initial} />
    </Boundary>,
  );

const problem = (status: number, code: string, extra = {}): ApiResult<Receipt> => ({ ok: false, error: { kind: "problem", status, code, ...extra } });

beforeEach(() => {
  getReceipt.mockReset();
});

describe("ReceiptView (a API decide; o token so vive na URL e na memoria)", () => {
  it("sem resposta da API fica NEUTRO: sem 'Pago', sem valor", () => {
    getReceipt.mockReturnValue(new Promise(() => {}));
    renderView();
    expect(screen.getByRole("status")).toHaveTextContent("Conferindo seu comprovante");
    expect(document.body.textContent).not.toMatch(/\bPago\b|R\$/);
  });

  it("so mostra 'Pago' e os dados depois que a API responde 200", async () => {
    getReceipt.mockResolvedValue({ ok: true, data: RECEIPT });
    renderView();
    expect(await screen.findByRole("heading", { name: "Contribuição confirmada" })).toBeInTheDocument();
    expect(screen.getByText("APM-000026")).toBeInTheDocument();
    expect(screen.getByText("Pix")).toBeInTheDocument();
    expect(screen.getByText("Pago")).toBeInTheDocument();
    expect(getReceipt).toHaveBeenCalledWith("demo-aurora", TOKEN);
  });

  it("404 da API (token ruim, de outra escola ou inexistente): a mesma 404 estilizada", async () => {
    getReceipt.mockResolvedValue(problem(404, "not_found"));
    renderView();
    expect(await screen.findByText("404:NEXT_NOT_FOUND")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\bPago\b/);
  });

  it("409 payment_not_confirmed: nao afirma pagamento e permite atualizar", async () => {
    getReceipt.mockResolvedValueOnce(problem(409, "payment_not_confirmed")).mockResolvedValueOnce({ ok: true, data: RECEIPT });
    renderView();
    expect(await screen.findByRole("heading", { name: "Este pagamento ainda não foi confirmado" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\bPago\b/);
    await userEvent.setup().click(screen.getByRole("button", { name: "Atualizar" }));
    expect(await screen.findByRole("heading", { name: "Contribuição confirmada" })).toBeInTheDocument();
  });

  it("429: texto amigavel com o tempo de espera", async () => {
    getReceipt.mockResolvedValue(problem(429, "rate_limited", { retryAfterSeconds: 45 }));
    renderView();
    expect(await screen.findByRole("alert")).toHaveTextContent("Aguarde 45 segundos");
  });

  it("falha de rede: avisa e permite tentar de novo", async () => {
    getReceipt.mockResolvedValueOnce({ ok: false, error: { kind: "network" } }).mockResolvedValueOnce({ ok: true, data: RECEIPT });
    renderView();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Tentar de novo" }));
    expect(await screen.findByRole("heading", { name: "Contribuição confirmada" })).toBeInTheDocument();
    expect(getReceipt).toHaveBeenCalledTimes(2);
  });

  it("resposta 200 que o servidor ja trouxe aparece direto, sem consultar de novo", async () => {
    renderView({ receipt: RECEIPT });
    expect(screen.getByRole("heading", { name: "Contribuição confirmada" })).toBeInTheDocument();
    await waitFor(() => expect(getReceipt).not.toHaveBeenCalled());
  });

  it("409 que o servidor ja trouxe aparece direto como 'ainda nao confirmado'", () => {
    renderView({ unconfirmed: true });
    expect(screen.getByRole("heading", { name: "Este pagamento ainda não foi confirmado" })).toBeInTheDocument();
    expect(getReceipt).not.toHaveBeenCalled();
  });

  it("nunca grava o token em localStorage nem sessionStorage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    getReceipt.mockResolvedValue({ ok: true, data: RECEIPT });
    renderView();
    await screen.findByRole("heading", { name: "Contribuição confirmada" });
    expect(setItem).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });
});
