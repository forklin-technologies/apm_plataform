# The financial model in the database

Decision record: ADR-015 and its revision (`architectural-decisions`), on top of the isolation of ADR-014 (`docs/tenancy.md`). Code: `apps/api/migrations/versions/0007_financial_schema.py`, `apps/api/app/models/financial*.py`, `apps/api/app/seed_financial.py`. This page explains the model, where every invariant is enforced, what the application may rely on, how to add a financial table, and what the design deliberately does not protect against.

The ledger is the heart of the money of the system, so the rule is the one of a ledger: **a settled line never changes, a mistake is fixed by a compensating line, nothing is deleted, the audit log only grows, and the numbers of a statement are computed by the database**.

## Principles

- Money is `bigint` **cents** of BRL. Never floating point. One currency.
- Instants are `timestamptz` (UTC); months and days are computed in the **time zone of the school** (`school_settings.timezone`, default `America/Sao_Paulo`).
- Every table belongs to an organization and a school (`organization_id`, `school_id`, both `NOT NULL`; `audit_logs.school_id` may be NULL for an event of the organization itself). Row level security uses the scope predicate of ADR-014.
- Every foreign key is `ON DELETE RESTRICT`. There is no cascade and no delete.
- The platform never moves money: the treasury pays through the bank and **records** the payment (`payment_reference`). Only the answer of the provider confirms a Pix payment (ADR-011).

## Tables

| Table | What it is | Notes |
| --- | --- | --- |
| `financial_transactions` (ft) | the ledger: one row per movement, whatever its kind | `kind` CONTRIBUTION, EXPENSE, REIMBURSEMENT or REFUND; `direction` IN or OUT; `amount_cents`; `status`; `occurred_at` (the real date); `settled_at` (when it was booked in the cash ledger); `late_adjustment`; `parent_transaction_id` + `parent_kind`; `created_by_user_id`; `reference_code` |
| `contributions` | detail of a CONTRIBUTION | `method` PIX or CASH; optional `guardian_name`, `student_name`, `class_name` (only when the school enables it, never public); `receipt_token_hash` (SHA-256 of a 128-bit token) and `receipt_expires_at` |
| `expenses` | detail of an EXPENSE | `description`, `vendor`, `paid_by` APM or COLLABORATOR, `submitted_by_user_id`, and who decided: `approved_by_user_id`, `approved_at` (set by the database), `decision_reason` |
| `reimbursements` | detail of a REIMBURSEMENT | `beneficiary_user_id`, `payment_reference` (the payment made outside the system) |
| `refunds` | detail of a REFUND | `reason`, `payment_reference` |
| `expense_attachments` | files attached to an expense | insert-only; `storage_key`, `file_name`, `content_type`, `size_bytes`, `sha256` |
| `categories` | per school, applicable to money IN **or** OUT | `key` (English, stable), `name` (Portuguese), `applies_to`, `is_active` |
| `school_settings` | 1:1 with the school, created with it | time zone, minimum and maximum contribution, Pix expiry minutes, identification mode and required fields, brand colors, approval limit |
| `pix_charges` | a dynamic Pix charge of a PIX contribution | `provider`, `txid`, `status`, `amount_cents`, `expires_at`, `emv_payload`, `end_to_end_id`, `paid_at` |
| `webhook_events` | the raw notification of the provider | `idempotency_key` (event id or end-to-end id), `raw_payload`, `signature_valid`, `processed_at`, `attempts`, `processing_error` |
| `audit_logs` | what changed, by whom | written by triggers; append-only |
| `monthly_closings` | a closed month (a snapshot) | figures and hash computed by the database |

### Kinds, directions and statuses

