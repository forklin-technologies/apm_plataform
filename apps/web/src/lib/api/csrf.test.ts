import { describe, expect, it } from "vitest";
import { readCsrfToken } from "./csrf";

const A = "a".repeat(40);
const B = "b".repeat(40);

describe("readCsrfToken", () => {
  it("le o cookie de desenvolvimento e o de producao (__Host-), preferindo o de producao", () => {
    expect(readCsrfToken(`x=1; apm_csrf=${A}; y=2`)).toBe(A);
    expect(readCsrfToken(`__Host-apm_csrf=${B}`)).toBe(B);
    expect(readCsrfToken(`apm_csrf=${A}; __Host-apm_csrf=${B}`)).toBe(B);
  });

  it("nunca devolve o cookie de sessao nem valor fora do formato", () => {
    expect(readCsrfToken(`apm_session=${A}`)).toBeNull();
    expect(readCsrfToken(`__Host-apm_session=${A}`)).toBeNull();
    expect(readCsrfToken("apm_csrf=curto")).toBeNull();
    expect(readCsrfToken(`apm_csrf=${A}%0d%0aX-Evil: 1`)).toBeNull();
    expect(readCsrfToken("")).toBeNull();
  });

  it("nao confunde nome parecido", () => {
    expect(readCsrfToken(`not_apm_csrf=${A}; apm_csrf_old=${B}`)).toBeNull();
  });
});
