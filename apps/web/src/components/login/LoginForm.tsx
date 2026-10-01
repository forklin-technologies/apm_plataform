"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/TextField";

/**
 * Apenas visual (TASK-002): NAO simula login e nao envia nada. O botao fica desativado e a
 * tecla Enter nao faz nada. A autenticacao real e da Fase 1 (sessao em cookie httpOnly, ADR-009).
 */
export function LoginForm() {
  const [showPassword, setShowPassword] = useState(false);
  return (
    <form
      noValidate
      onSubmit={(event) => event.preventDefault()}
      aria-describedby="login-unavailable"
      className="mt-6 space-y-5 rounded-[var(--r-lg)] bg-surface p-5 shadow-[0_0_0_1px_var(--line)] sm:p-6"
    >
      <TextField label="E-mail" name="email" type="email" inputMode="email" autoComplete="username" placeholder="voce@escola.com.br" />
      <div>
        <TextField
          label="Senha"
          name="password"
          type={showPassword ? "text" : "password"}
          autoComplete="current-password"
          placeholder="Sua senha"
        />
        <button
          type="button"
          onClick={() => setShowPassword((v) => !v)}
          aria-pressed={showPassword}
          className="mt-1 inline-flex min-h-11 items-center rounded-full px-1 text-sub font-semibold text-ink underline underline-offset-2"
        >
          {showPassword ? "Ocultar a senha" : "Mostrar a senha"}
        </button>
      </div>
      <Button type="submit" disabled className="w-full" aria-describedby="login-unavailable">
        Entrar
      </Button>
      <p id="login-unavailable" className="text-center text-foot text-ink-2">
        Disponível quando a autenticação for liberada, na Fase 1.
      </p>
    </form>
  );
}
