import { describe, expect, it } from "vitest";
import { formatBRL } from "./money";
import {
  validateAmount,
  validateIdentification,
  visibleFields,
  type IdentificationConfig,
} from "./validation";

const rules = { minCents: 500, maxCents: 200_000 };

describe("validateAmount", () => {
  it("aceita valores dentro dos limites da escola, inclusive os limites", () => {
    expect(validateAmount(500, rules)).toEqual({ ok: true, cents: 500 });
    expect(validateAmount(12000, rules)).toEqual({ ok: true, cents: 12000 });
    expect(validateAmount(200_000, rules)).toEqual({ ok: true, cents: 200_000 });
  });

  it("recusa vazio e zero", () => {
    expect(validateAmount(null, rules)).toMatchObject({ ok: false, code: "EMPTY" });
    expect(validateAmount(0, rules)).toMatchObject({ ok: false, code: "EMPTY" });
  });

  it("recusa abaixo do minimo e acima do maximo com a mensagem da regra", () => {
    const below = validateAmount(499, rules);
    expect(below).toMatchObject({ ok: false, code: "BELOW_MIN" });
    expect(below.ok === false && below.message).toBe(`O valor mínimo é ${formatBRL(500)}.`);
    const above = validateAmount(200_001, rules);
    expect(above).toMatchObject({ ok: false, code: "ABOVE_MAX" });
    expect(above.ok === false && above.message).toBe(`O valor máximo é ${formatBRL(200_000)}.`);
  });

  it("recusa negativos e fracionados", () => {
    expect(validateAmount(-100, rules)).toMatchObject({ ok: false, code: "NOT_INTEGER" });
    expect(validateAmount(1000.5, rules)).toMatchObject({ ok: false, code: "NOT_INTEGER" });
  });

  it("respeita regras diferentes por escola", () => {
    expect(validateAmount(1000, { minCents: 2000, maxCents: 5000 }).ok).toBe(false);
    expect(validateAmount(1000, { minCents: 100, maxCents: 5000 }).ok).toBe(true);
  });
});

const config: IdentificationConfig = {
  guardianName: "REQUIRED",
  studentName: "REQUIRED",
  classroom: "OPTIONAL",
};

describe("validateIdentification", () => {
  it("exige so os campos obrigatorios", () => {
    const result = validateIdentification({ guardianName: "Ana Lima", studentName: "Davi Lima" }, config);
    expect(result.ok).toBe(true);
    expect(result.cleaned).toEqual({ guardianName: "Ana Lima", studentName: "Davi Lima" });
  });

  it("aponta obrigatorios vazios", () => {
    const result = validateIdentification({ guardianName: "   " }, config);
    expect(result.ok).toBe(false);
    expect(result.errors.guardianName).toBe("Informe o nome do responsável.");
    expect(result.errors.studentName).toBe("Informe o nome do aluno.");
    expect(result.errors.classroom).toBeUndefined();
  });

  it("aceita opcional preenchido e normaliza espacos", () => {
    const result = validateIdentification(
      { guardianName: "  Ana   Lima ", studentName: "Davi", classroom: " 4º B " },
      config,
    );
    expect(result.cleaned).toEqual({ guardianName: "Ana Lima", studentName: "Davi", classroom: "4º B" });
  });

  it("ignora campo oculto mesmo que venha preenchido", () => {
    const hidden: IdentificationConfig = { ...config, classroom: "HIDDEN" };
    const result = validateIdentification(
      { guardianName: "Ana Lima", studentName: "Davi", classroom: "4º B" },
      hidden,
    );
    expect(result.ok).toBe(true);
    expect(result.cleaned).not.toHaveProperty("classroom");
    expect(visibleFields(hidden)).toEqual(["guardianName", "studentName"]);
  });

  it("valida tamanho de campos opcionais preenchidos", () => {
    const tooShort = validateIdentification({ guardianName: "Ana", studentName: "Davi", classroom: "x" }, config);
    expect(tooShort.errors.classroom).toMatch(/pelo menos 2/);
    const tooLong = validateIdentification({ guardianName: "a".repeat(81), studentName: "Davi" }, config);
    expect(tooLong.errors.guardianName).toMatch(/no máximo 80/);
  });
});
