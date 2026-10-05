# Statement, summary, monthly closing and PDF reports

Decision records: ADR-015 (the model), ADR-016 (permissions), ADR-017 (HTTP contract). Code: `apps/api/app/statement/`, `app/closing/`, `app/reports/`; tests in `apps/api/tests/statement/`. The model and the database functions are in [financial-model.md](financial-model.md); the markers every route carries are in [auth.md](auth.md).

The rule of this layer: **the API does not add, subtract or recompute money.** The statement, the summary, the pending list and the closing come from the database functions of revision 0007 (`statement_entries`, `statement_summary`, `statement_pending`, the trigger of `monthly_closings`, `verify_closing`); a route chooses the school and the month, checks who may ask, and repeats the columns. The PDFs only draw numbers the database already produced, and refuse to draw when the parts do not add up to them.

## Routes

All under `/api/v1/schools/{school_id}`. `period` is `YYYY-MM`, read in the time zone of the school (default: the current month there). Lists answer `{items, next_cursor}` (`?cursor=&limit=`, `limit` 1 to 100, default 50; the cursor is opaque, a bad one is `422 validation_error` with `errors[].code = invalid_cursor`). Errors are `problem+json` with a stable `code`.

| Route | Permission | What it does |
| --- | --- | --- |
| `GET /statement` | `statement:read` | Cash entries of the month, oldest first, with `opening_balance_cents` and `running_balance_cents`. Filters: `kind` (CONTRIBUTION, EXPENSE, REIMBURSEMENT, REFUND), `display_type` (INCOME, EXPENSE, REFUND), `status` (also REIMBURSED), `category` (id), `person` (user id of the origin or the beneficiary). A filter only chooses rows: the balances are those of the whole account |
| `GET /statement/summary` | `statement:read` or `reports:read_aggregate` | The columns of `statement_summary`. **Two balances**: `closing_balance_cents` is the primary one (in cash); `balance_after_pending_cents` is the secondary one (minus the reimbursements still to be paid, as of now) |
| `GET /statement/pending` | `statement:read` | The rows of `statement_pending` (outside the balance); filter `section` |
| `POST /closings` | `months:close` | `{period, bank_balance_reported_cents?}`. Only the `INSERT` is done here; the trigger computes everything. `201` with the closing and its `breakdown` |
| `GET /closings`, `GET /closings/{id}` | `statement:read` or `reports:read_aggregate` | The closing: figures, `entries_hash` (the verification code), `bank_difference_cents` (bank minus cash), `breakdown` (detail only). The `viewer` does not get who closed or reopened it, nor the reason |
| `POST /closings/{id}/verify` | `statement:read` | `verify_closing`: `{verified}` (409 `closing_reopened` for a superseded closing) |
| `POST /closings/{id}/reopen` | `months:reopen` | `{reason}` (10 to 500 characters after trimming). Latest active closing only, once |
| `GET /closings/{id}/report.pdf` | `reports:read` or `reports:read_aggregate` | The monthly report (below) |
| `GET /reports/contributions.pdf?period=` | `reports:read` or `reports:read_aggregate` | The contributions of the month (below) |

**The school of the path is checked against the session.** The tenant never comes from the client: the session is already bound to the tenant of the active membership, and the path id is looked up under row level security. A school the membership does not cover, a school of another organization and one that does not exist all answer the **same** `404 not_found`; so does a closing of another school. A malformed id is `422`.

**Several permissions on one route.** `require_any` (in `app/statement/deps.py`) is one marker that accepts a permission or its alternatives; the route walk of the tests reads the first as `x-permission`, and the others are in `x-permission-alternatives`. It is how the read-only `viewer` (`reports:read_aggregate`) reaches the aggregates and the anonymous PDFs without the permissions of management.

### Errors of the closing

The database refuses; the API answers with a fixed text and a stable code and **never repeats the text of Postgres**.

| Status | `code` | When |
| --- | --- | --- |
| 409 | `period_not_ended` | the month has not ended in the time zone of the school |
| 409 | `closing_out_of_sequence` | months are closed in order (`detail` says the next one: `2026-04`); closing a month again before reopening it also answers this |
| 409 | `period_already_closed` | a second active closing of the same month (the sequence rule answers first; this is the backstop) |
| 409 | `closing_not_latest`, `closing_already_reopened`, `closing_reopened` | reopening or verifying out of turn |
| 409 | `closing_mismatch`, `report_mismatch` | a PDF was refused because the lines do not add up to the figures |
| 422 | `validation_error` | the body (extra fields are refused; the bank balance is within +-10^12 cents) |