| Kind | Direction | Statuses (the transitions are the state machine in code; the database checks the values) |
| --- | --- | --- |
| CONTRIBUTION | IN | PENDING_PAYMENT → PAID, EXPIRED or CANCELLED. A CASH contribution is entered by the treasury and is born PAID |
| EXPENSE | OUT | SUBMITTED → APPROVED or REJECTED; APPROVED → PAID or CANCELLED. `paid_by` COLLABORATOR is paid back by a reimbursement |
| REIMBURSEMENT | OUT | PENDING → PAID or CANCELLED. Created when an expense paid by a collaborator is approved. Its parent is that expense |
| REFUND | OUT if its parent is a CONTRIBUTION, IN if its parent is an EXPENSE | PENDING → PAID or FAILED |

The final statuses are PAID, EXPIRED, CANCELLED, REJECTED and FAILED. A row in one of them (or with `settled_at`) never changes again.

## Where every invariant is enforced

Each one is enforced **in the database**, usually twice (a declarative constraint and a trigger or a column grant). The application's state machine decides which transition is allowed; the database makes sure that what is written is a valid, consistent and unalterable ledger.

| Invariant | Enforced by |
| --- | --- |
| `amount_cents > 0` and below 10^12 | `ck_financial_transactions_amount_range` (and the same for `pix_charges`) |
| direction follows from the kind, and for a refund from the kind of its parent | `ck_financial_transactions_shape`, with `parent_kind` tied to the parent by the composite foreign key |
| status is valid for the kind | `ck_financial_transactions_status_for_kind` |
| a settled row has `settled_at`, and only a settled row | `ck_financial_transactions_paid_iff_settled` |
| the parent is in the same school and of the declared kind | composite FK `fk_financial_transactions_parent` |
| a category applies to the direction of the row | composite FK `fk_financial_transactions_category` |
| a detail row belongs to a ledger row of the same school and of its kind | composite FK `fk_<detail>_transaction_id_financial_transactions` (it carries a constant `kind`) |
| every ledger row has the detail row of its kind | deferred trigger `ft_90_consistency` (at commit) |
| nobody decides their own expense | `ck_expenses_decider_is_not_submitter`, and `ft_90_consistency` requires the decider of an APPROVED, REJECTED or PAID expense |
| a settled or final row never changes | `ft_05_immutable` + `ft_06_freeze`, and the column grants (no `UPDATE` on `id`, `organization_id`, `school_id`, `kind`, `direction`, `amount_cents`, the parent columns) |
| value, kind, school, parent and reference code never change, settled or not | `ft_05_immutable` |
| nothing is deleted or truncated | no `DELETE` grant, no `DELETE` policy, and the triggers `<table>_no_delete` and `<table>_no_truncate` (they also stop the owner and the admin) |
| the audit log only grows | `audit_logs` has `INSERT` and `SELECT` only, plus `audit_logs_90_no_update` |
| a refund of an expense only when the APM paid it | `ft_20_relations` |
| refunds of a transaction add up to at most its amount | `ft_20_relations`, which locks the parent row (partial refunds are allowed) |
| a refund needs a PAID parent | `ft_20_relations` |
| a reimbursement: approved collaborator expense, its exact amount, one active per expense | `ft_20_relations` and `uq_financial_transactions_one_active_reimbursement` |
| a cash contribution records who entered it and is born PAID | `ft_90_consistency` |
| a Pix contribution is PAID only with a PAID Pix charge of the same amount | `ft_90_consistency` (only the provider's answer confirms) |
| a collaborator expense is PAID only with a PAID reimbursement | `ft_90_consistency` |
| a PAID reimbursement or refund records its `payment_reference` | `ft_90_consistency` |
| a Pix charge needs a PENDING_PAYMENT PIX contribution and its exact amount; one PENDING charge per contribution | `pix_charges_20_contribution` and `uq_pix_charges_one_pending_per_contribution` |
| personal data of a contribution can only be erased, never rewritten | `contributions_10_anonymize` |
| an expense is edited only while SUBMITTED; the decision is set once | `expenses_20_editable`, `expenses_10_set_once` |
| a webhook is idempotent | `uq_webhook_events_school_id_provider_idempotency_key` (insert with `ON CONFLICT DO NOTHING`) |
| the time zone never changes after the first settlement | `school_settings_10_timezone` |
| a month is closed once, in order, after it ended | `monthly_closings_20_snapshot`, `uq_monthly_closings_active_period` |

## Isolation: the lessons of M1

Foreign key checks run **without** row level security. A single-column foreign key to a tenant table therefore answers "does this id exist anywhere?" to whoever writes it, and a unique index that is not scoped to the school answers the same with a duplicate-key error. Everything below exists to close that:

- **No single-column foreign key to a tenant table.** A parent, a category, a detail, a charge, an attachment: every reference carries `organization_id` and `school_id` (and the kind where it matters). Pointing at a row of another school fails with exactly the error of a missing id. The only single-column foreign keys are to `organizations` (the `WITH CHECK` pins the organization to the context) and to `users` (below). A catalog test enforces both.
- **The primary key of a detail is `(transaction_id, organization_id, school_id)`**, not the transaction id alone: a primary key on the id would answer "this id has a detail" before the foreign key refuses an id of another tenant. There is still one detail per transaction, because the composite foreign key ties all three columns to the one ledger row.
- **Every unique index the application can feed carries `school_id`** (`uq_pix_charges_one_pending_per_contribution`, `uq_financial_transactions_one_active_reimbursement`, the attachment unique, the Pix and webhook uniques). The one exception, `uq_contributions_receipt_token_hash`, is the hash of a 128-bit secret and must be looked up without a tenant (`resolve_receipt`, ADR-016). A catalog test enforces the rule.
- **The `WITH CHECK` of the write policies also requires that the school belongs to the organization of the row.** The scope predicate alone, in an organization-wide context, only compares the organization: a row with the organization of the caller and a school of another organization passed the policy and reached a unique index first. The subquery runs under the row level security of `schools`, so another tenant's school is simply not there.
- **`*_user_id` columns are foreign keys to `users` (global) guarded by a trigger.** `assert_active_member` raises, before the foreign key, a uniform error unless the user holds an `active` membership of the organization that covers the school of the row **and is visible in the context**. A user of another tenant, a user without membership and a made-up id fail identically. The membership of an organization-wide administrator is only visible in an organization-wide context: in a school context, name a member of that school.
- **Tenant hops.** Updating `organization_id` or `school_id` is refused by the column privileges (`apm_app`) and by `ft_05_immutable` and its siblings (the owner and the admin). Writing a row into another tenant is refused by the policies.

## Grants

`apm_app` has `SELECT` on the whole of each financial table, `INSERT` and `UPDATE` **by column**, and nothing else. Columns the database assigns (`id`, `reference_code`, `late_adjustment`, `approved_at`, `reopened_at`, the actor of an audit row, timestamps) are in no `INSERT` list, so the application cannot choose them. The exact lists are asserted from the catalog in `tests/financial/test_grants.py`.

| Table | `INSERT` | `UPDATE` |
| --- | --- | --- |
| `financial_transactions` | scope, `kind`, `direction`, `amount_cents`, `status`, `category_id`, `occurred_at`, `settled_at`, parent columns, `created_by_user_id` | `status`, `settled_at`, `updated_at` |
| `contributions` | key, scope, `method`, the three names, receipt hash and expiry | the three names (to erase them), `updated_at` |
| `expenses` | key, scope, `description`, `vendor`, `paid_by`, `submitted_by_user_id` | `description`, `vendor`, `approved_by_user_id`, `decision_reason`, `updated_at` |
| `reimbursements`, `refunds` | key, scope and `beneficiary_user_id` / `reason` | `payment_reference`, `updated_at` |
| `expense_attachments` | everything but the id | none |
| `categories` | scope, `key`, `name`, `applies_to`, `is_active` | `name`, `is_active`, `updated_at` |
| `school_settings` | the business columns | the business columns |
| `pix_charges` | key, scope, `provider`, `txid`, `status`, `amount_cents`, `expires_at`, `emv_payload` | `status`, `end_to_end_id`, `paid_at`, `emv_payload`, `updated_at` |
| `webhook_events` | scope, `provider`, `idempotency_key`, `end_to_end_id`, `raw_payload`, `signature_valid` | `processed_at`, `processing_error`, `attempts` |
| `audit_logs` | scope, `action`, `entity_type`, `entity_id`, `before_data`, `after_data` | none |
| `monthly_closings` | scope, `period_start`, `closed_by_user_id` | `reopened_by_user_id`, `reopen_reason`, `report_ref` |

Functions: `EXECUTE` goes only to `apm_app`, only for `statement_entries`, `statement_summary`, `statement_pending`, `closing_entries_hash` and `verify_closing` (plus the two context functions). Trigger functions need no `EXECUTE`. There is **no `SECURITY DEFINER` function** in this schema: every function is `SECURITY INVOKER` with `search_path = pg_catalog`, owned by `apm_owner`.

## The reference code

`reference_code` (`bigint`, per school) is the number the school sees. It is assigned **only** by the trigger `ft_10_reference_code` (the application has no `INSERT` on the column, and even an admin asking for a number gets the next one): a transaction-scoped advisory lock of the school, then `max(reference_code) + 1`. It never repeats, it never skips (a rollback gives the number back), and it needs no counter table. `UNIQUE (school_id, reference_code)` is the backstop. It relies on `READ COMMITTED`, the default: under a stricter level a second writer fails with a unique violation, it never duplicates. Creating a ledger row serialises with the others of the **same school** until commit; schools do not wait for each other. Format it in the presentation (`'APM-000042'`).

## The booking of a settlement and the late adjustment

`settled_at` is the instant a movement was **booked in the cash ledger** (NULL while pending); `occurred_at` is the real date of the fact. The trigger `ft_30_settle` runs when `settled_at` is first set:

1. it takes the **shared** advisory lock of the period of the school (a month closing takes the exclusive one), so a settlement and a closing never interleave;
2. it refuses a date in the future (more than 5 minutes ahead);
3. if the local date (in the time zone of the school) is on or before the end of the **latest active closing**, it sets `settled_at = clock_timestamp()` and `late_adjustment = true`: the entry is booked in the open period and flagged (ADR-015). The real date stays in `occurred_at`.

So the application must **read `settled_at` back** after settling (`RETURNING`, or a refresh). A closed month can never receive an entry, which is what makes its snapshot reproducible forever. For an expense paid by a collaborator, `settled_at` of the expense is the booking of its reimbursement (the expense itself never moves the cash, see below).

## The statement (cash basis)

Functions, all `STABLE`, `SECURITY INVOKER` (row level security applies: another school's id returns nothing):

- `statement_entries(school, from, to)`: the settled cash entries of the period, with `signed_amount_cents` (IN positive, OUT negative), `opening_balance_cents` and `running_balance_cents`, ordered by `(settled_at, reference_code)`, plus `local_date` and `late_adjustment`;
- `statement_summary(school, from, to)`: one row, even with no movement: time zone, opening, total in, total out, closing, count;
- `statement_pending(school)`: what is not settled, **outside the balance**: `RECEIVABLE` (a contribution waiting for payment, a refund of an expense), `PAYABLE` (an APM expense that is approved, a reimbursement, a refund of a contribution), `AWAITING_APPROVAL` (a submitted expense).

What counts as cash: a row with `settled_at` and, for an EXPENSE, `paid_by = 'APM'`. An expense paid by a collaborator moves the cash **only through its reimbursement**, so it is never counted twice. A contribution that is PAID comes in; an APM expense that is PAID goes out; a reimbursement that is PAID goes out; a refund goes out for a contribution and comes in for an expense. The period is `[from 00:00, to + 1 day 00:00)` in the time zone of the school. The **opening balance** is the signed sum of the cash entries booked before the start. Do not sum `financial_transactions` yourself.

## The monthly closing

`INSERT INTO monthly_closings (organization_id, school_id, period_start, closed_by_user_id)`: that is all the application says. The trigger `monthly_closings_20_snapshot`, under the exclusive lock of the school and in a `READ COMMITTED` transaction:

1. requires the month to have **ended** in the time zone of the school, and the closings to be **sequential** (the next one starts the day after the latest active one; the first may start anywhere and carries everything before it);
2. calls `statement_summary` and **writes** the opening, totals, closing, entry count and `entries_hash`: whatever the caller passes in those columns is overwritten, so a snapshot that disagrees with the ledger cannot be written (the `CHECK` also verifies `closing = opening + in - out`).

`entries_hash` is the SHA-256 (PostgreSQL's built-in `sha256`, hex) of this UTF-8 text, lines joined by LF with no trailing one:

```
<school_id>|<period_start YYYY-MM-DD>|<period_end YYYY-MM-DD>|<opening_balance_cents>
<reference_code>|<transaction_id>|<kind>|<signed_amount_cents>|<settled_at in UTC as YYYY-MM-DDTHH:MM:SS.ffffffZ>|<late_adjustment true|false>
...one line per cash entry of the period, ordered by (settled_at, reference_code)
```

`verify_closing(id)` recomputes the figures and the hash from the ledger and compares: `true` for an intact closing. It is only meaningful for an **active** closing (a reopened one is superseded). The PDF is generated from the stored snapshot, so it is reproducible.

**Reopening** sets `reopened_by_user_id` and `reopen_reason` (at least 10 characters; `reopened_at` is set by the trigger), once, and only on the **latest active** closing. Who may reopen (`organization_admin`) and the audit event are the service's rules. Closing the same month again creates a **new row**: the history is kept, and a partial unique index allows one active closing per school and month. `report_ref` is set once, after the PDF exists.

## The audit log

Every `INSERT` and `UPDATE` of the ledger, the details, `pix_charges`, `expense_attachments`, `categories`, `school_settings` and `monthly_closings` writes an `audit_logs` row from an `AFTER` trigger (`audit_row_change`, trigger `<table>_95_audit`), in the same transaction: a rolled-back change leaves no record and the application cannot forget it. `webhook_events` is itself a log and is not audited. The service may add high-level events (`expense.approved`) with a plain `INSERT`.

**What is stored.** `entity_type` is the table, `entity_id` its key, `action` is `<table>.insert` or `<table>.update`. `after_data` holds the audited columns; on `UPDATE`, `before_data` and `after_data` hold **only the keys that changed** (and nothing is written when no audited column changed). **Free text and secrets are never copied**: `guardian_name`, `student_name`, `class_name`, `receipt_token_hash`, `description`, `vendor`, `decision_reason`, `reason`, `payment_reference`, `file_name`, `storage_key`, `emv_payload`, `report_ref` and `reopen_reason` appear only as `<column>_present: true|false`, so an anonymisation is visible without the data. Ids, amounts, statuses and dates are kept.

**Who: the contract with the authentication layer.** The actor never comes from a column the application writes; it comes from transaction-local settings (`set_config(name, value, true)`):

| Setting | Meaning |
| --- | --- |
| `app.user_id` | the authenticated user (a uuid); it must be an **active member** of the school, or the write fails with the uniform membership error |
| `app.actor_type` | `USER`, `SYSTEM` (a job, set explicitly) or `PUBLIC` (the public flow); defaults to `USER` when `app.user_id` is set and `PUBLIC` when it is not |
| `app.request_id` | the id of the request (at most 200 characters) |
| `app.client_ip` | optional: the client address (an `inet`) |

The trigger `audit_logs_05_actor` fills `actor_user_id`, `actor_type`, `request_id`, `ip` and `occurred_at` from them (and `audit_logs` has no `INSERT` privilege on those columns), `ck_audit_logs_actor_matches_type` requires `actor_type = 'USER'` exactly when there is a user, and `audit_logs_10_member` checks the actor. Without the settings the actor is NULL and `PUBLIC`. `app.user_id`, `app.request_id` and `app.actor_type` are defined by TASK-004 together with the session; `app.client_ip` is proposed here and optional.

## Trigger inventory

Within a table and a timing, triggers fire in **alphabetical order of their names**, which is why they carry a number. `tests/financial/test_triggers.py` derives this list from the catalog: a missing, disabled or undocumented trigger fails the suite, and so does a function that is not `SECURITY INVOKER` with a fixed `search_path`.

| Table | Trigger | When | Function and why |
| --- | --- | --- | --- |
| `financial_transactions` | `ft_05_immutable` | BEFORE UPDATE | `assert_immutable_columns`: id, scope, kind, direction, amount, parent, reference code and author never change |
| | `ft_06_freeze` | BEFORE UPDATE | `freeze_when_final`: a PAID, EXPIRED, CANCELLED, REJECTED or FAILED row changes in no column |
| | `ft_10_reference_code` | BEFORE INSERT | `ft_assign_reference_code`: the gapless per-school number (advisory lock) |
| | `ft_20_relations` | BEFORE INSERT (with a parent) | `ft_check_relations`: refund and reimbursement rules; locks the parent |
| | `ft_25_member` | BEFORE INSERT | `assert_active_member('created_by_user_id')` |
| | `ft_30_settle` | BEFORE INSERT OR UPDATE | `ft_settle`: the booking and the late adjustment |
| | `ft_90_consistency` | AFTER INSERT OR UPDATE, **deferred** | `ft_check_consistency`: the rules that look at two tables, at commit |
| | `financial_transactions_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `contributions` | `contributions_05_immutable` | BEFORE UPDATE | `assert_immutable_columns` |
| | `contributions_10_anonymize` | BEFORE UPDATE | `assert_anonymize_only`: the three names can only go from a value to NULL |
| | `contributions_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `expenses` | `expenses_05_immutable` | BEFORE UPDATE | `assert_immutable_columns` |
| | `expenses_10_set_once` | BEFORE UPDATE | `assert_set_once_columns`: the decision is written once |
| | `expenses_15_decision_time` | BEFORE INSERT OR UPDATE | `expenses_set_decision_time`: `approved_at` is the database's clock |
| | `expenses_20_editable` | BEFORE UPDATE | `expenses_edit_only_while_submitted` |
| | `expenses_25_member` | BEFORE INSERT OR UPDATE OF the user columns | `assert_active_member` |
| | `expenses_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `reimbursements` | `reimbursements_05_immutable`, `reimbursements_10_set_once` | BEFORE UPDATE | `assert_immutable_columns`, `assert_set_once_columns` (the payment reference) |
| | `reimbursements_25_member` | BEFORE INSERT | `assert_active_member` |
| | `reimbursements_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `refunds` | `refunds_05_immutable`, `refunds_10_set_once` | BEFORE UPDATE | as above |
| | `refunds_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `expense_attachments` | `expense_attachments_05_no_update` | BEFORE UPDATE | `forbid_update`: insert-only |
| | `expense_attachments_25_member` | BEFORE INSERT | `assert_active_member` |
| | `expense_attachments_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `categories` | `categories_05_immutable`, `categories_95_audit` | BEFORE UPDATE, AFTER | `assert_immutable_columns`, `audit_row_change` |
| `school_settings` | `school_settings_05_immutable`, `school_settings_10_timezone`, `school_settings_95_audit` | BEFORE UPDATE, AFTER | `assert_immutable_columns`, `school_settings_lock_timezone`, `audit_row_change` |
| `pix_charges` | `pix_charges_05_immutable`, `pix_charges_06_freeze`, `pix_charges_10_set_once` | BEFORE UPDATE | `assert_immutable_columns`, `freeze_when_final` (PAID, EXPIRED, CANCELLED), `assert_set_once_columns` |
| | `pix_charges_20_contribution` | BEFORE INSERT | `pix_charges_check_contribution` |
| | `pix_charges_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `webhook_events` | `webhook_events_05_immutable`, `webhook_events_10_set_once` | BEFORE UPDATE | the raw event never changes; `processed_at` is set once |
| `audit_logs` | `audit_logs_05_actor` | BEFORE INSERT | `audit_logs_fill_actor` |
| | `audit_logs_10_member` | BEFORE INSERT | `assert_active_member('actor_user_id')` |
| | `audit_logs_90_no_update` | BEFORE UPDATE | `forbid_update` |
| `monthly_closings` | `monthly_closings_05_immutable`, `monthly_closings_10_set_once` | BEFORE UPDATE | the snapshot never changes; `report_ref`, `reopened_*` are set once |
| | `monthly_closings_15_reopen` | BEFORE UPDATE | `monthly_closings_reopen`: latest active closing only |
| | `monthly_closings_20_snapshot` | BEFORE INSERT | `monthly_closings_snapshot`: computes everything |
| | `monthly_closings_25_member` | BEFORE INSERT OR UPDATE OF the user columns | `assert_active_member` |
| | `monthly_closings_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| every table above | `<table>_no_delete`, `<table>_no_truncate` | BEFORE DELETE, BEFORE TRUNCATE (statement) | `forbid_delete`, `forbid_truncate` |
| `schools` | `schools_10_settings` | AFTER INSERT | `schools_create_settings`: a school is born with its settings |

Other functions: `statement_entries`, `statement_summary`, `statement_pending`, `closing_entries_hash`, `verify_closing` (callable by `apm_app`), and `app_org`, `app_school` (tenancy).

## How to create a new financial table

1. Follow the checklist of `docs/tenancy.md`, with these additions, in a new migration (numbers are asked from the Architect).
2. Columns `organization_id` and `school_id` `NOT NULL`, and the composite foreign key `(school_id, organization_id)` to `schools`. **Every reference to another tenant table is a composite foreign key that carries the organization and the school** (and the kind of a ledger row). A detail's primary key carries them too.
3. **Every unique index the application can feed carries `school_id`.** If a value must be unique across tenants, write down why it reveals nothing, as `uq_contributions_receipt_token_hash` does.
4. `ENABLE` and `FORCE ROW LEVEL SECURITY`; `SELECT` with the scope predicate; `INSERT` and `UPDATE` with `WITH CHECK` of **scope and "the school belongs to the organization of the row"**. No `DELETE` policy.
5. `INSERT` and `UPDATE` grants **by column**, never on ids, scope, kind or amount; no `DELETE`.
6. Triggers: `assert_immutable_columns` for what never changes, a freeze for final states, `assert_active_member` for every `*_user_id`, `forbid_delete` and `forbid_truncate`, the audit trigger with an allow-list that leaves out free text. Number them for the firing order.
7. A model in `app/models/financial*.py` with the same names; a line in the matrix (`tests/financial/test_isolation.py`), the grants, the immutability, the trigger inventory and the CHECK cases (the coverage tests fail until you add them), and a row in this page.
8. Downgrade that reverses it all.

## Using it from the application

- Create a ledger row and its detail row **in the same transaction**: insert the ledger row first (the foreign key of the detail needs it; `INSERT ... RETURNING id` gives you the id, which the application cannot choose), then the detail. The check that every ledger row has its detail runs at commit.
- Load a detail by `Expense.transaction_id == id` (its primary key is the three columns).
- Settle with a compare-and-swap, `UPDATE ... SET status = 'PAID', settled_at = ... WHERE id = :id AND status = :expected RETURNING settled_at`: a second confirmation matches no row. Read `settled_at` back.
- Webhooks: `INSERT ... ON CONFLICT DO NOTHING` on `(school_id, provider, idempotency_key)`; a returned row count of 0 is a duplicate. Minimise the payload before storing it (below).
- Set the actor settings (`app.user_id`, `app.request_id`, `app.actor_type`) at the start of every transaction, together with the tenant context.
- Do not edit an amount: cancel and create a new row. Correct a settled row with a REFUND (or by reversing the reimbursement of an expense paid by a collaborator).

## Accepted risks

- **Anyone with `DISABLE TRIGGER` can bypass the triggers**: the owner (`apm_owner`, which is `NOLOGIN`), the admin role (a superuser in development), and any superuser through `session_replication_role = replica`. `apm_app` can do none of it (a test proves it). Operationally this means: the admin credential never enters the API process (ADR-014); it is used only by migrations, the seed and a person who restores or repairs data, with the action recorded outside the database; the database log should record DDL (`log_statement = 'ddl'`); and `audit_logs` should be backed up and shipped out of the database in production (a hash chain of the audit log is not implemented). The declarative constraints, the grants and the policies do not depend on the triggers.
- **`webhook_events.raw_payload` may carry the CPF and the name of the payer.** The Pix layer (TASK-006) must **minimise it before storing**: keep only what is needed to process and reconcile (the end-to-end id, the amount, the txid, the time), never the payer's identification. The audit log never holds it.
- **The context and the actor settings can be forged by arbitrary SQL run as `apm_app`** (ADR-014): row level security and the actor of the audit protect against bugs of the application, not against SQL injection or remote code execution. Mitigations are the same as in `docs/tenancy.md`.
- **A transaction that holds an advisory lock can be held by injected SQL** (the locks of a school are held to the end of the transaction): a denial of service, accepted with the rest of the SQL-injection model.
- **`reference_code` and the closing need `READ COMMITTED`.** Under `REPEATABLE READ` or `SERIALIZABLE` a reference-code collision fails with a unique violation (it never duplicates) and a closing is refused.
- **The backfill of `school_settings` for schools that already exist runs as the admin**: it works with a superuser (development). With a non-superuser admin, row level security hides the schools and nothing is inserted, so the first deployment must not have schools created before this migration (there is no flow that creates one without the trigger).
- **A late Pix payment** (the charge is confirmed after the contribution EXPIRED or was CANCELLED) is not forced into a state by the database: the application must record it (for example by refunding), because a closed row cannot change.
- **Roles.** A reader sees the data of every school of an organization-wide context. Which role may see personal data of the families, approve, pay or close is the permission map of the code (ADR-016), not the database.

## Tests

`apps/api/tests/financial/`:

| File | Criterion |
| --- | --- |
| `test_migration_cycle.py` | F1: upgrade and downgrade with data, repeated, backfill |
| `test_isolation.py`, `test_references.py` | F2: the matrix (12 tables x operations x 5 contexts x 2 roles), tenant hops, no foreign key or unique index that reveals another school |
| `test_immutability.py`, `test_grants.py` | F3 and F8: every protected column by every path, no delete, the exact grants |
| `test_statement.py` | F4: hand-worked scenarios, the closing and its hash recomputed in Python, the balance property over 40 random ledgers |
| `test_concurrency.py` | F5: real threads: one settlement, no repeated or skipped reference code, settlement against closing |
| `test_checks.py` | F6: every CHECK is exercised, and a coverage test |
| `test_triggers.py`, `test_audit.py` | the trigger inventory and the rules; the audit trigger |
| `test_performance.py` | 100 thousand movements, plans read with `auto_explain` |
| `test_models.py`, `test_seed_financial.py` | the models match the database; the seed |

The 100-thousand-row test and the concurrency tests take a few seconds; run a file with `docker compose run --rm tools pytest tests/financial/test_x.py`.
