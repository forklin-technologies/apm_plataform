import { describe, expect, it } from "vitest";
import { randomToken } from "./util";

describe("randomToken (N11: 128 bits, sem vies de modulo)", () => {
  it("tem 22 caracteres base64url e nao se repete", () => {
    const seen = new Set<string>();
    for (let i = 0; i < 20_000; i += 1) {
      const token = randomToken();
      expect(token).toMatch(/^[A-Za-z0-9_-]{22}$/);
      seen.add(token);
    }
    expect(seen.size).toBe(20_000);
  });

  it("os 128 bits vem de 16 bytes de crypto.getRandomValues", () => {
    const original = globalThis.crypto.getRandomValues.bind(globalThis.crypto);
    let requested = 0;
    globalThis.crypto.getRandomValues = ((array: Uint8Array) => {
      requested += array.length;
      array.fill(0xff);
      return array;
    }) as typeof globalThis.crypto.getRandomValues;
    try {
      expect(randomToken()).toBe("_____________________w");
      expect(requested).toBe(16);
    } finally {
      globalThis.crypto.getRandomValues = original;
    }
  });

  it("o primeiro simbolo e uniforme nos 64 valores (sem modulo, sem vies)", () => {
    const counts = new Map<string, number>();
    const N = 64_000;
    for (let i = 0; i < N; i += 1) {
      const c = randomToken()[0]!;
      counts.set(c, (counts.get(c) ?? 0) + 1);
    }
    expect(counts.size).toBe(64);
    for (const n of counts.values()) {
      expect(n).toBeGreaterThan(700);
      expect(n).toBeLessThan(1300);
    }
  });
});
