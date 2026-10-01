import { describe, expect, it } from "vitest";
import { isPlausibleSlug, isPlausibleToken } from "./token";

describe("isPlausibleToken", () => {
  it("aceita tokens seguros para URL", () => {
    for (const ok of ["demo-comprovante-0001", "teste1", "AbC_123-xyz", "a".repeat(64)]) {
      expect(isPlausibleToken(ok)).toBe(true);
    }
  });

  it("recusa formatos perigosos ou fora do tamanho", () => {
    for (const bad of ["", "abc", "a".repeat(65), "../etc/passwd", "a b c d", "<script>", "tok%20en", "tok/en1", "tök3n"]) {
      expect(isPlausibleToken(bad)).toBe(false);
    }
  });
});

describe("isPlausibleSlug", () => {
  it("aceita slugs em minusculas com hifen", () => {
    expect(isPlausibleSlug("escola-exemplo")).toBe(true);
    expect(isPlausibleSlug("emei-vale-verde")).toBe(true);
  });

  it("recusa o resto", () => {
    for (const bad of ["", "Escola", "-a", "a--b", "a_b", "a/b", "..", "a".repeat(81)]) {
      expect(isPlausibleSlug(bad)).toBe(false);
    }
  });
});
