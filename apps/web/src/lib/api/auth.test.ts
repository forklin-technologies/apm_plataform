import { describe, expect, it, vi } from "vitest";
import { ORG_MEMBERSHIP, SCHOOL_MEMBERSHIP, json, problem, sessionBody } from "@/test-utils/auth-fixtures";
import { acceptInvitation, changePassword, getMe, login, logout, parseSession, switchContext } from "./auth";

const TOKEN = "t".repeat(32);

function body(call: unknown[]): Record<string, unknown> {
  return JSON.parse((call[1] as RequestInit).body as string);
}

describe("parseSession (snake_case da API -> camelCase do site)", () => {
  it("converte usuario, vinculo ativo com permissoes, vinculos e prazos", () => {
    const session = parseSession(sessionBody());
    expect(session).toEqual({
      user: { id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", email: "pessoa@example.test", fullName: "Pessoa de Teste" },
      activeMembership: {
        membershipId: ORG_MEMBERSHIP.membership_id,
        organization: ORG_MEMBERSHIP.organization,
        school: null,
        role: "organization_admin",
        permissions: ["reports:read"],
      },
      memberships: [
        { membershipId: ORG_MEMBERSHIP.membership_id, organization: ORG_MEMBERSHIP.organization, school: null, role: "organization_admin" },
        {
          membershipId: SCHOOL_MEMBERSHIP.membership_id,
          organization: SCHOOL_MEMBERSHIP.organization,
          school: SCHOOL_MEMBERSHIP.school,
          role: "treasurer",
        },
      ],
      expiresAt: "2026-10-06T00:00:00Z",
      idleTimeoutSeconds: 1800,
    });
  });

  it("nao carrega o token CSRF para dentro do objeto de sessao", () => {
    expect(JSON.stringify(parseSession(sessionBody()))).not.toContain("csrf");
  });

  it("aceita sessao sem vinculo ativo (varios vinculos, nenhum escolhido)", () => {
    expect(parseSession(sessionBody({ active_membership: null }))?.activeMembership).toBeNull();
  });

  it("recusa formato inesperado em vez de adivinhar", () => {
    expect(parseSession(null)).toBeNull();
    expect(parseSession({})).toBeNull();
    expect(parseSession(sessionBody({ user: { id: "x" } }))).toBeNull();
    expect(parseSession(sessionBody({ memberships: [{ ...ORG_MEMBERSHIP, role: "root" }] }))).toBeNull();
    expect(parseSession(sessionBody({ memberships: [{ ...ORG_MEMBERSHIP, school: { id: "x" } }] }))).toBeNull();
    expect(parseSession(sessionBody({ active_membership: { ...ORG_MEMBERSHIP } }))).toBeNull(); // sem permissions
    expect(parseSession(sessionBody({ session: { expires_at: "x" } }))).toBeNull();
  });
});

describe("login", () => {
  it("POST /api/v1/auth/login com e-mail aparado e a senha so no corpo", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(sessionBody()));
    const result = await login("  Pessoa@Example.test ", "uma-senha-qualquer", { fetchImpl, csrfToken: null });
    expect(result.ok && result.data.user.fullName).toBe("Pessoa de Teste");
    const call = fetchImpl.mock.calls[0]!;
    expect(call[0]).toBe("/api/v1/auth/login");
    expect((call[1] as RequestInit).method).toBe("POST");
    expect(body(call)).toEqual({ email: "Pessoa@Example.test", password: "uma-senha-qualquer" });
    expect(String(call[0])).not.toContain("uma-senha");
  });

  it("401 invalid_credentials, 429 com Retry-After e 403 de origem viram erro com code", async () => {
    const r401 = await login("a@b.c", "x", { fetchImpl: vi.fn().mockResolvedValue(problem("invalid_credentials", 401)) });
    const r429 = await login("a@b.c", "x", { fetchImpl: vi.fn().mockResolvedValue(problem("rate_limited", 429, {}, { "Retry-After": "45" })) });
    const r403 = await login("a@b.c", "x", { fetchImpl: vi.fn().mockResolvedValue(problem("origin_not_allowed", 403)) });
    expect(r401).toEqual({ ok: false, error: { kind: "problem", status: 401, code: "invalid_credentials" } });
    expect(r429).toEqual({ ok: false, error: { kind: "problem", status: 429, code: "rate_limited", retryAfterSeconds: 45 } });
    expect(r403).toMatchObject({ error: { code: "origin_not_allowed" } });
  });

  it("200 com corpo estranho e invalid-response", async () => {
    const result = await login("a@b.c", "x", { fetchImpl: vi.fn().mockResolvedValue(json({ ok: true })) });
    expect(result).toEqual({ ok: false, error: { kind: "invalid-response", status: 200 } });
  });
});

