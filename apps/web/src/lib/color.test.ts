import { describe, expect, it } from "vitest";
import {
  SURFACES,
  contrastRatio,
  deriveSchoolTheme,
  ensureContrast,
  parseHex,
  pickContrastText,
  toHex,
} from "./color";

describe("parseHex / toHex", () => {
  it("le #rgb e #rrggbb", () => {
    expect(parseHex("#E6457A")).toEqual({ r: 230, g: 69, b: 122 });
    expect(parseHex("#fff")).toEqual({ r: 255, g: 255, b: 255 });
    expect(toHex({ r: 230, g: 69, b: 122 })).toBe("#e6457a");
  });

  it("rejeita lixo", () => {
    expect(parseHex("rosa")).toBeNull();
    expect(parseHex("#12")).toBeNull();
    expect(parseHex("url(javascript:alert(1))")).toBeNull();
  });
});

describe("contrastRatio", () => {
  it("bate com os valores de referencia do WCAG", () => {
    expect(contrastRatio(parseHex("#000")!, parseHex("#fff")!)).toBeCloseTo(21, 5);
    expect(contrastRatio(parseHex("#fff")!, parseHex("#fff")!)).toBeCloseTo(1, 5);
  });
});

describe("pickContrastText", () => {
  it("usa texto escuro sobre o rosa e claro sobre o azul-marinho", () => {
    expect(toHex(pickContrastText(parseHex("#E6457A")!))).toBe("#000000");
    expect(toHex(pickContrastText(parseHex("#0b1f4d")!))).toBe("#ffffff");
  });

  it("e sempre AA (>= 4,5:1) para cores arbitrarias", () => {
    let seed = 1234567;
    const next = () => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed % 256;
    };
    for (let i = 0; i < 2000; i += 1) {
      const bg = { r: next(), g: next(), b: next() };
      const text = pickContrastText(bg);
      expect(contrastRatio(text, bg)).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe("ensureContrast", () => {
  it("nao mexe quando ja passa", () => {
    const color = parseHex("#1a1a1a")!;
    expect(ensureContrast(color, [...SURFACES.light], 4.5)).toEqual(color);
  });

  it("escurece uma cor clara sobre fundo claro ate atingir o minimo", () => {
    const yellow = parseHex("#ffd60a")!;
    const fixed = ensureContrast(yellow, [...SURFACES.light], 4.5);
    for (const bg of SURFACES.light) expect(contrastRatio(fixed, bg)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("deriveSchoolTheme", () => {
  const accents = ["#E6457A", "#3BA7E0", "#ffd60a", "#0b1f4d", "#000000", "#ffffff", "#808080", "#2e9e6b"];

  it.each(accents)("garante AA em claro e escuro para %s", (hex) => {
    const theme = deriveSchoolTheme(hex);
    const pairs: Array<[string, string, readonly (typeof SURFACES)["light"][number][], number]> = [
      [theme.accentContrastLight, theme.accentLight, [], 4.5],
      [theme.accentContrastDark, theme.accentDark, [], 4.5],
    ];
    for (const [text, fill, , min] of pairs) {
      expect(contrastRatio(parseHex(text)!, parseHex(fill)!)).toBeGreaterThanOrEqual(min);
    }
    for (const bg of SURFACES.light) {
      expect(contrastRatio(parseHex(theme.accentInkLight)!, bg)).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(parseHex(theme.accentLight)!, bg)).toBeGreaterThanOrEqual(3);
    }
    for (const bg of SURFACES.dark) {
      expect(contrastRatio(parseHex(theme.accentInkDark)!, bg)).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(parseHex(theme.accentDark)!, bg)).toBeGreaterThanOrEqual(3);
    }
  });

  it("usa a cor padrao quando a escola manda uma cor invalida", () => {
    expect(deriveSchoolTheme("nao-e-cor")).toEqual(deriveSchoolTheme("#2f5fd0"));
  });
});
