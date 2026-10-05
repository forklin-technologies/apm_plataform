import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { ApiError, ApiResult, Session } from "@/lib/api/types";

const router = vi.hoisted(() => ({ replace: vi.fn(), refresh: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

import { LoginForm } from "./LoginForm";

const SESSION = { user: { id: "u", email: "a@example.test", fullName: "A" } } as unknown as Session;
const fail = (error: ApiError): ApiResult<Session> => ({ ok: false, error });
const problem = (code: string, status: number, extra: Partial<ApiError> = {}) => fail({ kind: "problem", status, code, ...extra });

async function fill(email: string, password: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("E-mail"), email);
  await user.type(screen.getByLabelText("Senha"), password);
  await user.click(screen.getByRole("button", { name: "Entrar" }));
}

beforeEach(() => {
  router.replace.mockReset();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("LoginForm (login real)", () => {
  it("campos vazios: avisa, nao chama a API", async () => {
    const login = vi.spyOn(api.auth, "login");
    render(<LoginForm />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Entrar" }));
    expect(screen.getByText("Informe o seu e-mail.")).toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
  });

  it("login ok: chama a API com e-mail e senha e vai para o destino", async () => {
    const login = vi.spyOn(api.auth, "login").mockResolvedValue({ ok: true, data: SESSION });
    render(<LoginForm next="/painel?tipo=despesas" />);
    await fill("ana@example.test", "uma-senha-qualquer");
    expect(login).toHaveBeenCalledWith("ana@example.test", "uma-senha-qualquer");
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/painel?tipo=despesas"));
  });

  it("credencial invalida: texto em portugues, senha apagada, nada de redirecionar", async () => {
    vi.spyOn(api.auth, "login").mockResolvedValue(problem("invalid_credentials", 401));
    render(<LoginForm />);
    await fill("ana@example.test", "senha-errada-123");
    expect(await screen.findByRole("alert")).toHaveTextContent("E-mail ou senha incorretos");
    expect(screen.getByLabelText("Senha")).toHaveValue("");
    expect(screen.getByLabelText("E-mail")).toHaveValue("ana@example.test");
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("e-mail que nao existe e senha errada mostram EXATAMENTE o mesmo texto", async () => {
    const texts: string[] = [];
    for (const email of ["existe@example.test", "naoexiste@example.test"]) {
      vi.spyOn(api.auth, "login").mockResolvedValue(problem("invalid_credentials", 401));
      const { unmount } = render(<LoginForm />);
      await fill(email, "qualquer-senha-123");
      texts.push((await screen.findByRole("alert")).textContent ?? "");
      unmount();
    }
    expect(texts[0]).toBe(texts[1]);
  });

  it("bloqueio por tentativas: mostra o tempo, desativa o botao e reativa depois", async () => {
    vi.useFakeTimers();
    vi.spyOn(api.auth, "login").mockResolvedValue(problem("rate_limited", 429, { retryAfterSeconds: 30 }));
    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText("E-mail"), { target: { value: "ana@example.test" } });
    fireEvent.change(screen.getByLabelText("Senha"), { target: { value: "senha-errada-123" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Aguarde 30 segundos");
    expect(screen.getByRole("button", { name: "Entrar" })).toBeDisabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_000);
    });
    expect(screen.getByRole("button", { name: "Entrar" })).toBeDisabled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });
    expect(screen.getByRole("button", { name: "Entrar" })).toBeEnabled();
  });

  it("origem nao permitida, rede e erro do servidor: textos proprios", async () => {
    const cases: Array<[ApiResult<Session>, string]> = [
      [problem("origin_not_allowed", 403), "Recarregue a página"],
      [fail({ kind: "network" }), "conexão"],
      [fail({ kind: "http", status: 502 }), "do nosso lado"],
    ];
    for (const [result, text] of cases) {
      vi.spyOn(api.auth, "login").mockResolvedValue(result);
      const { unmount } = render(<LoginForm />);
      await fill("ana@example.test", "senha-123456789");
      expect(await screen.findByRole("alert")).toHaveTextContent(text);
      unmount();
    }
  });

  it("nunca mostra title/detail do servidor nem escreve a senha no console", async () => {
    const spies = [vi.spyOn(console, "error"), vi.spyOn(console, "warn"), vi.spyOn(console, "log")];
    vi.spyOn(api.auth, "login").mockResolvedValue(
      fail({ kind: "problem", status: 401, code: "invalid_credentials", title: "Invalid e-mail or password" } as ApiError),
    );
    render(<LoginForm />);
    await fill("ana@example.test", "segredo-que-nao-pode-vazar");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toMatch(/Invalid|segredo/);
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });

  it("form method=post com preventDefault: a senha nunca iria para a URL", () => {
    const { container } = render(<LoginForm />);
    const form = container.querySelector("form")!;
    expect(form.method).toBe("post");
    expect(fireEvent.submit(form)).toBe(false);
  });

  it("campos com autocomplete de login e senha sem corretor ortografico", () => {
    render(<LoginForm />);
    expect(screen.getByLabelText("E-mail")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Senha")).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByLabelText("Senha")).toHaveAttribute("type", "password");
  });
});
