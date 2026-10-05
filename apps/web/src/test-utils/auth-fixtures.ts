/** Respostas FALSAS no formato da API (snake_case), para os testes. Nenhum dado real. */
export const ORG = { id: "11111111-1111-4111-8111-111111111111", name: "Rede Teste", slug: "rede-teste" };
export const SCHOOL = { id: "22222222-2222-4222-8222-222222222222", name: "Escola Teste", slug: "escola-teste" };

export const ORG_MEMBERSHIP = {
  membership_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  organization: ORG,
  school: null,
  role: "organization_admin",
};
export const SCHOOL_MEMBERSHIP = {
  membership_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  organization: ORG,
  school: SCHOOL,
  role: "treasurer",
};

export function sessionBody(overrides: Record<string, unknown> = {}) {
  return {
    user: { id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", email: "pessoa@example.test", full_name: "Pessoa de Teste" },
    active_membership: { ...ORG_MEMBERSHIP, permissions: ["reports:read"] },
    memberships: [ORG_MEMBERSHIP, SCHOOL_MEMBERSHIP],
    csrf_token: "csrf-token-que-a-interface-ignora-0123456789",
    session: { expires_at: "2026-10-06T00:00:00Z", idle_timeout_seconds: 1800 },
    ...overrides,
  };
}

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/problem+json", ...headers },
  });
}

export function problem(code: string, status: number, extra: Record<string, unknown> = {}, headers: Record<string, string> = {}): Response {
  return json(
    { type: `urn:apm-digital:problem:${code}`, title: "Texto em ingles do servidor", status, code, request_id: "req-1", detail: "detalhe interno", ...extra },
    status,
    headers,
  );
}
