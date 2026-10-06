"use client";

import { useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/TextField";
import { api } from "@/lib/api";
import type { ExpenseCategory, ExpenseDetail, PaidBy, PaymentMethod } from "@/lib/api/types";
import { describeExpenseError } from "@/lib/expense-labels";
import { PAYMENT_METHOD_LABELS } from "@/lib/expense-labels";
import { centsFromDigits, formatBRLNumber } from "@/lib/money";

const selectClass = "block w-full min-h-[52px] rounded-[14px] border border-field-border bg-field px-4 text-body text-ink";
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const SCHOOL_TZ = "America/Sao_Paulo";

/** YYYY-MM-DD de hoje (ou de um instante) no fuso da escola, para o campo de data. */
export function dayInSchoolZone(instant: Date | string = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: SCHOOL_TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(instant));
}

interface ExpenseFormProps {
  schoolId: string;
  categories: ExpenseCategory[];
  /** Presente = editar este rascunho/correcao. Ausente = nova despesa. */
  expense?: ExpenseDetail;
  /** Chamado com o id da despesa depois de salvar (e de anexar/enviar, se pedido). */
  onSaved: (expenseId: string, note?: string) => void;
  onCancel: () => void;
}

/**
 * Nova despesa ou edicao de rascunho. O NOME de quem envia nunca e pedido: a API usa o usuario da sessao.
 * O arquivo (nota ou recibo) vai por multipart para a API; o conteudo nao fica em estado, URL, log nem
 * storage do navegador: so o objeto File do campo, ate o envio.
 */
