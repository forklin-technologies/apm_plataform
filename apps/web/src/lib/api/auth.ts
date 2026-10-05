import { apiSend, apiGet, type RequestOptions, type SendOptions } from "./client";
import {
  ROLES,
  type AcceptedInvitation,
  type AcceptInvitationInput,
  type ActiveMembership,
  type ApiResult,
  type Membership,
  type OrganizationRef,
  type Role,
  type SchoolRef,
  type Session,
} from "./types";

/**
 * Autenticacao REAL (docs/auth.md, ADR-017). Aqui o snake_case da API vira camelCase.
 * Regras: o tenant nunca vem do cliente (so se manda o id de um vinculo que a API ja listou);
 * senha e token de convite so existem no corpo do pedido: nao vao para URL, log nem armazenamento.
 */

const BASE = "/api/v1";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function parseOrganization(value: unknown): OrganizationRef | null {
  if (!isRecord(value)) return null;
  const id = str(value.id);
  const name = str(value.name);
  const slug = str(value.slug);
  return id && name && slug ? { id, name, slug } : null;
}

function parseSchool(value: unknown): SchoolRef | null {
  return parseOrganization(value); // mesma forma: id, name, slug
}

function parseRole(value: unknown): Role | null {
  return typeof value === "string" && (ROLES as readonly string[]).includes(value) ? (value as Role) : null;
}

export function parseMembership(value: unknown): Membership | null {
  if (!isRecord(value)) return null;
  const membershipId = str(value.membership_id);
  const organization = parseOrganization(value.organization);
  const role = parseRole(value.role);
  if (!membershipId || !organization || !role) return null;
  let school: SchoolRef | null = null;
  if (value.school !== null && value.school !== undefined) {
    school = parseSchool(value.school);
    if (!school) return null;
  }
  return { membershipId, organization, school, role };
}

function parseActiveMembership(value: unknown): ActiveMembership | null {
  const base = parseMembership(value);
  if (!base || !isRecord(value) || !Array.isArray(value.permissions)) return null;
  if (!value.permissions.every((p) => typeof p === "string")) return null;
  return { ...base, permissions: value.permissions as string[] };
}

/** Resposta de login, me e context (SessionResponse). O csrf_token e ignorado de proposito: ver csrf.ts. */
export function parseSession(body: unknown): Session | null {
  if (!isRecord(body) || !isRecord(body.user) || !isRecord(body.session) || !Array.isArray(body.memberships)) {
    return null;
  }
  const id = str(body.user.id);
  const email = str(body.user.email);
  const fullName = typeof body.user.full_name === "string" ? body.user.full_name : null;
  const expiresAt = str(body.session.expires_at);
  const idle = body.session.idle_timeout_seconds;
  if (!id || !email || fullName === null || !expiresAt || typeof idle !== "number") return null;

  const memberships: Membership[] = [];
  for (const item of body.memberships) {
    const membership = parseMembership(item);
    if (!membership) return null;
    memberships.push(membership);
  }

  let activeMembership: ActiveMembership | null = null;
  if (body.active_membership !== null && body.active_membership !== undefined) {
    activeMembership = parseActiveMembership(body.active_membership);
    if (!activeMembership) return null;
  }
  return { user: { id, email, fullName }, activeMembership, memberships, expiresAt, idleTimeoutSeconds: idle };
}

const parseSessionOk = (body: unknown, status: number) => (status === 200 ? parseSession(body) : null);
const parseNoContent = (_body: unknown, status: number) => (status === 204 ? (true as const) : null);

/** POST /auth/login. Sem login automatico em nenhum outro lugar: so aqui. */
export function login(email: string, password: string, options?: SendOptions): Promise<ApiResult<Session>> {
  return apiSend("POST", `${BASE}/auth/login`, { email: email.trim(), password }, parseSessionOk, options);
}

/** POST /auth/logout: 204 sempre que a sessao e valida (com X-CSRF-Token) ou ausente. */
export function logout(options?: SendOptions): Promise<ApiResult<true>> {
  return apiSend("POST", `${BASE}/auth/logout`, undefined, parseNoContent, options);
}

/** GET /auth/me. */
export function getMe(options?: RequestOptions): Promise<ApiResult<Session>> {
  return apiGet(`${BASE}/auth/me`, parseSessionOk, options);
}

/** POST /auth/context: troca o vinculo ativo (a API gira o token da sessao e atualiza os cookies). */
export function switchContext(membershipId: string, options?: SendOptions): Promise<ApiResult<Session>> {
  return apiSend("POST", `${BASE}/auth/context`, { membership_id: membershipId }, parseSessionOk, options);
}

/** POST /auth/password: 204; a API encerra todas as sessoes e abre uma nova (cookies novos). */
export function changePassword(
  currentPassword: string,
  newPassword: string,
  options?: SendOptions,
): Promise<ApiResult<true>> {
  return apiSend(
    "POST",
    `${BASE}/auth/password`,
    { current_password: currentPassword, new_password: newPassword },
    parseNoContent,
    options,
  );
}

function parseAccepted(body: unknown, status: number): AcceptedInvitation | null {
  if (status !== 201 || !isRecord(body)) return null;
  const userId = str(body.user_id);
  const membership = parseMembership(body.membership);
  return userId && membership ? { userId, membership } : null;
}

/** POST /invitations/accept. Nao abre sessao: depois de aceitar, a pessoa entra pelo login. */
export function acceptInvitation(input: AcceptInvitationInput, options?: SendOptions): Promise<ApiResult<AcceptedInvitation>> {
  const body: Record<string, string> = { token: input.token };
  if (input.fullName !== undefined) body.full_name = input.fullName;
  if (input.password !== undefined) body.password = input.password;
  return apiSend("POST", `${BASE}/invitations/accept`, body, parseAccepted, options);
}
