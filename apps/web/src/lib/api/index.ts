/**
 * Unico ponto de entrada para dados. Componentes importam `api` daqui.
 *
 * - `health`, `auth` e `public` sao REAIS (mesma origem, /api/*): GET /api/health/ready, a autenticacao
 *   (docs/auth.md) e o portal publico de contribuicao (docs/public-flow.md).
 * - `statement` (resumo, extrato, pendencias e contribuicao em dinheiro do painel), `expenses` (docs/expenses.md) e
 *   `closings` (fechamento mensal e PDF) sao REAIS.
 *   Nada do site e simulado: nao ha mais camada de mocks.
 */
import { acceptInvitation, changePassword, getMe, login, logout, switchContext } from "./auth";
import { getReadiness } from "./health";
import { listClosings, createClosing, verifyClosing, reopenClosing, downloadClosingPdf } from "./closings";
import {
  approveExpense, cancelExpense, createExpense, downloadAttachment, getExpense, listCategories, listExpenses, payExpense,
  reimburseExpense, rejectExpense, requestCorrection, submitExpense, updateExpense, uploadAttachment,
} from "./expenses";
import { getPending, getStatement, getSummary, recordCashContribution } from "./statement";
import { createContribution, getContribution, getPublicSchool, getReceipt, renewCharge, sandboxPay } from "./public";

export const api: {
  health: { ready: typeof getReadiness };
  public: {
    school: typeof getPublicSchool;
    createContribution: typeof createContribution;
    contribution: typeof getContribution;
    renewCharge: typeof renewCharge;
    receipt: typeof getReceipt;
    sandboxPay: typeof sandboxPay;
  };
  expenses: {
    categories: typeof listCategories;
    list: typeof listExpenses;
    get: typeof getExpense;
    create: typeof createExpense;
    update: typeof updateExpense;
    upload: typeof uploadAttachment;
    download: typeof downloadAttachment;
    submit: typeof submitExpense;
    cancel: typeof cancelExpense;
    approve: typeof approveExpense;
    reject: typeof rejectExpense;
    requestCorrection: typeof requestCorrection;
    reimburse: typeof reimburseExpense;
    pay: typeof payExpense;
  };
  closings: {
    list: typeof listClosings;
    create: typeof createClosing;
    verify: typeof verifyClosing;
    reopen: typeof reopenClosing;
    pdf: typeof downloadClosingPdf;
  };
  statement: {
    summary: typeof getSummary;
    entries: typeof getStatement;
    pending: typeof getPending;
    recordCash: typeof recordCashContribution;
  };
  auth: {
    login: typeof login;
    logout: typeof logout;
    me: typeof getMe;
    switchContext: typeof switchContext;
    changePassword: typeof changePassword;
    acceptInvitation: typeof acceptInvitation;
  };
} = {
  health: { ready: getReadiness },
  public: {
    school: getPublicSchool,
    createContribution,
    contribution: getContribution,
    renewCharge,
    receipt: getReceipt,
    sandboxPay,
  },
  auth: { login, logout, me: getMe, switchContext, changePassword, acceptInvitation },
  expenses: {
    categories: listCategories,
    list: listExpenses,
    get: getExpense,
    create: createExpense,
    update: updateExpense,
    upload: uploadAttachment,
    download: downloadAttachment,
    submit: submitExpense,
    cancel: cancelExpense,
    approve: approveExpense,
    reject: rejectExpense,
    requestCorrection,
    reimburse: reimburseExpense,
    pay: payExpense,
  },
  closings: {
    list: listClosings,
    create: createClosing,
    verify: verifyClosing,
    reopen: reopenClosing,
    pdf: downloadClosingPdf,
  },
  statement: {
    summary: getSummary,
    entries: getStatement,
    pending: getPending,
    recordCash: recordCashContribution,
  },
};

export * from "./types";
