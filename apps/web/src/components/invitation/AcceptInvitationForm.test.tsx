import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { parseMembership } from "@/lib/api/auth";
import type { AcceptedInvitation, ApiError, ApiResult, Membership } from "@/lib/api/types";
import { SCHOOL_MEMBERSHIP } from "@/test-utils/auth-fixtures";
import { AcceptInvitationForm } from "./AcceptInvitationForm";

const TOKEN = "t".repeat(43);
const ACCEPTED: AcceptedInvitation = { userId: "u", membership: parseMembership(SCHOOL_MEMBERSHIP) as Membership };
const OK: ApiResult<AcceptedInvitation> = { ok: true, data: ACCEPTED };
const problem = (code: string, status: number, extra: Partial<ApiError> = {}): ApiResult<AcceptedInvitation> => ({
  ok: false,
  error: { kind: "problem", status, code, ...extra },
});
const SIGNED_IN = { fullName: "Pessoa Logada", email: "logada@example.test" };

afterEach(() => vi.restoreAllMocks());

async function fillNew(name: string, password: string, confirm = password) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Nome completo"), name);
  await user.type(screen.getByLabelText("Senha", { exact: true }), password);
  await user.type(screen.getByLabelText("Repita a senha"), confirm);
  await user.click(screen.getByRole("button", { name: "Criar conta e aceitar" }));
}

