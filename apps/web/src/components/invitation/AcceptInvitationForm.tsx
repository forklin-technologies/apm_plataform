"use client";

import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { InfoIcon } from "@/components/ui/icons";
import { TextField } from "@/components/ui/TextField";
import { api, type AcceptedInvitation } from "@/lib/api";
import { describeAuthError, describeFieldError } from "@/lib/auth-messages";
import { ROLE_LABELS, membershipScope, membershipTitle } from "@/lib/roles";

const MIN_PASSWORD = 12;
const MAX_PASSWORD = 128;
const MAX_NAME = 200;

type Mode = "new" | "existing";
type FieldName = "fullName" | "password" | "confirm";

/**
 * Aceite de convite (POST /api/v1/invitations/accept). O token e a credencial: vai so no corpo do
 * pedido. Pessoa nova envia nome e senha; quem ja tem conta precisa estar logada e envia so o token
 * (a API nao deixa o token sozinho tomar uma conta). Nao ha login automatico depois do aceite.
 */
export function AcceptInvitationForm({
  token,
  signedInAs,
}: {
  token: string;
  /** Conta logada neste navegador (lida no servidor), ou null. */
  signedInAs: { fullName: string; email: string } | null;
}) {
  const [mode, setMode] = useState<Mode>(signedInAs ? "existing" : "new");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<{ text: string; needsLogin: boolean } | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<FieldName, string>>>({});
  const [accepted, setAccepted] = useState<{ result: AcceptedInvitation; created: boolean } | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);

  const loginHref = `/login?next=${encodeURIComponent(`/accept-invitation?token=${token}`)}`;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setError(null);

    if (mode === "existing") {
      if (!signedInAs) return;
      setFieldErrors({});
      await send({ token }, false);
      return;
    }

    const fullName = nameRef.current?.value.trim() ?? "";
    const password = passwordRef.current?.value ?? "";
    const confirm = confirmRef.current?.value ?? "";
    const length = [...password].length;
    const problems: Partial<Record<FieldName, string>> = {};
    if (!fullName) problems.fullName = "Informe o seu nome completo.";
    else if (fullName.length > MAX_NAME) problems.fullName = "O nome é longo demais.";
    if (length < MIN_PASSWORD) problems.password = `Use pelo menos ${MIN_PASSWORD} caracteres.`;
    else if (length > MAX_PASSWORD) problems.password = `Use no máximo ${MAX_PASSWORD} caracteres.`;
    if (!problems.password && confirm !== password) problems.confirm = "As senhas não são iguais.";
    setFieldErrors(problems);
    if (problems.fullName) return nameRef.current?.focus();
    if (problems.password) return passwordRef.current?.focus();
    if (problems.confirm) return confirmRef.current?.focus();

    await send({ token, fullName, password }, true);
  }

  async function send(input: Parameters<typeof api.auth.acceptInvitation>[0], created: boolean) {
    setPending(true);
    const result = await api.auth.acceptInvitation(input);
    setPending(false);
    if (result.ok) {
      if (passwordRef.current) passwordRef.current.value = "";
      if (confirmRef.current) confirmRef.current.value = "";
      setAccepted({ result: result.data, created });
      return;
    }
    const { error: apiError } = result;
    if (passwordRef.current) passwordRef.current.value = "";
    if (confirmRef.current) confirmRef.current.value = "";

    const fromServer: Partial<Record<FieldName, string>> = {};
    for (const field of apiError.fields ?? []) {
      if (field.field === "password") fromServer.password = describeFieldError(field.code);
      if (field.field === "full_name") fromServer.fullName = describeFieldError(field.code);
    }
    setFieldErrors(fromServer);
    setError({
      text: describeAuthError(apiError, "invitation"),
      needsLogin: apiError.code === "account_exists_login_required",
    });
  }

  if (accepted) {
    const { membership } = accepted.result;
    return (
      <div role="status" className="mt-6 rounded-[var(--r-lg)] bg-surface p-5 shadow-[0_0_0_1px_var(--line)] sm:p-6">
        <h2 className="text-heading text-ink">Convite aceito</h2>
        <p className="mt-2 text-body text-ink-2">
          {accepted.created
            ? "Sua conta foi criada. Agora entre com o seu e-mail e a senha que você acabou de escolher."
            : "O vínculo foi adicionado à sua conta."}
        </p>
        <dl className="mt-4 space-y-1 text-sub">
          <div>
            <dt className="inline font-semibold text-ink">Onde: </dt>
            <dd className="inline text-ink-2">
              {membershipTitle(membership)} ({membershipScope(membership)})
            </dd>
          </div>
          <div>
            <dt className="inline font-semibold text-ink">Papel: </dt>
            <dd className="inline text-ink-2">{ROLE_LABELS[membership.role]}</dd>
          </div>
        </dl>
        <ButtonLink href={accepted.created ? "/login" : "/painel"} className="mt-6 w-full">
          {accepted.created ? "Entrar" : "Ir para o painel"}
        </ButtonLink>
      </div>
    );
  }

  const choice = (value: Mode, label: string) => (
    <button
      type="button"
      aria-pressed={mode === value}
      onClick={() => {
        setMode(value);
        setError(null);
        setFieldErrors({});
      }}
      className={`min-h-11 flex-1 rounded-[12px] px-3 text-sub font-semibold transition-colors ${mode === value ? "bg-ink text-bg" : "text-ink hover:bg-neutral-soft"}`}
    >
      {label}
    </button>
  );

  return (
    <form
      method="post"
      noValidate
      onSubmit={submit}
      aria-busy={pending}
      className="mt-6 space-y-5 rounded-[var(--r-lg)] bg-surface p-5 shadow-[0_0_0_1px_var(--line)] sm:p-6"
    >
      <div role="group" aria-label="Você já tem conta?" className="flex gap-1 rounded-[14px] bg-neutral-soft/70 p-1">
        {choice("new", "Sou novo aqui")}
        {choice("existing", "Já tenho conta")}
      </div>

      {mode === "new" ? (
        <>
          <TextField
            label="Nome completo"
            name="fullName"
            autoComplete="name"
            autoCapitalize="words"
            inputRef={nameRef}
            error={fieldErrors.fullName}
          />
          <TextField
            label="Senha"
            name="new-password"
            type="password"
            autoComplete="new-password"
            hint={`De ${MIN_PASSWORD} a ${MAX_PASSWORD} caracteres.`}
            inputRef={passwordRef}
            error={fieldErrors.password}
          />
          <TextField
            label="Repita a senha"
            name="confirm-password"
            type="password"
            autoComplete="new-password"
            inputRef={confirmRef}
            error={fieldErrors.confirm}
          />
        </>
      ) : signedInAs ? (
        <p className="text-body text-ink-2">
          Você está conectado como <strong className="text-ink">{signedInAs.fullName}</strong> ({signedInAs.email}). O
          convite será adicionado a esta conta.
        </p>
      ) : (
        <p className="text-body text-ink-2">
          Quem já tem conta precisa entrar nela antes de aceitar. Depois do login você volta para esta página.
        </p>
      )}

      {error && (
        <div role="alert" className="flex items-start gap-2 rounded-[var(--r-md)] bg-bad-soft px-4 py-3 text-sub font-medium text-bad">
          <InfoIcon size={18} className="mt-0.5 shrink-0" />
          <p>{error.text}</p>
        </div>
      )}

      {mode === "existing" && !signedInAs ? (
        <ButtonLink href={loginHref} className="w-full">
          Entrar para aceitar
        </ButtonLink>
      ) : (
        <Button type="submit" disabled={pending} className="w-full">
          {pending ? "Enviando…" : mode === "new" ? "Criar conta e aceitar" : "Aceitar o convite"}
        </Button>
      )}

      {error?.needsLogin && (
        <p className="text-center text-sub text-ink-2">
          <Link href={loginHref} className="inline-flex min-h-11 items-center font-semibold text-ink underline underline-offset-2">
            Ir para a entrada
          </Link>
        </p>
      )}
    </form>
  );
}
