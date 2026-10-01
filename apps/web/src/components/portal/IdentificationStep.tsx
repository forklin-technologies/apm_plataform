"use client";

import { useRef, type Ref } from "react";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/TextField";
import type { PublicSchool } from "@/lib/api/types";
import {
  FIELD_LABELS,
  visibleFields,
  type IdentificationErrors,
  type IdentificationField,
  type IdentificationValues,
} from "@/lib/validation";
import { StickyActions } from "./FlowChrome";

const AUTOCOMPLETE: Record<IdentificationField, string> = {
  guardianName: "name",
  studentName: "off",
  classroom: "off",
};

const PLACEHOLDERS: Record<IdentificationField, string> = {
  guardianName: "Como está no seu documento",
  studentName: "Nome do aluno ou da aluna",
  classroom: "Ex.: 4º ano B",
};

interface IdentificationStepProps {
  school: PublicSchool;
  values: IdentificationValues;
  errors: IdentificationErrors;
  headingRef: Ref<HTMLHeadingElement>;
  onChange: (field: IdentificationField, value: string) => void;
  onBack: () => void;
  onContinue: (focusField: (field: IdentificationField) => void) => void;
}

export function IdentificationStep({
  school,
  values,
  errors,
  headingRef,
  onChange,
  onBack,
  onContinue,
}: IdentificationStepProps) {
  const formRef = useRef<HTMLFormElement>(null);
  const fields = visibleFields(school.identification);
  const hasOptional = fields.some((f) => school.identification[f] === "OPTIONAL");

  const focusField = (field: IdentificationField) => {
    const element = formRef.current?.elements.namedItem(field);
    if (element instanceof HTMLInputElement) element.focus();
  };

  return (
    <form
      ref={formRef}
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        onContinue(focusField);
      }}
    >
      <h2 ref={headingRef} tabIndex={-1} className="text-title text-ink outline-none">
        Quem está contribuindo?
      </h2>
      <p className="mt-2 max-w-[52ch] text-body text-ink-2">
        Esses dados ajudam a tesouraria a identificar o pagamento. Você não precisa criar conta nem fazer login.
        {hasOptional && " Campos marcados como opcionais podem ficar em branco."}
      </p>

      <div className="mt-6 space-y-5">
        {fields.map((field) => (
          <TextField
            key={field}
            name={field}
            label={FIELD_LABELS[field]}
            optional={school.identification[field] === "OPTIONAL"}
            autoComplete={AUTOCOMPLETE[field]}
            placeholder={PLACEHOLDERS[field]}
            maxLength={80}
            value={values[field] ?? ""}
            onChange={(e) => onChange(field, e.target.value)}
            error={errors[field]}
          />
        ))}
      </div>

      <StickyActions>
        <Button variant="secondary" className="shrink-0 sm:min-w-32" onClick={onBack}>
          Voltar
        </Button>
        <Button type="submit" className="flex-1 sm:min-w-48 sm:flex-none">
          Continuar
        </Button>
      </StickyActions>
    </form>
  );
}
