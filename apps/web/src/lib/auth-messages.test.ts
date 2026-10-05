import { describe, expect, it } from "vitest";
import type { ApiError } from "@/lib/api/types";
import { describeAuthError, describeFieldError, formatWait, isSessionLost } from "./auth-messages";

const problem = (code: string, extra: Partial<ApiError> = {}): ApiError => ({ kind: "problem", status: 400, code, ...extra });

describe("formatWait", () => {
  it("segundos, minutos arredondados para cima e padrao sem valor", () => {
    expect(formatWait(1)).toBe("1 segundo");
    expect(formatWait(30)).toBe("30 segundos");
    expect(formatWait(60)).toBe("1 minuto");
    expect(formatWait(61)).toBe("2 minutos");
    expect(formatWait(900)).toBe("15 minutos");
    expect(formatWait(undefined)).toBe("alguns minutos");
    expect(formatWait(0)).toBe("alguns minutos");
  });
});

describe("describeAuthError", () => {
  it("credencial invalida: um texto so, que nao diz se o e-mail existe", () => {
    const text = describeAuthError(problem("invalid_credentials", { status: 401 }), "login");
    expect(text).toBe("E-mail ou senha incorretos. Confira os dados e tente de novo.");
    expect(text).not.toMatch(/n[ãa]o existe|inativ|bloquead/i);
  });

  it("bloqueio por tentativas mostra o tempo restante vindo do Retry-After", () => {
    expect(describeAuthError(problem("rate_limited", { retryAfterSeconds: 30 }), "login")).toContain("30 segundos");
    expect(describeAuthError(problem("rate_limited", { retryAfterSeconds: 125 }), "login")).toContain("3 minutos");
    expect(describeAuthError(problem("rate_limited"), "login")).toContain("alguns minutos");
  });

  it("origem e CSRF pedem para recarregar a pagina", () => {
    for (const code of ["origin_not_allowed", "csrf_failed"]) {
      expect(describeAuthError(problem(code, { status: 403 }), "login")).toContain("Recarregue a página");
    }
  });

  it("convite: invalido, conta existente e senha fraca", () => {
    expect(describeAuthError(problem("invitation_invalid"), "invitation")).toContain("Este convite não é válido");
    expect(describeAuthError(problem("account_exists_login_required", { status: 409 }), "invitation")).toContain("Entre com ela");
    expect(describeAuthError(problem("weak_password", { status: 422 }), "invitation")).toContain("12 a 128");
  });

  it("contexto, sessao e permissao", () => {
    expect(describeAuthError(problem("context_not_allowed"), "context")).toContain("Você não tem acesso");
    expect(describeAuthError(problem("context_required"), "context")).toContain("Escolha onde");
    expect(describeAuthError(problem("session_revoked"), "context")).toContain("sessão terminou");
    expect(describeAuthError(problem("permission_denied"), "context")).toContain("permissão");
  });

  it("falha de rede, timeout, 5xx e codigo desconhecido: textos genericos em portugues", () => {
    expect(describeAuthError({ kind: "network" }, "login")).toContain("conexão");
    expect(describeAuthError({ kind: "timeout" }, "login")).toContain("conexão");
    expect(describeAuthError({ kind: "http", status: 502 }, "login")).toContain("do nosso lado");
    expect(describeAuthError(problem("internal_error", { status: 500 }), "login")).toContain("do nosso lado");
    expect(describeAuthError(problem("codigo_novo_do_servidor"), "login")).toContain("do nosso lado");
  });

  it("nunca repassa texto do servidor", () => {
    const hostile = { kind: "problem", status: 401, code: "invalid_credentials", title: "Invalid e-mail or password", detail: "x" } as ApiError;
    expect(describeAuthError(hostile, "login")).not.toMatch(/Invalid|detail/);
  });
});

describe("isSessionLost e describeFieldError", () => {
  it("so unauthenticated e session_revoked contam como sessao perdida", () => {
    expect(isSessionLost(problem("unauthenticated", { status: 401 }))).toBe(true);
    expect(isSessionLost(problem("session_revoked", { status: 401 }))).toBe(true);
    expect(isSessionLost(problem("invalid_credentials", { status: 401 }))).toBe(false);
    expect(isSessionLost({ kind: "network" })).toBe(false);
  });

  it("campos: codigos fixos conhecidos e texto neutro para os demais", () => {
    expect(describeFieldError("too_short")).toContain("12 caracteres");
    expect(describeFieldError("missing")).toBe("Preencha este campo.");
    expect(describeFieldError("qualquer_outro")).toBe("Confira este campo.");
  });
});
