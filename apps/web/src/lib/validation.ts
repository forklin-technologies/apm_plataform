import { formatBRL, isCents, type Cents } from "./money";

/**
 * Validacao de UX com as regras da escola. A regra valida e a do backend: ele revalida
 * tudo (valor, campos e tenant) e e quem diz se a contribuicao e aceita.
 */
export interface AmountRules {
  minCents: Cents;
  maxCents: Cents;
}

export type AmountValidation =
  | { ok: true; cents: Cents }
  | { ok: false; code: "EMPTY" | "NOT_INTEGER" | "BELOW_MIN" | "ABOVE_MAX"; message: string };

export function validateAmount(cents: Cents | null, rules: AmountRules): AmountValidation {
  if (cents === null || cents === 0) {
    return { ok: false, code: "EMPTY", message: "Informe o valor da contribuição." };
  }
  if (!isCents(cents) || cents < 0) {
    return { ok: false, code: "NOT_INTEGER", message: "Informe um valor válido." };
  }
  if (cents < rules.minCents) {
    return {
      ok: false,
      code: "BELOW_MIN",
      message: `O valor mínimo é ${formatBRL(rules.minCents)}.`,
    };
  }
  if (cents > rules.maxCents) {
    return {
      ok: false,
      code: "ABOVE_MAX",
      message: `O valor máximo é ${formatBRL(rules.maxCents)}.`,
    };
  }
  return { ok: true, cents };
}

export type FieldRule = "REQUIRED" | "OPTIONAL" | "HIDDEN";
export type IdentificationField = "guardianName" | "studentName" | "classroom" | "contributorEmail" | "contributorPhone";

export type IdentificationConfig = Record<IdentificationField, FieldRule>;
export type IdentificationValues = Partial<Record<IdentificationField, string>>;
export type IdentificationErrors = Partial<Record<IdentificationField, string>>;

export const FIELD_LABELS: Record<IdentificationField, string> = {
  guardianName: "Nome do responsável",
  studentName: "Nome do aluno",
  classroom: "Turma",
  contributorEmail: "E-mail",
  contributorPhone: "Telefone",
};

/** Nome do campo no corpo do POST (snake_case da API). */
export const API_FIELD: Record<IdentificationField, string> = {
  guardianName: "guardian_name",
  studentName: "student_name",
  classroom: "class_name",
  contributorEmail: "contributor_email",
  contributorPhone: "contributor_phone",
};

export const FIELD_FROM_API: Record<string, IdentificationField> = Object.fromEntries(
  Object.entries(API_FIELD).map(([field, api]) => [api, field as IdentificationField]),
);

export const MISSING_MESSAGES: Record<IdentificationField, string> = {
  guardianName: "Informe o nome do responsável.",
  studentName: "Informe o nome do aluno.",
  classroom: "Informe a turma.",
  contributorEmail: "Informe o e-mail.",
  contributorPhone: "Informe o telefone.",
};

/** Mesmos limites da API (120 nos nomes e na turma, 254 no e-mail, 30 no telefone). */
export const FIELD_MAX_LENGTH: Record<IdentificationField, number> = {
  guardianName: 120,
  studentName: 120,
  classroom: 120,
  contributorEmail: 254,
  contributorPhone: 30,
};

// Mesmos formatos que a API aceita (app/public/service.py): a API revalida de qualquer jeito.
const EMAIL = /^[^@\s]+@[^@\s]+$/;
const PHONE = /^[0-9 ()+.-]{1,30}$/;

export const IDENTIFICATION_FIELDS: readonly IdentificationField[] = [
  "guardianName",
  "studentName",
  "classroom",
  "contributorEmail",
  "contributorPhone",
];

/** Campos que a escola quer ver na tela (oculto nao aparece). */
export function visibleFields(config: IdentificationConfig): IdentificationField[] {
  return IDENTIFICATION_FIELDS.filter((field) => config[field] !== "HIDDEN");
}

export function validateIdentification(
  values: IdentificationValues,
  config: IdentificationConfig,
): { ok: boolean; errors: IdentificationErrors; cleaned: IdentificationValues } {
  const errors: IdentificationErrors = {};
  const cleaned: IdentificationValues = {};
  for (const field of IDENTIFICATION_FIELDS) {
    const rule = config[field];
    if (rule === "HIDDEN") continue; // campo oculto: nunca segue adiante
    const value = (values[field] ?? "").replace(/\s+/g, " ").trim();
    if (value === "") {
      if (rule === "REQUIRED") errors[field] = MISSING_MESSAGES[field];
      continue;
    }
    if (value.length > FIELD_MAX_LENGTH[field]) {
      errors[field] = `Use no máximo ${FIELD_MAX_LENGTH[field]} caracteres.`;
      continue;
    }
    if (field === "contributorEmail") {
      if (!EMAIL.test(value)) errors[field] = "Confira o e-mail: ele precisa ter um @.";
    } else if (field === "contributorPhone") {
      if (!PHONE.test(value) || value.replace(/\D/g, "").length < 8) errors[field] = "Confira o telefone: use só números, com DDD.";
    } else if (value.length < 2) {
      errors[field] = `${FIELD_LABELS[field]} precisa ter pelo menos 2 letras.`;
    }
    if (!errors[field]) cleaned[field] = value;
  }
  return { ok: Object.keys(errors).length === 0, errors, cleaned };
}
