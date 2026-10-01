import { describe, expect, it } from "vitest";
import { formatCountdown, formatDateLong, formatDateShort, formatTime } from "./format";

describe("format", () => {
  it("formata data curta no fuso de Sao Paulo", () => {
    expect(formatDateShort("2026-09-30T16:42:00-03:00")).toBe("30 set");
    // 01h UTC ainda e o dia anterior em Sao Paulo
    expect(formatDateShort("2026-10-01T01:30:00Z")).toBe("30 set");
  });

  it("formata data longa e hora", () => {
    expect(formatDateLong("2026-09-30T16:42:00-03:00")).toBe("30 de setembro de 2026");
    expect(formatTime("2026-09-30T16:42:00-03:00")).toBe("16:42");
  });

  it("contagem regressiva nunca fica negativa", () => {
    expect(formatCountdown(600_000)).toBe("10:00");
    expect(formatCountdown(61_001)).toBe("01:02");
    expect(formatCountdown(0)).toBe("00:00");
    expect(formatCountdown(-5000)).toBe("00:00");
  });
});
