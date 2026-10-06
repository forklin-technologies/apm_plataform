import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { ApiError, ApiResult } from "@/lib/api/types";
import { PasswordForm } from "./PasswordForm";

const NEW = "uma-senha-nova-bem-longa";
const fail = (error: ApiError): ApiResult<true> => ({ ok: false, error });
const problem = (status: number, code: string, extra: Partial<ApiError> = {}) => fail({ kind: "problem", status, code, ...extra });

async function fill(user: ReturnType<typeof userEvent.setup>, current: string, next: string, confirm = next) {
  await user.type(screen.getByLabelText("Senha atual"), current);
  await user.type(screen.getByLabelText("Nova senha"), next);
  await user.type(screen.getByLabelText("Repita a nova senha"), confirm);
  await user.click(screen.getByRole("button", { name: "Alterar senha" }));
}

afterEach(() => vi.restoreAllMocks());

describe("PasswordForm", () => {
  it("troca a senha pela API, limpa os campos e avisa que as outras sessoes foram encerradas", async () => {
    const change = vi.spyOn(api.auth, "changePassword").mockResolvedValue({ ok: true, data: true });
    const user = userEvent.setup();
    render(<PasswordForm />);
    await fill(user, "senha-atual-qualquer", NEW);
    expect(change).toHaveBeenCalledWith("senha-atual-qualquer", NEW);
    expect(await screen.findByRole("status")).toHaveTextContent("Senha alterada. As outras sessões foram encerradas.");
    for (const label of ["Senha atual", "Nova senha", "Repita a nova senha"]) expect(screen.getByLabelText(label)).toHaveValue("");
  });

  it("valida no cliente: curta, igual a atual, confirmacao diferente, atual vazia; nao chama a API", async () => {
    const change = vi.spyOn(api.auth, "changePassword");
    const user = userEvent.setup();
    render(<PasswordForm />);
    await user.click(screen.getByRole("button", { name: "Alterar senha" }));
    expect(screen.getByText("Informe a senha atual.")).toBeInTheDocument();

    await fill(user, "atual-bem-comprida-1", "curta");
    expect(screen.getByText("Use pelo menos 12 caracteres.")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Nova senha"));
    await user.clear(screen.getByLabelText("Repita a nova senha"));
    await user.clear(screen.getByLabelText("Senha atual"));
    await fill(user, NEW, NEW);
    expect(screen.getByText("A nova senha precisa ser diferente da atual.")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Nova senha"));
    await user.clear(screen.getByLabelText("Repita a nova senha"));
    await user.type(screen.getByLabelText("Nova senha"), "outra-senha-bem-longa-1");
    await user.type(screen.getByLabelText("Repita a nova senha"), "outra-senha-bem-longa-2");
    await user.click(screen.getByRole("button", { name: "Alterar senha" }));
    expect(screen.getByText("As senhas não são iguais.")).toBeInTheDocument();
    expect(change).not.toHaveBeenCalled();
  });

  it("senha atual incorreta (403): erro no campo e todos os campos limpos", async () => {
    vi.spyOn(api.auth, "changePassword").mockResolvedValue(problem(403, "current_password_incorrect"));
    const user = userEvent.setup();
    render(<PasswordForm />);
    await fill(user, "errada-errada-123", NEW);
    expect(await screen.findByText("A senha atual está incorreta.")).toBeInTheDocument();
    expect(screen.getByLabelText("Nova senha")).toHaveValue("");
  });

  it("422 weak_password: o texto do campo vem do code (too_short, same_as_current)", async () => {
    vi.spyOn(api.auth, "changePassword").mockResolvedValue(problem(422, "weak_password", { fields: [{ field: "new_password", code: "same_as_current" }] }));
    const user = userEvent.setup();
    render(<PasswordForm />);
    await fill(user, "senha-atual-bem-longa", NEW);
    expect(await screen.findByText("A nova senha precisa ser diferente da atual.")).toBeInTheDocument();
  });

  it("429, rede e erro generico em portugues", async () => {
    const change = vi.spyOn(api.auth, "changePassword").mockResolvedValueOnce(problem(429, "rate_limited", { retryAfterSeconds: 90 }));
    const user = userEvent.setup();
    render(<PasswordForm />);
    await fill(user, "senha-atual-bem-longa", NEW);
    expect(await screen.findByRole("alert")).toHaveTextContent("Aguarde 2 minutos");
    change.mockResolvedValueOnce(fail({ kind: "network" }));
    await fill(user, "senha-atual-bem-longa", NEW);
    expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível falar com o servidor");
  });

  it("senhas nao vao para console, storage nem URL", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const spies = [vi.spyOn(console, "error"), vi.spyOn(console, "warn"), vi.spyOn(console, "log")];
    vi.spyOn(api.auth, "changePassword").mockResolvedValue(fail({ kind: "network" }));
    const user = userEvent.setup();
    const { container } = render(<PasswordForm />);
    await fill(user, "segredo-atual-12345", "segredo-novo-bem-longo");
    await screen.findByRole("alert");
    expect(setItem).not.toHaveBeenCalled();
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    expect(container.querySelector("form")!.method).toBe("post");
    expect(window.location.href).not.toContain("segredo");
  });
});
