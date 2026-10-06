"use client";

import { useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/TextField";
import { api } from "@/lib/api";
import { describeAuthError, describeFieldError } from "@/lib/auth-messages";

const MIN = 12;
const MAX = 128;

type Field = "current" | "next" | "confirm";

/**
 * Troca de senha (POST /api/v1/auth/password). A API confere a senha atual, exige de 12 a 128 caracteres e
 * uma senha diferente da atual, ENCERRA todas as outras sessoes e abre uma nova neste navegador (cookies
 * novos na mesma resposta). As senhas so existem nos campos e no corpo do pedido: nao entram em estado do
 * React, URL, log nem storage, e os campos sao limpos depois.
 */
export function PasswordForm() {
  const currentRef = useRef<HTMLInputElement>(null);
  const nextRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);
  const [errors, setErrors] = useState<Partial<Record<Field, string>>>({});
  const [general, setGeneral] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);

  const clear = () => {
    for (const ref of [currentRef, nextRef, confirmRef]) if (ref.current) ref.current.value = "";
  };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const current = currentRef.current?.value ?? "";
    const next = nextRef.current?.value ?? "";
    const confirm = confirmRef.current?.value ?? "";
    const length = [...next].length;
    const problems: Partial<Record<Field, string>> = {};
    if (!current) problems.current = "Informe a senha atual.";
    if (length < MIN) problems.next = `Use pelo menos ${MIN} caracteres.`;
    else if (length > MAX) problems.next = `Use no máximo ${MAX} caracteres.`;
    else if (next === current) problems.next = "A nova senha precisa ser diferente da atual.";
    if (!problems.next && confirm !== next) problems.confirm = "As senhas não são iguais.";
    setErrors(problems);
    setGeneral(null);
    setDone(false);
    if (problems.current) return currentRef.current?.focus();
    if (problems.next) return nextRef.current?.focus();
    if (problems.confirm) return confirmRef.current?.focus();

    setPending(true);
    const result = await api.auth.changePassword(current, next);
    setPending(false);
    if (result.ok) {
      clear();
      setErrors({});
      setDone(true);
      return;
    }
    const fromServer: Partial<Record<Field, string>> = {};
    for (const item of result.error.fields ?? []) {
      if (item.field === "new_password") fromServer.next = describeFieldError(item.code);
      if (item.field === "current_password") fromServer.current = describeFieldError(item.code);
    }
    if (result.error.code === "current_password_incorrect") fromServer.current = "A senha atual está incorreta.";
    setErrors(fromServer);
    setGeneral(Object.keys(fromServer).length > 0 ? null : describeAuthError(result.error, "password"));
    if (currentRef.current) currentRef.current.value = "";
    if (nextRef.current) nextRef.current.value = "";
    if (confirmRef.current) confirmRef.current.value = "";
  }

  return (
    <form
      method="post"
      noValidate
      onSubmit={submit}
      aria-labelledby="password-title"
      aria-busy={pending}
      className="max-w-lg space-y-4 rounded-[var(--r-lg)] bg-surface p-5 shadow-[0_0_0_1px_var(--line)] sm:p-6"
    >
      <h2 id="password-title" className="text-heading text-ink">
        Alterar senha
      </h2>
      <p className="text-sub text-ink-2">
        Ao trocar, as outras sessões abertas com a sua conta são encerradas. Você continua conectado neste navegador.
      </p>
      <TextField label="Senha atual" name="current-password" type="password" autoComplete="current-password" inputRef={currentRef} error={errors.current} />
      <TextField
        label="Nova senha"
        name="new-password"
        type="password"
        autoComplete="new-password"
        hint={`De ${MIN} a ${MAX} caracteres, diferente da atual.`}
        inputRef={nextRef}
        error={errors.next}
      />
      <TextField label="Repita a nova senha" name="confirm-password" type="password" autoComplete="new-password" inputRef={confirmRef} error={errors.confirm} />

      <div aria-live="polite">
        {general && <p role="alert" className="rounded-[var(--r-md)] bg-bad-soft px-4 py-3 text-sub font-medium text-bad">{general}</p>}
        {done && <p role="status" className="rounded-[var(--r-md)] bg-ok-soft px-4 py-3 text-sub font-medium text-ok">Senha alterada. As outras sessões foram encerradas.</p>}
      </div>

      <Button type="submit" disabled={pending} aria-busy={pending}>
        {pending ? "Alterando…" : "Alterar senha"}
      </Button>
    </form>
  );
}
