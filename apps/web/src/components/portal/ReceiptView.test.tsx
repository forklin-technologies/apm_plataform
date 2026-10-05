import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Component, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiResult, Receipt } from "@/lib/api/types";

const getReceipt = vi.fn<(slug: string, token: string) => Promise<ApiResult<Receipt>>>();
const clearPersonalData = vi.fn<(slug: string, token: string) => Promise<void>>();

vi.mock("@/lib/api", () => ({
  api: { contributions: { getReceipt: (...a: [string, string]) => getReceipt(...a), clearPersonalData: (...a: [string, string]) => clearPersonalData(...a) } },
}));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
}));

import { ReceiptView } from "./ReceiptView";

const TOKEN = "AbCdEfGhIjKlMnOpQrStUv";
const receipt: Receipt = {
  token: TOKEN,
  number: "2026-000001",
  schoolName: "Escola Exemplo",
  apmName: "APM da Escola Exemplo",
  description: "Cota anual",
  amountCents: 20000,
  status: "PAID",
  paidAt: "2026-09-30T16:42:00-03:00",
  identification: { guardianName: "Ana Lima", studentName: "Davi Lima" },
};

class Boundary extends Component<{ children: ReactNode }, { failed: string | null }> {
  override state = { failed: null as string | null };
  static getDerivedStateFromError(error: Error) {
    return { failed: error.message };
  }
  override render() {
    return this.state.failed ? <p>404:{this.state.failed}</p> : this.props.children;
  }
}

const renderView = (initial: Receipt | null = null) =>
  render(
    <Boundary>
      <ReceiptView slug="escola-exemplo" token={TOKEN} initial={initial} />
    </Boundary>,
  );

beforeEach(() => {
  getReceipt.mockReset();
  clearPersonalData.mockReset().mockResolvedValue(undefined);
});

describe("ReceiptView (N1, N8)", () => {
  it("sem resposta da camada de dados fica NEUTRO: sem 'Pago', sem nome, sem valor, sem apagar nada", () => {
    getReceipt.mockReturnValue(new Promise(() => {}));
    renderView();
    expect(screen.getByRole("status")).toHaveTextContent("Conferindo seu comprovante");
    expect(document.body.textContent).not.toMatch(/\bPago\b|Ana Lima|R\$/);
    expect(clearPersonalData).not.toHaveBeenCalled();
  });

  it("so mostra 'Pago' e os dados depois que a camada de dados responde, e DEPOIS apaga os dados pessoais", async () => {
    getReceipt.mockResolvedValue({ ok: true, data: receipt });
    renderView();
    expect(await screen.findByRole("heading", { name: "Contribuição confirmada" })).toBeInTheDocument();
    expect(screen.getByText("Ana Lima")).toBeInTheDocument();
    expect(screen.getByText("Pago")).toBeInTheDocument();
    await waitFor(() => expect(clearPersonalData).toHaveBeenCalledWith("escola-exemplo", TOKEN));
  });

  it("token desconhecido para a camada de dados: a mesma 404 estilizada (notFound)", async () => {
    getReceipt.mockResolvedValue({ ok: false, error: { kind: "not-found", status: 404 } });
    renderView();
    expect(await screen.findByText("404:NEXT_NOT_FOUND")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\bPago\b/);
    expect(clearPersonalData).not.toHaveBeenCalled();
  });

  it("pedido ainda nao pago (409): nao afirma pagamento", async () => {
    getReceipt.mockResolvedValue({ ok: false, error: { kind: "http", status: 409 } });
    renderView();
    expect(await screen.findByRole("heading", { name: "Este pagamento ainda não foi confirmado" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\bPago\b/);
  });

  it("falha de rede: avisa e permite tentar de novo", async () => {
    getReceipt.mockResolvedValueOnce({ ok: false, error: { kind: "network" } }).mockResolvedValueOnce({ ok: true, data: receipt });
    renderView();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Tentar de novo" }));
    expect(await screen.findByRole("heading", { name: "Contribuição confirmada" })).toBeInTheDocument();
    expect(getReceipt).toHaveBeenCalledTimes(2);
  });

  it("o comprovante de exemplo (initial vindo do servidor) aparece direto", () => {
    getReceipt.mockReturnValue(new Promise(() => {}));
    renderView(receipt);
    expect(screen.getByRole("heading", { name: "Contribuição confirmada" })).toBeInTheDocument();
  });
});
