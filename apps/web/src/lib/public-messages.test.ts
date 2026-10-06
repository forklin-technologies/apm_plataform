import { describe, expect, it } from "vitest";
import type { ApiError } from "@/lib/api/types";
import { describeContributionError, describeRenewError } from "./public-messages";

const problem = (code: string, status: number, extra: Partial<ApiError> = {}): ApiError => ({ kind: "problem", status, code, ...extra });

describe("describeContributionError", () => {
  it("422: erros por campo, em portugues, ao lado do campo certo", () => {
    const out = describeContributionError(
      problem("validation_error", 422, {
        fields: [
          { field: "amount_cents", code: "out_of_range" },
          { field: "contributor_email", code: "invalid" },
          { field: "guardian_name", code: "required" },
          { field: "contributor_phone", code: "invalid" },
        ],
      }),
    );
    expect(out.amount).toBe("O valor não está dentro do que a escola aceita.");
    expect(out.fields).toEqual({
      contributorEmail: "Confira este campo.",
      guardianName: "Informe o nome do responsável.",
      contributorPhone: "Confira este campo.",
    });
    expect(out.general).toBeNull();
  });

  it("valor fora da lista sugerida e 422 sem campos conhecidos", () => {
    expect(describeContributionError(problem("validation_error", 422, { fields: [{ field: "amount_cents", code: "not_suggested" }] })).amount).toMatch(/só os valores sugeridos/);
    expect(describeContributionError(problem("validation_error", 422)).general).toMatch(/Algum dado não foi aceito/);
    expect(describeContributionError(problem("validation_error", 422, { fields: [{ field: "Idempotency-Key", code: "required" }] })).general).toMatch(/Recarregue/);
  });

  it("429 com tempo, rede, 501, 409 e erro generico", () => {
    expect(describeContributionError(problem("rate_limited", 429, { retryAfterSeconds: 90 })).general).toContain("2 minutos");
    expect(describeContributionError({ kind: "network" }).general).toMatch(/Nada foi cobrado/);
    expect(describeContributionError({ kind: "http", status: 501 }).general).toMatch(/ainda não está disponível/);
    expect(describeContributionError(problem("idempotency_key_reused", 409)).general).toMatch(/Confira os dados/);
    expect(describeContributionError(problem("internal_error", 500)).general).toMatch(/Tente de novo/);
    expect(describeContributionError(problem("not_found", 404)).general).toMatch(/Não encontramos esta escola/);
  });

  it("nunca repassa texto do servidor", () => {
    const hostile = { kind: "problem", status: 500, code: "internal_error", title: "Internal Server Error" } as ApiError;
    expect(JSON.stringify(describeContributionError(hostile))).not.toMatch(/Internal/);
  });
});

describe("describeRenewError", () => {
  it("contribuicao fechada, 429, rede e generico", () => {
    expect(describeRenewError(problem("contribution_closed", 409))).toMatch(/não está mais aguardando pagamento/);
    expect(describeRenewError(problem("rate_limited", 429, { retryAfterSeconds: 30 }))).toContain("30 segundos");
    expect(describeRenewError({ kind: "timeout" })).toMatch(/conexão/);
    expect(describeRenewError(problem("internal_error", 500))).toMatch(/novo QR/);
  });
});