export function ExpenseForm({ schoolId, categories, expense, onSaved, onCancel }: ExpenseFormProps) {
  const editing = Boolean(expense);
  const [cents, setCents] = useState(expense?.amountCents ?? 0);
  const [day, setDay] = useState(expense ? dayInSchoolZone(expense.occurredAt) : dayInSchoolZone());
  const [chosenCategory, setChosenCategory] = useState(expense?.category.id ?? "");
  // Sem categoria escolhida, vale a primeira da lista (a pessoa ainda pode trocar).
  const categoryId = chosenCategory || categories[0]?.id || "";
  const [description, setDescription] = useState(expense?.description ?? "");
  const [vendor, setVendor] = useState(expense?.vendor ?? "");
  const [purchaseReason, setPurchaseReason] = useState(expense?.purchaseReason ?? "");
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod | "">(expense?.paymentMethod ?? "");
  const [paidBy, setPaidBy] = useState<PaidBy>(expense?.paidBy ?? "COLLABORATOR");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [general, setGeneral] = useState<string | null>(null);
  const [pending, setPending] = useState<"draft" | "send" | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const amountRef = useRef<HTMLInputElement>(null);

  async function save(sendAfter: boolean) {
    if (pending) return;
    setGeneral(null);
    const problems: Record<string, string> = {};
    if (cents <= 0) problems.amount_cents = "Informe o valor.";
    if (!day) problems.occurred_at = "Informe a data da despesa.";
    if (!categoryId) problems.category_id = "Escolha uma categoria.";
    if (!description.trim()) problems.description = "Descreva a despesa.";
    const file = fileRef.current?.files?.[0];
    if (file && file.size > MAX_FILE_BYTES) problems.file = "O arquivo é grande demais. O limite é de 10 MB.";
    if (sendAfter) {
      if (purchaseReason.trim().length < 3) problems.purchase_reason = "Informe o motivo da compra (pelo menos 3 letras).";
      if (!paymentMethod) problems.payment_method = "Informe a forma de pagamento.";
      if (!file && !(expense && expense.attachments.length > 0)) problems.file = "Anexe a nota ou o recibo para enviar.";
    }
    setErrors(problems);
    if (Object.keys(problems).length > 0) {
      if (problems.amount_cents) amountRef.current?.focus();
      return;
    }

    setPending(sendAfter ? "send" : "draft");
    const fields = {
      amountCents: cents,
      occurredAt: day,
      categoryId,
      description: description.trim(),
      vendor: vendor.trim() || undefined,
      purchaseReason: purchaseReason.trim() || undefined,
      paymentMethod: paymentMethod || undefined,
    };
    const saved = expense
      ? await api.expenses.update(schoolId, expense.id, fields)
      : await api.expenses.create(schoolId, { ...fields, paidBy });
    if (!saved.ok) {
      const text = describeExpenseError(saved.error);
      setErrors(text.fields);
      setGeneral(text.general);
      setPending(null);
      return;
    }
    const id = saved.data.id;
    let note: string | undefined;
    if (file) {
      const uploaded = await api.expenses.upload(schoolId, id, file, "INVOICE");
      if (!uploaded.ok) {
        const text = describeExpenseError(uploaded.error);
        // O rascunho ja existe: a pessoa continua dele (anexar de novo) em vez de perder o que digitou.
        setPending(null);
        onSaved(id, `Rascunho salvo, mas o arquivo não foi enviado. ${text.general ?? ""}`.trim());
        return;
      }
      note = "Arquivo anexado.";
    }
    if (sendAfter) {
      const sent = await api.expenses.submit(schoolId, id);
      if (!sent.ok) {
        const text = describeExpenseError(sent.error);
        setPending(null);
        onSaved(id, `Rascunho salvo, mas não foi possível enviar. ${text.general ?? ""}`.trim());
        return;
      }
      note = "Despesa enviada para a análise da gestão.";
    } else {
      note = note ?? "Rascunho salvo.";
    }
    setPending(null);
    onSaved(id, note);
  }

  const submitting = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void save(false);
  };

  return (
    <form
      method="post"
      noValidate
      onSubmit={submitting}
      aria-labelledby="expense-form-title"
      aria-busy={pending !== null}
      className="mb-8 space-y-4 rounded-[var(--r-lg)] bg-surface p-5 shadow-[0_0_0_1px_var(--line)] sm:p-6"
    >
      <h2 id="expense-form-title" className="text-heading text-ink">
        {editing ? `Editar despesa nº ${expense!.referenceCode}` : "Nova despesa"}
      </h2>
      <p className="max-w-[60ch] text-sub text-ink-2">
        Quem envia é você, pela sua conta: não pedimos o seu nome. A gestão só vê a despesa depois que você enviar.
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Valor"
          name="expense-amount"
          prefix="R$"
          inputMode="numeric"
          autoComplete="off"
          placeholder="0,00"
          inputRef={amountRef}
          value={cents === 0 ? "" : formatBRLNumber(cents)}
          onChange={(e) => {
            setCents(centsFromDigits(e.target.value));
            setErrors((p) => ({ ...p, amount_cents: "" }));
          }}
          error={errors.amount_cents || undefined}
        />
        <TextField
          label="Data da despesa"
          name="expense-date"
          type="date"
          max={dayInSchoolZone()}
          value={day}
          onChange={(e) => {
            setDay(e.target.value);
            setErrors((p) => ({ ...p, occurred_at: "" }));
          }}
          error={errors.occurred_at || undefined}
        />
      </div>

      <div>
        <label htmlFor="expense-category" className="mb-1.5 block text-sub font-semibold text-ink">
          Categoria
        </label>
        <select id="expense-category" value={categoryId} onChange={(e) => setChosenCategory(e.target.value)} className={selectClass} aria-invalid={errors.category_id ? true : undefined}>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        {errors.category_id && <p className="mt-1.5 text-sub font-medium text-bad">{errors.category_id}</p>}
      </div>

      <TextField
        label="Descrição"
        name="expense-description"
        autoComplete="off"
        maxLength={500}
        value={description}
        onChange={(e) => {
          setDescription(e.target.value);
          setErrors((p) => ({ ...p, description: "" }));
        }}
        error={errors.description || undefined}
      />
      <TextField label="Fornecedor" optional name="expense-vendor" autoComplete="off" maxLength={200} value={vendor} onChange={(e) => setVendor(e.target.value)} />
      <TextField
        label="Motivo da compra"
        name="expense-reason"
        autoComplete="off"
        maxLength={500}
        hint="Obrigatório para enviar à gestão."
        value={purchaseReason}
        onChange={(e) => {
          setPurchaseReason(e.target.value);
          setErrors((p) => ({ ...p, purchase_reason: "" }));
        }}
        error={errors.purchase_reason || undefined}
      />

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="expense-method" className="mb-1.5 block text-sub font-semibold text-ink">
            Forma de pagamento
          </label>
          <select id="expense-method" value={paymentMethod} onChange={(e) => setPaymentMethod(e.target.value as PaymentMethod | "")} className={selectClass}>
            <option value="">Escolha…</option>
            {(Object.keys(PAYMENT_METHOD_LABELS) as PaymentMethod[]).map((m) => (
              <option key={m} value={m}>
                {PAYMENT_METHOD_LABELS[m]}
              </option>
            ))}
          </select>
          {errors.payment_method && <p className="mt-1.5 text-sub font-medium text-bad">{errors.payment_method}</p>}
        </div>
        {!editing && (
          <fieldset>
            <legend className="mb-1.5 block text-sub font-semibold text-ink">Quem pagou</legend>
            <label className="flex min-h-11 items-center gap-2 text-body text-ink">
              <input type="radio" name="expense-paid-by" checked={paidBy === "COLLABORATOR"} onChange={() => setPaidBy("COLLABORATOR")} />
              Eu paguei do meu bolso (reembolso)
            </label>
            <label className="flex min-h-11 items-center gap-2 text-body text-ink">
              <input type="radio" name="expense-paid-by" checked={paidBy === "APM"} onChange={() => setPaidBy("APM")} />
              Foi paga pela APM
            </label>
          </fieldset>
        )}
      </div>

      <div>
        <label htmlFor="expense-file" className="mb-1.5 block text-sub font-semibold text-ink">
          Nota ou recibo (PNG, JPEG, WebP ou PDF, até 10 MB)
        </label>
        <input
          id="expense-file"
          ref={fileRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,application/pdf"
          onChange={() => setErrors((p) => ({ ...p, file: "" }))}
          className="block w-full text-body text-ink file:mr-3 file:min-h-11 file:rounded-[12px] file:border-0 file:bg-neutral-soft file:px-4 file:font-semibold file:text-ink"
        />
        {errors.file && <p role="alert" className="mt-1.5 text-sub font-medium text-bad">{errors.file}</p>}
      </div>

      <div aria-live="polite">
        {general && (
          <p role="alert" className="rounded-[var(--r-md)] bg-bad-soft px-4 py-3 text-sub font-medium text-bad">
            {general}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-3 sm:flex-row">
        <Button type="submit" variant="secondary" disabled={pending !== null} aria-busy={pending === "draft"}>
          {pending === "draft" ? "Salvando…" : "Salvar rascunho"}
        </Button>
        <Button onClick={() => void save(true)} disabled={pending !== null} aria-busy={pending === "send"}>
          {pending === "send" ? "Enviando…" : "Salvar e enviar para análise"}
        </Button>
        <Button variant="plain" onClick={onCancel} disabled={pending !== null}>
          Fechar
        </Button>
      </div>
    </form>
  );
}
