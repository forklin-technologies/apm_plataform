import { describe, expect, it } from "vitest";
import {
  MAX_INPUT_CENTS,
  centsFromDigits,
  formatBRL,
  formatBRLNumber,
  isCents,
  parseBRLToCents,
} from "./money";

const NBSP = " ";

describe("formatBRL", () => {
  it("formata centavos no padrao brasileiro", () => {
    expect(formatBRL(0)).toBe(`R$${NBSP}0,00`);
    expect(formatBRL(5)).toBe(`R$${NBSP}0,05`);
    expect(formatBRL(1250)).toBe(`R$${NBSP}12,50`);
    expect(formatBRL(12000)).toBe(`R$${NBSP}120,00`);
    expect(formatBRL(123456)).toBe(`R$${NBSP}1.234,56`);
    expect(formatBRL(100000000)).toBe(`R$${NBSP}1.000.000,00`);
  });

  it("formata valores negativos", () => {
    expect(formatBRL(-1250)).toBe(`-R$${NBSP}12,50`);
    expect(formatBRLNumber(-99)).toBe("-0,99");
  });

  it("nao sofre com o erro de ponto flutuante", () => {
    // 0.1 + 0.2 em float daria 30.000000000000004 centavos; aqui tudo e inteiro.
    expect(formatBRL(10 + 20)).toBe(`R$${NBSP}0,30`);
    expect(formatBRL(1999)).toBe(`R$${NBSP}19,99`);
  });

  it("recusa valores que nao sao centavos inteiros", () => {
    expect(() => formatBRL(12.5)).toThrow(TypeError);
    expect(() => formatBRL(Number.NaN)).toThrow(TypeError);
    expect(() => formatBRL(Number.POSITIVE_INFINITY)).toThrow(TypeError);
  });
});

describe("isCents", () => {
  it("aceita so inteiros seguros", () => {
    expect(isCents(0)).toBe(true);
    expect(isCents(-5)).toBe(true);
    expect(isCents(1.5)).toBe(false);
    expect(isCents("10")).toBe(false);
    expect(isCents(Number.MAX_SAFE_INTEGER + 1)).toBe(false);
  });
});

describe("parseBRLToCents", () => {
  it("converte textos validos", () => {
    expect(parseBRLToCents("12")).toBe(1200);
    expect(parseBRLToCents("12,5")).toBe(1250);
    expect(parseBRLToCents("12,50")).toBe(1250);
    expect(parseBRLToCents("1.234,56")).toBe(123456);
    expect(parseBRLToCents(`R$${NBSP}20,00`)).toBe(2000);
    expect(parseBRLToCents("0,05")).toBe(5);
  });

  it("rejeita textos invalidos", () => {
    for (const bad of ["", "abc", "12,345", "12.5", "1,2,3", "-5", "1..000,00", "R$"]) {
      expect(parseBRLToCents(bad)).toBeNull();
    }
  });
});

describe("centsFromDigits", () => {
  it("transforma digitos em centavos", () => {
    expect(centsFromDigits("")).toBe(0);
    expect(centsFromDigits("5")).toBe(5);
    expect(centsFromDigits("1250")).toBe(1250);
    expect(centsFromDigits("R$ 12,50")).toBe(1250);
    expect(centsFromDigits("0001")).toBe(1);
  });

  it("limita o tamanho do campo", () => {
    expect(centsFromDigits("99999999999999999999")).toBe(MAX_INPUT_CENTS);
  });
});
