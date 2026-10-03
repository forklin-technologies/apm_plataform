import { useId, type InputHTMLAttributes, type Ref } from "react";
import { InfoIcon } from "./icons";

interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  optional?: boolean;
  hint?: string;
  error?: string;
  inputRef?: Ref<HTMLInputElement>;
  /** Texto fixo antes do valor (ex.: "R$"). */
  prefix?: string;
}

export const fieldClass =
  "block w-full min-h-[52px] rounded-[14px] border bg-field px-4 text-body text-ink placeholder:text-ink-3 " +
  "transition-colors";

export function TextField({ label, optional, hint, error, inputRef, prefix, className = "", ...rest }: TextFieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  const border = error ? "border-bad" : "border-field-border";

  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1.5 block text-sub font-semibold text-ink">
        {label}
        {optional && <span className="font-normal text-ink-2"> (opcional)</span>}
      </label>
      {prefix ? (
        <div className={`flex min-h-[52px] items-center gap-2 rounded-[14px] border bg-field px-4 ${border} focus-within:outline focus-within:outline-[3px] focus-within:outline-offset-2 focus-within:outline-[var(--focus)]`}>
          <span aria-hidden="true" className="text-body font-semibold text-ink-2">
            {prefix}
          </span>
          <input
            id={id}
            ref={inputRef}
            aria-invalid={error ? true : undefined}
            aria-describedby={describedBy}
            className="min-h-[50px] w-full bg-transparent text-body tabular-nums text-ink outline-none placeholder:text-ink-3"
            {...rest}
          />
        </div>
      ) : (
        <input
          id={id}
          ref={inputRef}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={`${fieldClass} ${border}`}
          {...rest}
        />
      )}
      {hint && (
        <p id={hintId} className="mt-1.5 text-foot text-ink-2">
          {hint}
        </p>
      )}
      <div aria-live="polite">
        {error && (
          <p id={errorId} className="mt-1.5 flex items-start gap-1.5 text-sub font-medium text-bad">
            <InfoIcon size={18} className="mt-0.5 shrink-0" />
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
