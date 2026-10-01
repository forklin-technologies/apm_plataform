import { describe, expect, it } from "vitest";
import { centavosParaDecimal, decimalParaCentavos, formatarReais } from "@/lib/dinheiro";
import { formatarCodigoPedido, gerarTxid, TXID_VALIDO } from "@/lib/ids";
import { cifrar, decifrar } from "@/lib/cripto";

describe("dinheiro", () => {
  it("converte centavos <-> decimal da API Pix sem ponto flutuante", () => {
    expect(centavosParaDecimal(2000)).toBe("20.00");
    expect(centavosParaDecimal(5)).toBe("0.05");
    expect(decimalParaCentavos("20.00")).toBe(2000);
    expect(decimalParaCentavos("0.1")).toBe(10);
    expect(decimalParaCentavos("12")).toBe(1200);
    expect(() => decimalParaCentavos("1,00")).toThrow();
    expect(() => decimalParaCentavos("-1.00")).toThrow();
    expect(() => centavosParaDecimal(10.5)).toThrow();
  });
  it("formata em reais", () => expect(formatarReais(6500)).toBe("R$ 65,00"));
});

describe("identificadores", () => {
  it("txid segue a regra do Banco Central e não repete", () => {
    const ids = new Set(Array.from({ length: 2000 }, gerarTxid));
    expect(ids.size).toBe(2000);
    for (const t of ids) expect(t).toMatch(TXID_VALIDO);
  });
  it("código do pedido", () => expect(formatarCodigoPedido(123)).toBe("APM-000123"));
});

describe("criptografia", () => {
  it("cifra e decifra; adulteração é detectada", () => {
    const c = cifrar("segredo-do-banco");
    expect(c).not.toContain("segredo");
    expect(decifrar(c)).toBe("segredo-do-banco");
    const partes = c.split(":"); partes[3] = Buffer.from("xxxx").toString("base64");
    expect(() => decifrar(partes.join(":"))).toThrow();
  });
});
