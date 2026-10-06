/**
 * Tipos do contrato entre o frontend e a API.
 *
 * REAIS hoje (veja /api/openapi.json): GET /api/health, GET /api/health/ready e a autenticacao
 * (/api/v1/auth/* e /api/v1/invitations/accept, docs/auth.md). Os tipos de auth ficam em camelCase:
 * a conversao do snake_case da API acontece em src/lib/api/auth.ts, na fronteira.
 * TODO o resto e PROPOSTA (docs/web-contract-proposals.md), hoje simulada em src/mocks.
 * Dinheiro: sempre inteiro em centavos.
 */
import type { Cents } from "../money";
import type { IdentificationConfig, IdentificationValues } from "../validation";
import type { ContributionStatus, MovementKind, MovementStatus, PixChargeStatus } from "../status";

// ---------------------------------------------------------------- resultado e erros

export type ApiErrorKind = "network" | "timeout" | "http" | "invalid-response" | "not-found" | "problem";

/** Erro de campo de um 422 (`errors[]` do problem+json): campo e codigo FIXO, nunca texto do servidor. */
export interface FieldError {
  field: string;
  code: string;
}

export interface ApiError {
  kind: ApiErrorKind;
  /** Status HTTP quando houve resposta. */
  status?: number;
  /**
   * `code` estavel de um application/problem+json (kind "problem"). E a UNICA parte do erro do
   * servidor que a interface usa: o texto em portugues vem do `code`, nunca de title/detail.
   */
  code?: string;
  /** Retry-After (segundos) de um 429, ja limitado a um intervalo razoavel. */
  retryAfterSeconds?: number;
  fields?: FieldError[];
}

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ApiError };

// ---------------------------------------------------------------- REAL: health

/** GET /api/health/ready: 200 {"status":"ready"} ou 503 {"status":"unavailable"}. */
export type ReadinessStatus = "ready" | "unavailable";

export type ReadinessResult =
  | { state: "ready" }
  | { state: "unavailable"; reason: "not-ready" | "unreachable" };

// ---------------------------------------------------------------- REAL: autenticacao (docs/auth.md)

export const ROLES = ["organization_admin", "school_admin", "treasurer", "staff", "viewer"] as const;
export type Role = (typeof ROLES)[number];

export interface AuthUser {
  id: string;
  email: string;
  fullName: string;
}

export interface OrganizationRef {
  id: string;
  name: string;
  slug: string;
}

/** Vinculo (membership): uma organizacao (e, se houver, uma escola) com um papel. */
export interface Membership {
  membershipId: string;
  organization: OrganizationRef;
  /** null = vinculo da organizacao inteira (rede). */
  school: SchoolRef | null;
  role: Role;
}

export interface ActiveMembership extends Membership {
  /** So para esconder o que a pessoa nao pode fazer. O servidor confere de novo a cada pedido. */
  permissions: string[];
}

/** Quem esta logado e onde atua. O token CSRF NAO entra aqui: o cliente le do cookie legivel. */
export interface Session {
  user: AuthUser;
  /** null enquanto a pessoa com varios vinculos ainda nao escolheu onde atuar. */
  activeMembership: ActiveMembership | null;
  memberships: Membership[];
  expiresAt: string;
  idleTimeoutSeconds: number;
}

export interface AcceptedInvitation {
  userId: string;
  membership: Membership;
}

export interface AcceptInvitationInput {
  token: string;
  /** Pessoa nova: nome e senha. Conta existente: so o token, com a pessoa logada. */
  fullName?: string;
  password?: string;
}

// ---------------------------------------------------------------- REAL: portal publico (docs/public-flow.md)

/** Configuracao publica da escola, resolvida pelo slug da URL (ADR-010). Sem id interno. */
export interface PublicSchool {
  slug: string;
  name: string;
  /** Nome curto da APM, ex.: "APM da Escola Exemplo". */
  apmName: string;
  /** Cor da escola (hex). O frontend deriva dai o tema com contraste AA. */
  accentColor: string;
  /** Valores sugeridos (centavos), na ordem da API. */
  suggestedAmountsCents: Cents[];
  /** false = so os valores sugeridos sao aceitos. */
  allowCustomAmount: boolean;
  minAmountCents: Cents;
  maxAmountCents: Cents;
  identification: IdentificationConfig;
}

export type ChargeStatus = PixChargeStatus;
export type { ContributionStatus };

export interface PixCharge {
  status: ChargeStatus;
  amountCents: Cents;
  /** Texto do "copia e cola" e do QR. No sandbox: `PIX-SANDBOX:<txid>:<valor>`. */
  emvPayload: string | null;
  expiresAt: string;
}

/** Estado da contribuicao e da cobranca mais recente (GET .../charge, POST .../charges). */
export interface ContributionState {
  status: ContributionStatus;
  amountCents: Cents;
  charge: PixCharge | null;
}

/** Resposta do POST de criacao: o token opaco (segredo da familia) so existe aqui e na URL do comprovante. */
export interface CreatedContribution extends ContributionState {
  token: string;
}

export interface CreateContributionInput {
  amountCents: Cents;
  identification: IdentificationValues;
}

export interface Receipt {
  /** O comprovante so existe pago (a API responde 409 antes disso). */
  status: "PAID";
  referenceCode: string;
  amountCents: Cents;
  paidAt: string;
  schoolName: string;
  /** Forma de pagamento como a API manda (PIX, CASH, TRANSFER, OTHER). */
  method: string;
}

// ---------------------------------------------------------------- PROPOSTA: painel

export interface SchoolRef {
  id: string;
  slug: string;
  name: string;
}

export interface Organization {
  id: string;
  name: string;
  schools: SchoolRef[];
}

export interface Movement {
  id: string;
  kind: MovementKind;
  status: MovementStatus;
  /** Quem manda se e entrada ou saida e o backend, nao a tela. */
  direction: "IN" | "OUT";
  title: string;
  counterparty: string;
  amountCents: Cents;
  occurredAt: string;
}

export interface WeeklyTotal {
  label: string;
  inCents: Cents;
  outCents: Cents;
}

/** Totais calculados pelo backend. A tela so exibe. */
export interface DashboardSummary {
  periodLabel: string;
  /** Entradas menos despesas, reembolsos pagos e devolucoes. */
  balanceCents: Cents;
  collectedCents: Cents;
  collectedCount: number;
  expensesCents: Cents;
  expensesCount: number;
  reimbursementsPaidCents: Cents;
  reimbursementsPendingCents: Cents;
  reimbursementsPendingCount: number;
  refundsCents: Cents;
  refundsCount: number;
  weekly: WeeklyTotal[];
}

export interface DashboardData {
  summary: DashboardSummary;
  movements: Movement[];
}

// ---------------------------------------------------------------- interface de dados (mock hoje)

export interface DashboardApi {
  listOrganizations(): Promise<ApiResult<Organization[]>>;
  getDashboard(schoolId: string): Promise<ApiResult<DashboardData>>;
}

export interface DataSource {
  dashboard: DashboardApi;
}