describe("logout, me, context, password", () => {
  it("logout: POST com X-CSRF-Token; 204 e sucesso; 403 csrf_failed e erro", async () => {
    const ok = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    expect(await logout({ fetchImpl: ok, csrfToken: TOKEN })).toEqual({ ok: true, data: true });
    expect(ok.mock.calls[0]![0]).toBe("/api/v1/auth/logout");
    expect((ok.mock.calls[0]![1] as RequestInit).headers).toMatchObject({ "X-CSRF-Token": TOKEN });
    const denied = await logout({ fetchImpl: vi.fn().mockResolvedValue(problem("csrf_failed", 403)), csrfToken: null });
    expect(denied).toMatchObject({ ok: false, error: { code: "csrf_failed" } });
  });

  it("me: GET sem CSRF; 401 vira erro unauthenticated", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(sessionBody()));
    const result = await getMe({ fetchImpl });
    expect(result.ok).toBe(true);
    expect(fetchImpl.mock.calls[0]![0]).toBe("/api/v1/auth/me");
    expect((fetchImpl.mock.calls[0]![1] as RequestInit).method).toBe("GET");
    const anonymous = await getMe({ fetchImpl: vi.fn().mockResolvedValue(problem("unauthenticated", 401)) });
    expect(anonymous).toMatchObject({ ok: false, error: { code: "unauthenticated", status: 401 } });
  });

  it("context: manda so o id do vinculo (nunca organizacao nem escola) e devolve a nova sessao", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(sessionBody()));
    const result = await switchContext(SCHOOL_MEMBERSHIP.membership_id, { fetchImpl, csrfToken: TOKEN });
    expect(result.ok).toBe(true);
    expect(fetchImpl.mock.calls[0]![0]).toBe("/api/v1/auth/context");
    expect(body(fetchImpl.mock.calls[0]!)).toEqual({ membership_id: SCHOOL_MEMBERSHIP.membership_id });
    const denied = await switchContext("x", { fetchImpl: vi.fn().mockResolvedValue(problem("context_not_allowed", 403)) });
    expect(denied).toMatchObject({ error: { code: "context_not_allowed" } });
  });

  it("password: corpo em snake_case, 204 e sucesso, 403 da senha atual e erro", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    expect(await changePassword("senha-atual-123", "senha-nova-456", { fetchImpl })).toEqual({ ok: true, data: true });
    expect(body(fetchImpl.mock.calls[0]!)).toEqual({ current_password: "senha-atual-123", new_password: "senha-nova-456" });
    const wrong = await changePassword("a", "b", { fetchImpl: vi.fn().mockResolvedValue(problem("current_password_incorrect", 403)) });
    expect(wrong).toMatchObject({ error: { code: "current_password_incorrect" } });
  });
});

describe("acceptInvitation", () => {
  const accepted = { user_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", membership: SCHOOL_MEMBERSHIP };

  it("pessoa nova: token, full_name e password no corpo; 201 vira camelCase", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(accepted, 201));
    const result = await acceptInvitation({ token: "tok", fullName: "Pessoa Nova", password: "senha-bem-longa-1" }, { fetchImpl });
    expect(body(fetchImpl.mock.calls[0]!)).toEqual({ token: "tok", full_name: "Pessoa Nova", password: "senha-bem-longa-1" });
    expect(fetchImpl.mock.calls[0]![0]).toBe("/api/v1/invitations/accept");
    expect(result).toEqual({
      ok: true,
      data: {
        userId: accepted.user_id,
        membership: {
          membershipId: SCHOOL_MEMBERSHIP.membership_id,
          organization: SCHOOL_MEMBERSHIP.organization,
          school: SCHOOL_MEMBERSHIP.school,
          role: "treasurer",
        },
      },
    });
  });

  it("conta existente: so o token (sem full_name nem password)", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(accepted, 201));
    await acceptInvitation({ token: "tok" }, { fetchImpl });
    expect(body(fetchImpl.mock.calls[0]!)).toEqual({ token: "tok" });
  });

  it("400 invitation_invalid, 409 account_exists_login_required e 422 weak_password com campo", async () => {
    const invalid = await acceptInvitation({ token: "t" }, { fetchImpl: vi.fn().mockResolvedValue(problem("invitation_invalid", 400)) });
    const exists = await acceptInvitation({ token: "t" }, { fetchImpl: vi.fn().mockResolvedValue(problem("account_exists_login_required", 409)) });
    const weak = await acceptInvitation(
      { token: "t", fullName: "x", password: "curta" },
      { fetchImpl: vi.fn().mockResolvedValue(problem("weak_password", 422, { errors: [{ field: "password", code: "too_short" }] })) },
    );
    expect(invalid).toMatchObject({ error: { code: "invitation_invalid", status: 400 } });
    expect(exists).toMatchObject({ error: { code: "account_exists_login_required", status: 409 } });
    expect(weak).toMatchObject({ error: { code: "weak_password", fields: [{ field: "password", code: "too_short" }] } });
  });

  it("nao escreve senha nem token no console quando falha", async () => {
    const spies = [vi.spyOn(console, "error"), vi.spyOn(console, "warn"), vi.spyOn(console, "log")];
    await acceptInvitation({ token: "segredo-tok", fullName: "X", password: "segredo-senha-123" }, {
      fetchImpl: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    });
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });
});
