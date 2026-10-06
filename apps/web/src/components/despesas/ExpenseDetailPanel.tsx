"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { StatusPill } from "@/components/ui/StatusPill";
import { TextField } from "@/components/ui/TextField";
import { api } from "@/lib/api";
import type { ApiResult, ExpenseCategory, ExpenseDetail } from "@/lib/api/types";
import { saveBlob } from "@/lib/download";
import {
  ATTACHMENT_KIND_LABELS, PAID_BY_LABELS, PAYMENT_METHOD_LABELS, amountSummary, describeExpenseError, expenseStatusView, formatFileSize,
} from "@/lib/expense-labels";
import { formatDateShort } from "@/lib/format";
import { centsFromDigits, formatBRL, formatBRLNumber } from "@/lib/money";
import { ExpenseForm } from "./ExpenseForm";

type Mode = "edit" | "approve" | "reject" | "correct" | "reimburse" | null;

interface Props {
  schoolId: string;
  expenseId: string;
  /** Usuario da sessao: so serve para NAO oferecer ao autor o que a API recusa. A API continua recusando. */
  userId: string;
  permissions: string[];
  categories: ExpenseCategory[];
  /** Avisa a lista que algo mudou (ela recarrega e mostra a mensagem: a despesa pode sair do filtro e levar o painel junto). */
  onChanged: (message?: string) => void;
}

const REASON_RANGE = "O motivo precisa ter de 3 a 500 caracteres.";

function ReasonForm({ label, submit, busy, error, onSubmit, onCancel }: { label: string; submit: string; busy: boolean; error: string | null; onSubmit: (reason: string) => void; onCancel: () => void }) {
  const [reason, setReason] = useState("");
  const [local, setLocal] = useState<string | null>(null);
  return (
    <form
      method="post"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        const text = reason.trim();
        if (text.length < 3 || text.length > 500) return setLocal(REASON_RANGE);
        setLocal(null);
        onSubmit(text);
      }}
      className="mt-4 space-y-3 rounded-[var(--r-md)] bg-neutral-soft/60 p-4"
    >
      <TextField label={label} name="reason" autoComplete="off" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} error={local ?? error ?? undefined} />
      <div className="flex flex-col gap-3 sm:flex-row">
        <Button type="submit" size="md" disabled={busy}>
          {busy ? "Enviando…" : submit}
        </Button>
        <Button variant="plain" size="md" onClick={onCancel} disabled={busy}>
          Voltar
        </Button>
      </div>
    </form>
  );
}

function ApproveForm({ expense, busy, error, onSubmit, onCancel }: { expense: ExpenseDetail; busy: boolean; error: string | null; onSubmit: (input: { approvedAmountCents?: number; reason?: string }) => void; onCancel: () => void }) {
  const canPartial = expense.paidBy === "COLLABORATOR"; // regra da API: valor menor so quando um colaborador pagou
  const [partial, setPartial] = useState(false);
  const [cents, setCents] = useState(0);
  const [reason, setReason] = useState("");
  const [local, setLocal] = useState<Record<string, string>>({});
  return (
    <form
      method="post"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (!partial) return onSubmit({});
        const problems: Record<string, string> = {};
        if (cents <= 0 || cents >= expense.amountCents) problems.amount = `Informe um valor menor que ${formatBRL(expense.amountCents)}.`;
        if (reason.trim().length < 3 || reason.trim().length > 500) problems.reason = "Explique a diferença (de 3 a 500 caracteres).";
        setLocal(problems);
        if (Object.keys(problems).length === 0) onSubmit({ approvedAmountCents: cents, reason: reason.trim() });
      }}
      className="mt-4 space-y-3 rounded-[var(--r-md)] bg-neutral-soft/60 p-4"
    >
      <fieldset>
        <legend className="mb-1 text-sub font-semibold text-ink">Valor a aprovar</legend>
        <label className="flex min-h-11 items-center gap-2 text-body text-ink">
          <input type="radio" name="approve-mode" checked={!partial} onChange={() => setPartial(false)} />
          Pelo valor pedido ({formatBRL(expense.amountCents)})
        </label>
        {canPartial && (
          <label className="flex min-h-11 items-center gap-2 text-body text-ink">
            <input type="radio" name="approve-mode" checked={partial} onChange={() => setPartial(true)} />
            Um valor menor (aprovação parcial)
          </label>
        )}
      </fieldset>
      {partial && (
        <>
          <TextField
            label="Valor aprovado"
            name="approved-amount"
            prefix="R$"
            inputMode="numeric"
            autoComplete="off"
            placeholder="0,00"
            value={cents === 0 ? "" : formatBRLNumber(cents)}
            onChange={(e) => setCents(centsFromDigits(e.target.value))}
            error={local.amount}
          />
          <TextField label="Motivo da diferença" name="approve-reason" autoComplete="off" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} error={local.reason} />
        </>
      )}
      {error && <p role="alert" className="text-sub font-medium text-bad">{error}</p>}
      <div className="flex flex-col gap-3 sm:flex-row">
        <Button type="submit" size="md" disabled={busy}>
          {busy ? "Aprovando…" : partial ? "Aprovar valor menor" : "Aprovar"}
        </Button>
        <Button variant="plain" size="md" onClick={onCancel} disabled={busy}>
          Voltar
        </Button>
      </div>
    </form>
  );
}

