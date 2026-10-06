import { describe, expect, it } from "vitest";
import { MIN_TOKEN_LENGTH, isPlausibleSlug, isPlausibleToken } from "./token";

describe("isPlausibleToken", () => {
  it("o comprimento minimo corresponde a 128 bits em base64url (22 caracteres)", () => {
    expect(MIN_TOKEN_LENGTH).toBe(22);
    expect(Math.ceil((128 / 6))).toBe(22);
  });

  it("aceita tokens de 22 a 64 caracteres seguros para URL (a API gera 43)", () => {
    for (const ok of ["A".repeat(22), "AbC_123-xyz_AbC_123-xyz_", "a".repeat(43), "a".repeat(64)]) {
      expect(isPlausibleToken(ok)).toBe(true);
    }
  });

  it("recusa formatos curtos, longos ou perigosos", () => {
    for (const bad of ["", "abc", "teste1", "a".repeat(21), "a".repeat(65), "../etc/passwd", "a b c d e f g h i j k l", "<script>alert(1)</script>", "tok%20en".repeat(4), "tok/en1".repeat(4), "tök3n".repeat(5), "demo-comprovante-0001"]) {
      expect(isPlausibleToken(bad)).toBe(false);
    }
  });
});

describe("isPlausibleSlug", () => {
  it("aceita slugs em minusculas com hifen", () => {
    expect(isPlausibleSlug("demo-aurora")).toBe(true);
    expect(isPlausibleSlug("demo-central")).toBe(true);
  });

  it("recusa o resto", () => {
    for (const bad of ["", "Escola", "-a", "a--b", "a_b", "a/b", "..", "a".repeat(81)]) {
      expect(isPlausibleSlug(bad)).toBe(false);
    }
  });
});