describe("AcceptInvitationForm: pessoa nova", () => {
  it("envia token, nome e senha, mostra o convite aceito e NAO faz login", async () => {
    const accept = vi.spyOn(api.auth, "acceptInvitation").mockResolvedValue(OK);
    const login = vi.spyOn(api.auth, "login");
    render(<AcceptInvitationForm token={TOKEN} signedInAs={null} />);
    await fillNew("Pessoa Nova", "uma-senha-bem-longa-1");

    expect(accept).toHaveBeenCalledWith({ token: TOKEN, fullName: "Pessoa Nova", password: "uma-senha-bem-longa-1" });
    expect(await screen.findByRole("status")).toHaveTextContent("Convite aceito");
    expect(screen.getByRole("status")).toHaveTextContent("Escola Teste");
    expect(screen.getByRole("status")).toHaveTextContent("Tesouraria");
    expect(screen.getByRole("link", { name: "Entrar" })).toHaveAttribute("href", "/login");
    expect(login).not.toHaveBeenCalled();
  });

  it("valida no cliente: nome vazio, senha curta e senhas diferentes; nao chama a API", async () => {
    const accept = vi.spyOn(api.auth, "acceptInvitation").mockResolvedValue(OK);
    render(<AcceptInvitationForm token={TOKEN} signedInAs={null} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Criar conta e aceitar" }));
    expect(screen.getByText("Informe o seu nome completo.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Nome completo"), "Pessoa");
    await user.type(screen.getByLabelText("Senha", { exact: true }), "curta");
    await user.click(screen.getByRole("button", { name: "Criar conta e aceitar" }));
    expect(screen.getByText("Use pelo menos 12 caracteres.")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Senha", { exact: true }));
    await user.type(screen.getByLabelText("Senha", { exact: true }), "uma-senha-bem-longa-1");
    await user.type(screen.getByLabelText("Repita a senha"), "outra-senha-bem-longa-2");
    await user.click(screen.getByRole("button", { name: "Criar conta e aceitar" }));
    expect(screen.getByText("As senhas não são iguais.")).toBeInTheDocument();
    expect(accept).not.toHaveBeenCalled();
  });

  it("invitation_invalid: texto unico, sem dizer por que; senhas apagadas", async () => {
    vi.spyOn(api.auth, "acceptInvitation").mockResolvedValue(problem("invitation_invalid", 400));
    render(<AcceptInvitationForm token={TOKEN} signedInAs={null} />);
    await fillNew("Pessoa Nova", "uma-senha-bem-longa-1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Este convite não é válido");
    expect(screen.getByLabelText("Senha", { exact: true })).toHaveValue("");
    expect(screen.getByLabelText("Repita a senha")).toHaveValue("");
  });

  it("account_exists_login_required: explica e oferece o login que volta para este convite", async () => {
    vi.spyOn(api.auth, "acceptInvitation").mockResolvedValue(problem("account_exists_login_required", 409));
    render(<AcceptInvitationForm token={TOKEN} signedInAs={null} />);
    await fillNew("Pessoa Nova", "uma-senha-bem-longa-1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Já existe uma conta com o e-mail deste convite");
    const link = screen.getByRole("link", { name: "Ir para a entrada" });
    expect(link).toHaveAttribute("href", `/login?next=${encodeURIComponent(`/accept-invitation?token=${TOKEN}`)}`);
  });

  it("weak_password do servidor: mostra o texto do campo a partir do code, nao da mensagem", async () => {
    vi.spyOn(api.auth, "acceptInvitation").mockResolvedValue(
      problem("weak_password", 422, { fields: [{ field: "password", code: "too_short" }] }),
    );
    render(<AcceptInvitationForm token={TOKEN} signedInAs={null} />);
    await fillNew("Pessoa Nova", "uma-senha-bem-longa-1");
    expect(await screen.findByRole("alert")).toHaveTextContent("12 a 128");
    expect(screen.getByText("Use pelo menos 12 caracteres.")).toBeInTheDocument();
  });

  it("limite de tentativas mostra o tempo", async () => {
    vi.spyOn(api.auth, "acceptInvitation").mockResolvedValue(problem("rate_limited", 429, { retryAfterSeconds: 120 }));
    render(<AcceptInvitationForm token={TOKEN} signedInAs={null} />);
    await fillNew("Pessoa Nova", "uma-senha-bem-longa-1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Aguarde 2 minutos");
  });

  it("nao escreve senha nem token no console", async () => {
    const spies = [vi.spyOn(console, "error"), vi.spyOn(console, "warn"), vi.spyOn(console, "log")];
    vi.spyOn(api.auth, "acceptInvitation").mockResolvedValue({ ok: false, error: { kind: "network" } });
    render(<AcceptInvitationForm token={TOKEN} signedInAs={null} />);
    await fillNew("Pessoa Nova", "segredo-que-nao-pode-vazar");
    expect(await screen.findByRole("alert")).toHaveTextContent("conexão");
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });
});

describe("AcceptInvitationForm: conta que ja existe", () => {
  it("logada: envia SO o token (sem nome nem senha) e diz que foi para a conta atual", async () => {
    const accept = vi.spyOn(api.auth, "acceptInvitation").mockResolvedValue(OK);
    render(<AcceptInvitationForm token={TOKEN} signedInAs={SIGNED_IN} />);
    expect(screen.getByText(/Você está conectado como/)).toHaveTextContent("Pessoa Logada");
    expect(screen.queryByLabelText("Nome completo")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Senha", { exact: true })).not.toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "Aceitar o convite" }));
    expect(accept).toHaveBeenCalledWith({ token: TOKEN });
    expect(await screen.findByRole("status")).toHaveTextContent("O vínculo foi adicionado à sua conta");
    expect(screen.getByRole("link", { name: "Ir para o painel" })).toHaveAttribute("href", "/painel");
  });

  it("conta de outra pessoa (409): mostra o texto e o caminho para entrar com a conta convidada", async () => {
    vi.spyOn(api.auth, "acceptInvitation").mockResolvedValue(problem("account_exists_login_required", 409));
    render(<AcceptInvitationForm token={TOKEN} signedInAs={SIGNED_IN} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Aceitar o convite" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("saia da conta atual");
    expect(screen.getByRole("link", { name: "Ir para a entrada" })).toBeInTheDocument();
  });

  it("sem sessao: 'Já tenho conta' manda para o login e nao tem botao de enviar", async () => {
    const accept = vi.spyOn(api.auth, "acceptInvitation");
    render(<AcceptInvitationForm token={TOKEN} signedInAs={null} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Já tenho conta" }));
    const link = screen.getByRole("link", { name: "Entrar para aceitar" });
    expect(link).toHaveAttribute("href", `/login?next=${encodeURIComponent(`/accept-invitation?token=${TOKEN}`)}`);
    expect(screen.queryByRole("button", { name: "Aceitar o convite" })).not.toBeInTheDocument();
    expect(accept).not.toHaveBeenCalled();
  });
});