function ReimburseForm({ expense, busy, error, onSubmit, onCancel }: { expense: ExpenseDetail; busy: boolean; error: string | null; onSubmit: (ref: string) => void; onCancel: () => void }) {
  const [ref, setRef] = useState("");
  const [local, setLocal] = useState<string | null>(null);
  return (
    <form
      method="post"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (!ref.trim()) return setLocal("Informe a referência do pagamento (ex.: o código do Pix).");
        setLocal(null);
        onSubmit(ref.trim());
      }}
      className="mt-4 space-y-3 rounded-[var(--r-md)] bg-neutral-soft/60 p-4"
    >
      <p className="text-sub text-ink-2">
        Registre o reembolso de <strong className="tabular-nums text-ink">{formatBRL(expense.approvedAmountCents ?? expense.amountCents)}</strong> que a APM já pagou no banco. O site não move dinheiro.
      </p>
      <TextField label="Referência do pagamento" name="payment-reference" autoComplete="off" maxLength={200} value={ref} onChange={(e) => setRef(e.target.value)} error={local ?? error ?? undefined} />
      <div className="flex flex-col gap-3 sm:flex-row">
        <Button type="submit" size="md" disabled={busy}>
          {busy ? "Registrando…" : "Registrar reembolso"}
        </Button>
        <Button variant="plain" size="md" onClick={onCancel} disabled={busy}>
          Voltar
        </Button>
      </div>
    </form>
  );
}

/**
 * Detalhe de uma despesa com as acoes que cabem a QUEM ve, pelas permissoes da sessao e pela autoria. O
 * site nunca decide o estado: depois de cada acao mostra o que a API devolveu. A API segue sendo a
 * autoridade: 403, 404, 409 e 422 viram texto em portugues e a tela nao quebra.
 */
