import { describe, expect, it } from "vitest";
import { niceScale } from "./chart";

describe("niceScale", () => {
  it("arredonda o teto para um numero limpo e nunca corta o maior valor", () => {
    for (const cents of [1, 99, 100, 12_345, 526_000, 1_864_000, 7_215_40, 999_999_999]) {
      const { max, ticks } = niceScale(cents);
      expect(max).toBeGreaterThanOrEqual(cents);
      expect(Number.isInteger(max)).toBe(true);
      expect(ticks).toHaveLength(3);
      expect(ticks[0]).toBe(0);
      expect(ticks[2]).toBe(max);
      for (const t of ticks) expect(Number.isInteger(t)).toBe(true);
    }
  });

  it("exemplos conhecidos", () => {
    expect(niceScale(526_000)).toEqual({ max: 600_000, ticks: [0, 300_000, 600_000] });
    expect(niceScale(238_040).max).toBe(300_000);
  });

  it("sem dados usa uma escala padrao", () => {
    expect(niceScale(0)).toEqual({ max: 10_000, ticks: [0, 5_000, 10_000] });
    expect(niceScale(Number.NaN).max).toBe(10_000);
  });
});
