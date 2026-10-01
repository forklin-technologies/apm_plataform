import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReadinessResult } from "@/lib/api/types";
import { ApiStatusChip } from "./ApiStatusChip";

const READY: ReadinessResult = { state: "ready" };
const DOWN: ReadinessResult = { state: "unavailable", reason: "unreachable" };

afterEach(() => vi.useRealTimers());

describe("ApiStatusChip", () => {
  it("passa por verificando e termina em 'API no ar'", async () => {
    let resolve!: (r: ReadinessResult) => void;
    const check = vi.fn(() => new Promise<ReadinessResult>((r) => (resolve = r)));
    render(<ApiStatusChip check={check} />);

    const chip = screen.getByRole("status");
    expect(chip).toHaveAttribute("data-state", "checking");
    expect(chip).toHaveTextContent("Verificando a API");

    await act(async () => resolve(READY));
    expect(screen.getByRole("status")).toHaveAttribute("data-state", "ready");
    expect(screen.getByRole("status")).toHaveTextContent("API no ar");
  });

  it("API fora do ar: mostra 'indisponivel' sem lancar erro e sem escrever no console", async () => {
    const errors = [vi.spyOn(console, "error"), vi.spyOn(console, "warn")];
    const check = vi.fn().mockResolvedValue(DOWN);
    render(<ApiStatusChip check={check} />);
    expect(await screen.findByText("API indisponível")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Tentar de novo/ })).toBeInTheDocument();
    for (const spy of errors) expect(spy).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  it("uma excecao inesperada na consulta vira 'indisponivel' (nao quebra a pagina)", async () => {
    const check = vi.fn().mockRejectedValue(new Error("boom"));
    render(<ApiStatusChip check={check} />);
    expect(await screen.findByText("API indisponível")).toBeInTheDocument();
  });

  it("'Tentar de novo' consulta na hora e se recupera", async () => {
    const check = vi.fn().mockResolvedValueOnce(DOWN).mockResolvedValue(READY);
    render(<ApiStatusChip check={check} />);
    await userEvent.setup().click(await screen.findByRole("button", { name: /Tentar de novo/ }));
    expect(await screen.findByText("API no ar")).toBeInTheDocument();
    expect(check).toHaveBeenCalledTimes(2);
  });

  it("A API respondeu mas nao esta pronta: diferencia no texto de apoio", async () => {
    const check = vi.fn().mockResolvedValue({ state: "unavailable", reason: "not-ready" } satisfies ReadinessResult);
    render(<ApiStatusChip check={check} showDetail />);
    expect(await screen.findByText("A API respondeu, mas ainda não está pronta para atender.")).toBeInTheDocument();
  });
});

describe("ApiStatusChip: reconsulta em intervalo moderado", () => {
  beforeEach(() => vi.useFakeTimers());

  it("no ar: reconsulta a cada 30 s", async () => {
    const check = vi.fn().mockResolvedValue(READY);
    render(<ApiStatusChip check={check} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(check).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_000);
    });
    expect(check).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });
    expect(check).toHaveBeenCalledTimes(2);
  });

  it("fora do ar: espaca as consultas (15 s, 30 s, 60 s, teto de 60 s) para nao inundar o console", async () => {
    const check = vi.fn().mockResolvedValue(DOWN);
    render(<ApiStatusChip check={check} />);
    const advance = async (ms: number) =>
      act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
      });
    await advance(0);
    expect(check).toHaveBeenCalledTimes(1);
    await advance(15_000);
    expect(check).toHaveBeenCalledTimes(2);
    await advance(29_000);
    expect(check).toHaveBeenCalledTimes(2);
    await advance(1_000);
    expect(check).toHaveBeenCalledTimes(3);
    await advance(60_000);
    expect(check).toHaveBeenCalledTimes(4);
    await advance(60_000);
    expect(check).toHaveBeenCalledTimes(5);
    // em 10 minutos fora do ar: no maximo ~12 consultas
    await advance(600_000);
    expect(check.mock.calls.length).toBeLessThanOrEqual(15);
  });

  it("com o documento oculto (portal do Maestri) continua consultando, so mais devagar (60 s)", async () => {
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    try {
      const check = vi.fn().mockResolvedValue(READY);
      render(<ApiStatusChip check={check} />);
      const advance = async (ms: number) =>
        act(async () => {
          await vi.advanceTimersByTimeAsync(ms);
        });
      await advance(0);
      expect(check).toHaveBeenCalledTimes(1);
      await advance(45_000); // o intervalo normal (30 s) NAO dispara oculto
      expect(check).toHaveBeenCalledTimes(1);
      await advance(16_000); // 61 s: dispara (nunca para)
      expect(check).toHaveBeenCalledTimes(2);
    } finally {
      Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    }
  });

  it("para de consultar ao desmontar", async () => {
    const check = vi.fn().mockResolvedValue(READY);
    const { unmount } = render(<ApiStatusChip check={check} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(check).toHaveBeenCalledTimes(1);
  });
});