export function ExpenseDetailPanel({ schoolId, expenseId, userId, permissions, categories, onChanged }: Props) {
  const [loaded, setLoaded] = useState<{ key: string; res: ApiResult<ExpenseDetail> } | null>(null);
  const [version, setVersion] = useState(0);
  const [override, setOverride] = useState<ExpenseDetail | null>(null);
  const [mode, setMode] = useState<Mode>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  const key = `${schoolId}|${expenseId}|${version}`;
  useEffect(() => {
    let cancelled = false;
    void api.expenses.get(schoolId, expenseId).then((res) => {
      if (!cancelled) setLoaded({ key, res });
    });
    return () => {
      cancelled = true;
    };
  }, [schoolId, expenseId, key]);

  if (loaded?.key !== key && !override) return <p role="status" className="px-5 py-4 text-sub text-ink-2">Carregando a despesa…</p>;
  const res = loaded?.key === key ? loaded.res : null;
  if (res && !res.ok) return <p role="alert" className="px-5 py-4 text-sub font-medium text-bad">{describeExpenseError(res.error).general}</p>;
  const d = override ?? (res && res.ok ? res.data : null);
  if (!d) return null;

  const has = (p: string) => permissions.includes(p);
  const own = d.submittedByUserId === userId;
  const status = expenseStatusView(d.status, d.paidBy);
  const editable = d.status === "DRAFT" || d.status === "CORRECTION_REQUESTED";
  const canEditOwn = has("expenses:submit") && own && editable;
  const canDecide = has("expenses:approve") && d.status === "SUBMITTED" && !own;
  const canRegister = has("reimbursements:register") && d.status === "APPROVED" && !own;
  const canCancelApproved = has("expenses:approve") && d.status === "APPROVED";
  const ownNotice = own && has("expenses:approve") && (d.status === "SUBMITTED" || d.status === "APPROVED");

  function changed(next: ExpenseDetail, message?: string) {
    setOverride(next);
    setMode(null);
    setError(null);
    setFieldError(null);
    onChanged(message);
  }

  async function run(name: string, call: () => Promise<ApiResult<ExpenseDetail>>, message: string) {
    if (busy) return;
    setBusy(name);
    setError(null);
    setFieldError(null);
    const result = await call();
    setBusy(null);
    if (result.ok) return changed(result.data, message);
    const text = describeExpenseError(result.error);
    setError(text.general);
    setFieldError(Object.values(text.fields)[0] ?? null);
    // Estado que mudou por baixo: recarrega para a tela voltar a bater com a API.
    if (result.error.status === 409) {
      setOverride(null);
      setVersion((n) => n + 1);
    }
  }

  async function download(attachmentId: string, fileName: string) {
    setError(null);
    const result = await api.expenses.download(schoolId, d!.id, attachmentId);
    if (!result.ok) return setError(describeExpenseError(result.error).general);
    saveBlob(result.data.blob, fileName);
  }

  async function attach(file: File | undefined) {
    if (!file || busy) return;
    setFileError(null);
    setBusy("attach");
    setError(null);
    const uploaded = await api.expenses.upload(schoolId, d!.id, file, "INVOICE");
    if (!uploaded.ok) {
      setBusy(null);
      setFileError(describeExpenseError(uploaded.error).general);
      return;
    }
    const fresh = await api.expenses.get(schoolId, d!.id);
    setBusy(null);
    if (fresh.ok) changed(fresh.data, "Arquivo anexado.");
    else setVersion((n) => n + 1);
  }

  const row = (label: string, value: React.ReactNode) => (
    <div className="flex flex-wrap gap-x-2">
      <dt className="text-ink-2">{label}:</dt>
      <dd className="font-medium text-ink [overflow-wrap:anywhere]">{value}</dd>
    </div>
  );

  return (
    <div className="border-t border-line bg-bg/40 px-4 py-4 sm:px-5" data-testid="expense-detail">
      {mode === "edit" ? (
        <ExpenseForm
          schoolId={schoolId}
          categories={categories}
          expense={d}
          onCancel={() => setMode(null)}
          onSaved={() => {
            setOverride(null);
            setMode(null);
            setVersion((n) => n + 1);
            onChanged("Despesa atualizada.");
          }}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={status.tone}>{status.label}</StatusPill>
            <span className="text-foot text-ink-2">nº {d.referenceCode}</span>
          </div>
          <dl className="mt-3 space-y-1 text-sub">
            {row("Valor", amountSummary(d.amountCents, d.approvedAmountCents))}
            {row("Data", formatDateShort(d.occurredAt))}
            {row("Categoria", d.category.name)}
            {row("Pagamento", PAID_BY_LABELS[d.paidBy])}
            {d.paymentMethod && row("Forma", PAYMENT_METHOD_LABELS[d.paymentMethod])}
            {d.vendor && row("Fornecedor", d.vendor)}
            {d.purchaseReason && row("Motivo da compra", d.purchaseReason)}
            {own && row("Enviada por", "você")}
          </dl>

          {d.status === "CORRECTION_REQUESTED" && d.correctionReason && (
            <p role="note" className="mt-4 rounded-[var(--r-md)] bg-warn-soft px-4 py-3 text-sub text-warn">
              <strong className="font-semibold">A gestão pediu correção:</strong> {d.correctionReason}
            </p>
          )}
          {d.decisionReason && d.status !== "CORRECTION_REQUESTED" && (
            <p className="mt-4 rounded-[var(--r-md)] bg-neutral-soft px-4 py-3 text-sub text-ink">
              <strong className="font-semibold">Motivo da decisão:</strong> {d.decisionReason}
            </p>
          )}
          {d.reimbursement && (
            <p className="mt-3 text-sub text-ink-2">
              Reembolso de <strong className="tabular-nums text-ink">{formatBRL(d.reimbursement.amountCents)}</strong>
              {d.reimbursement.paymentReference ? ` (ref.: ${d.reimbursement.paymentReference})` : ""}.
            </p>
          )}

          <h3 className="mt-4 text-headline text-ink">Anexos</h3>
          {d.attachments.length === 0 ? (
            <p className="text-sub text-ink-2">Nenhum arquivo anexado ainda.</p>
          ) : (
            <ul className="mt-1 divide-y divide-line">
              {d.attachments.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span className="min-w-0 text-sub text-ink [overflow-wrap:anywhere]">
                    {a.fileName} <span className="text-ink-2">· {ATTACHMENT_KIND_LABELS[a.kind]} · {formatFileSize(a.sizeBytes)}</span>
                  </span>
                  <Button variant="secondary" size="md" onClick={() => void download(a.id, a.fileName)} aria-label={`Baixar ${a.fileName}`}>
                    Baixar
                  </Button>
                </li>
              ))}
            </ul>
          )}

          {canEditOwn && (
            <div className="mt-3">
              <label htmlFor={`attach-${d.id}`} className="mb-1 block text-sub font-semibold text-ink">
                {d.attachments.length > 0 ? "Anexar outro arquivo (para trocar a nota, anexe a nova)" : "Anexar a nota ou o recibo"}
              </label>
              <input
                id={`attach-${d.id}`}
                type="file"
                accept="image/png,image/jpeg,image/webp,application/pdf"
                disabled={busy !== null}
                onChange={(e) => {
                  void attach(e.target.files?.[0]);
                  e.target.value = "";
                }}
                className="block w-full text-body text-ink file:mr-3 file:min-h-11 file:rounded-[12px] file:border-0 file:bg-neutral-soft file:px-4 file:font-semibold file:text-ink"
              />
              {fileError && <p role="alert" className="mt-1.5 text-sub font-medium text-bad">{fileError}</p>}
            </div>
          )}

          {ownNotice && (
            <p role="note" className="mt-4 rounded-[var(--r-md)] bg-info-soft px-4 py-3 text-sub text-info">
              Esta despesa é sua: outra pessoa da gestão precisa {d.status === "SUBMITTED" ? "decidir" : "registrar o pagamento ou o reembolso"}.
            </p>
          )}
          {d.status === "SUBMITTED" && own && !has("expenses:approve") && (
            <p role="note" className="mt-4 rounded-[var(--r-md)] bg-info-soft px-4 py-3 text-sub text-info">
              Enviada: aguardando a decisão da gestão.
            </p>
          )}

          <div aria-live="polite">
            {error && <p role="alert" className="mt-4 rounded-[var(--r-md)] bg-bad-soft px-4 py-3 text-sub font-medium text-bad">{error}</p>}
          </div>

          {mode === null && (
            <div className="mt-4 flex flex-wrap gap-3">
              {canEditOwn && (
                <>
                  <Button size="md" variant="secondary" onClick={() => setMode("edit")}>
                    Editar
                  </Button>
                  <Button size="md" onClick={() => void run("submit", () => api.expenses.submit(schoolId, d.id), "Despesa enviada para a análise da gestão.")} disabled={busy !== null} aria-busy={busy === "submit"}>
                    {busy === "submit" ? "Enviando…" : "Enviar para análise"}
                  </Button>
                  <Button size="md" variant="plain" onClick={() => void run("cancel", () => api.expenses.cancel(schoolId, d.id), "Despesa cancelada.")} disabled={busy !== null}>
                    Cancelar despesa
                  </Button>
                </>
              )}
              {canDecide && (
                <>
                  <Button size="md" onClick={() => setMode("approve")}>Aprovar</Button>
                  <Button size="md" variant="secondary" onClick={() => setMode("correct")}>Pedir correção</Button>
                  <Button size="md" variant="secondary" onClick={() => setMode("reject")}>Recusar</Button>
                </>
              )}
              {canRegister && d.paidBy === "COLLABORATOR" && (
                <Button size="md" onClick={() => setMode("reimburse")}>Registrar reembolso</Button>
              )}
              {canRegister && d.paidBy === "APM" && (
                <Button size="md" onClick={() => void run("pay", () => api.expenses.pay(schoolId, d.id), "Pagamento registrado.")} disabled={busy !== null} aria-busy={busy === "pay"}>
                  {busy === "pay" ? "Registrando…" : "Registrar pagamento"}
                </Button>
              )}
              {canCancelApproved && (
                <Button size="md" variant="plain" onClick={() => void run("cancel", () => api.expenses.cancel(schoolId, d.id), "Despesa cancelada.")} disabled={busy !== null}>
                  Cancelar despesa
                </Button>
              )}
            </div>
          )}

          {mode === "approve" && (
            <ApproveForm expense={d} busy={busy === "approve"} error={fieldError ?? null} onCancel={() => setMode(null)} onSubmit={(input) => void run("approve", () => api.expenses.approve(schoolId, d.id, input), "Despesa aprovada.")} />
          )}
          {mode === "reject" && (
            <ReasonForm label="Motivo da recusa" submit="Recusar despesa" busy={busy === "reject"} error={fieldError} onCancel={() => setMode(null)} onSubmit={(reason) => void run("reject", () => api.expenses.reject(schoolId, d.id, reason), "Despesa recusada.")} />
          )}
          {mode === "correct" && (
            <ReasonForm label="O que precisa ser corrigido" submit="Pedir correção" busy={busy === "correct"} error={fieldError} onCancel={() => setMode(null)} onSubmit={(reason) => void run("correct", () => api.expenses.requestCorrection(schoolId, d.id, reason), "Correção pedida. A despesa volta para quem enviou.")} />
          )}
          {mode === "reimburse" && (
            <ReimburseForm expense={d} busy={busy === "reimburse"} error={fieldError} onCancel={() => setMode(null)} onSubmit={(ref) => void run("reimburse", () => api.expenses.reimburse(schoolId, d.id, ref), "Reembolso registrado.")} />
          )}
        </>
      )}
    </div>
  );
}
