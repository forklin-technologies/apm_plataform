import { describe, expect, it } from "vitest";
import { plausibleInviteToken, safeNext } from "./safe-next";

const TOKEN = "a".repeat(43);

describe("safeNext (sem redirecionamento aberto)", () => {
  it("aceita so o painel e o aceite de convite", () => {
    expect(safeNext("/painel")).toBe("/painel");
    expect(safeNext("/painel?tipo=despesas")).toBe("/painel?tipo=despesas");
    expect(safeNext(`/accept-invitation?token=${TOKEN}`)).toBe(`/accept-invitation?token=${TOKEN}`);
  });

  it("todo o resto cai no painel", () => {
    for (const bad of [
      "https://evil.example/",
      "//evil.example",
      "/\\evil.example",
      "/painel/../../x",
      "/painel?x=1",
      "/painel#x",
      "javascript:alert(1)",
      "/apm/escola-exemplo",
      "/login",
      `/accept-invitation?token=${TOKEN}&next=//x`,
      "/accept-invitation?token=",
      "",
      "x".repeat(400),
    ]) {
      expect(safeNext(bad), bad).toBe("/painel");
    }
    expect(safeNext(undefined)).toBe("/painel");
    expect(safeNext(["/painel?tipo=reembolsos", "//x"])).toBe("/painel?tipo=reembolsos");
  });
});

describe("plausibleInviteToken", () => {
  it("so um valor unico, com caracteres seguros e tamanho razoavel", () => {
    expect(plausibleInviteToken(TOKEN)).toBe(TOKEN);
    expect(plausibleInviteToken(undefined)).toBeNull();
    expect(plausibleInviteToken([TOKEN, TOKEN])).toBeNull();
    expect(plausibleInviteToken("curto")).toBeNull();
    expect(plausibleInviteToken(`${TOKEN}<script>`)).toBeNull();
    expect(plausibleInviteToken("a".repeat(201))).toBeNull();
  });
});