## What the database calculates

Everything on page one of the report and in the summary: opening, the split of the entries (`contributions_in_cents`, `other_in_cents`, `refunds_in_cents`, `expenses_out_cents` with the bank fees, `reimbursements_out_cents`), the closing, the pending reimbursements, `entries_hash` and the `breakdown`. Reopening and the audit are the database's too: every closing and reopening is audited with the person (`monthly_closings.insert`, `monthly_closings.update`), with the free text only as `reopen_reason_present`.

## The monthly PDF

`GET /closings/{id}/report.pdf`, drawn by `app/reports/closing_report.py` with reportlab (pure Python, no system package, standard fonts; `printable()` keeps any text inside what they can draw).

1. **From the snapshot, never recomputed.** The numbers of page one (the box of the period, both balances, the reconciliation with the bank and its difference) are columns of the stored closing, with the verification code. The lines come from `statement_entries` of the closed period, which cannot change: a closed month never receives an entry.
2. **The sections must close with the box.** `build_closing_report` sorts the entries into A (contributions), B (expenses and reimbursements without the fees), C (confirmed returns) and D (bank fees), and checks that, by kind, they add up to the columns of the closing. If they do not, no page is drawn (`closing_mismatch`). Section E (pending, outside the balance) is the state **now** and says so: the box shows the pending reimbursements as they were at the closing.
3. **Every page** has the footer "Página x de y", the verification code and the note about the receipts; the last page has the signature lines. Money is `R$ 1.234,56`, dates are in the time zone of the school.
4. **`viewer` gets no personal data.** Whoever lacks `reports:read` gets the version where every origin and beneficiary is shown by initials (done when the report is built, so the drawing code never sees a name). A contribution with no name is "Contribuinte anônimo".
5. **`report_ref` is written once**, by the first PDF made (`pdf-v1:<instant>:<first 16 of the hash>`); the PDF is not stored, it is reproducible from the snapshot (same bytes for the same data, apart from the generation time).

A reopened closing has no PDF (`409 closing_reopened`).

## The contributions PDF

`GET /reports/contributions.pdf?period=YYYY-MM`, drawn by `app/reports/contributions_report.py` from a direct read, under row level security, of `financial_transactions`, `contributions` and `pix_charges` (no migration).

- **Summary**: how many, total, smallest, largest, average, how many per amount (the five most frequent get a row, the rest are "Outros valores"), and per channel (`contributions.method`: Pix pela plataforma, Pix direto na conta, Dinheiro, Transferência).
- **A. Contribuições dos responsáveis** (group `CONTRIBUTIONS`) adds up to `contributions_in_cents` of `statement_summary` of the same month; **B. Outras entradas** (donations and the other income groups) to `other_in_cents`. Both are checked before drawing (the read is repeated once if the ledger moved in between, then `409 report_mismatch`). Refunds are not contributions and are not here.
- **C. Fora do total**: contributions waiting for payment, in review, cancelled or expired (by the date of the fact). They never enter a sum.
- Each line: date and time, reference (`APM-000042`), guardian (`guardian_name`; "Contribuinte anônimo" when there is none), student and class when the school collects them, channel, the shortened Pix id (of the paid charge, or the end-to-end id of a direct Pix), status, value, and "(ajuste tardio)" for `late_adjustment`.
- The `viewer` gets initials for guardian and student. The class is kept (it is not a person).

## How to add a section to a PDF

1. In `build_closing_report` (or `build_contributions_report`) build the lines of the section from the entries the database returned, and add the **cross-check** that ties them to a column of the closing (or of `statement_summary`). A section nobody checks is a section that can drift.
2. Draw it in `render_*` with `section(title, columns, rows, subtotal_label=..., count=..., total_cents=...)` from `app/reports/layout.py`: it adds the repeated header, the subtotal row and the count.
3. Any name that reaches the page must go through the anonymising step of the build (initials), never be added while drawing.
4. Add the figures to the tests of the route: the section exists, its subtotal is the expected one, the `viewer` version has no name.

## Not done here

- The channel is not a column of `statement_entries` (it would need a migration; the contributions PDF reads it directly instead).
- The report does not break the collection down by week, nor by class.
- A consolidated view of several schools (`org_statement_summary`) has no route: in the MVP a person belongs to one school.
- Section B of the monthly PDF does not say who requested, approved and paid an expense: `statement_entries` does not carry them.
