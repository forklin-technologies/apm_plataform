# The financial model in the database

Decision record: ADR-015 and its second and third revisions (`architectural-decisions`), on top of the isolation of ADR-014 (`docs/tenancy.md`). Code: `apps/api/migrations/versions/0007_financial_schema.py`, `apps/api/app/models/financial*.py`, `apps/api/app/seed_financial.py`. This page explains the model, where every invariant is enforced, what the application may rely on, how to add a financial table, and what the design deliberately does not protect against.

The ledger is the heart of the money of the system, so the rule is the one of a ledger: **a settled line never changes, a mistake is fixed by a compensating line, nothing is deleted, the audit log only grows, and the numbers of a statement are computed by the database**.

## Principles

- Money is `bigint` **cents** of BRL. Never floating point. One currency.
- Instants are `timestamptz` (UTC); months and days are computed in the **time zone of the school** (`school_settings.timezone`, default `America/Sao_Paulo`).
- Every table belongs to an organization and a school (`organization_id`, `school_id`, both `NOT NULL`; `audit_logs.school_id` may be NULL for an event of the organization itself). A network is an organization with several schools; row level security uses the scope predicate of ADR-014.
- Every foreign key is `ON DELETE RESTRICT`. There is no cascade and no delete.
- The platform never moves money: the treasury pays through the bank and **records** the payment (`payment_reference`, `paid_by_user_id`). Only the answer of the provider confirms a Pix payment (ADR-011).
- **The database holds no credential of any provider.** `payment_accounts` keeps a *reference* to a secret (`secret_ref`, `env:NAME` or `vault:path`), never the secret (a catalog test fails if a column looks like one).
- Every movement has a **purpose** (`category_id`, never NULL) and an **origin** (`origin_type`, `origin_name`, `origin_user_id`).

## Tables

| Table | What it is | Notes |
| --- | --- | --- |
| `financial_transactions` (ft) | the ledger: one row per movement, whatever its kind | `kind` CONTRIBUTION, EXPENSE, REIMBURSEMENT or REFUND (a *devolução*); `direction` IN or OUT; `amount_cents`; `status`; `category_id` (the purpose); `origin_type`, `origin_name`, `origin_user_id`; `occurred_at` (the real date); `settled_at` (when it was booked in the cash ledger); `late_adjustment`; `parent_transaction_id` + `parent_kind`; `created_by_user_id`; `reference_code` |
| `contributions` | detail of a CONTRIBUTION | `method` PIX (through the charge of the platform), **PIX_DIRECT** (paid straight to the key of the APM, registered or reconciled by the management), CASH, TRANSFER or OTHER; `external_reference` (the end-to-end id of a PIX_DIRECT Pix, and only of it); optional `guardian_name` (the payer), `student_name`, `class_name`, `contributor_email`, `contributor_phone` (only what the school enables, never public, **erasable**); `receipt_token_hash` (SHA-256 of a 128-bit token) and `receipt_expires_at`; `review_decision_reason` |
| `expenses` | detail of an EXPENSE | `description`, `vendor`, `purchase_reason`, `payment_method` (PIX, CARD, CASH, OTHER), `paid_by` APM or COLLABORATOR, `submitted_by_user_id`, and the decision: `approved_by_user_id`, `approved_at` (set by the database), `approved_amount_cents`, `decision_reason`, `correction_reason` |
| `reimbursements` | detail of a REIMBURSEMENT | `beneficiary_user_id`, `paid_by_user_id` and `payment_reference` (the payment made outside the system) |
| `refunds` | detail of a REFUND | `reason`, `payment_reference`, `confirmed_by_user_id` |
| `expense_attachments` | files attached to an expense | insert-only; `kind` INVOICE, PAYMENT_PROOF or OTHER; `storage_key`, `file_name`, `content_type`, `size_bytes`, `sha256` |
| `categories` | per school, applicable to money IN **or** OUT | `key` (English, stable), `name` (Portuguese), `applies_to`, `report_group`, `requires_approval` (false only for the bank fees), `is_active` |
| `school_settings` | 1:1 with the school, created with it | time zone, minimum and maximum contribution, `suggested_amounts_cents` (1 to 6 values), `allow_custom_amount`, Pix expiry minutes, `required_fields` and `optional_fields` of the public form, brand colors, approval limit |
| `payment_accounts` | the Pix account of a school at a provider | `provider` BB or SANDBOX, `external_account_id`, `status` ACTIVE, INACTIVE or PENDING, `secret_ref`, `webhook_secret_hash`; **one ACTIVE account per school** |
| `pix_charges` | a dynamic Pix charge of a PIX contribution | `payment_account_id`, `provider`, `txid`, `status`, `amount_cents` (expected), `received_amount_cents`, `divergence_reason`, `expires_at`, `emv_payload`, `end_to_end_id`, `paid_at` |
| `webhook_events` | the raw notification of the provider | `idempotency_key` (event id or end-to-end id), `raw_payload`, `signature_valid`, `processed_at`, `attempts`, `processing_error` |
| `audit_logs` | what changed, by whom | written by triggers; append-only; `entity_reference` is the `reference_code` the people see |
| `monthly_closings` | a closed month (a snapshot) | the figures, the hash and the breakdown are computed by the database; `bank_balance_reported_cents` (the balance according to the bank, given at closing) and `bank_difference_cents` (computed): the reconciliation |

### Purpose and origin

The **purpose** is the category; its `report_group` decides where it appears in the monthly report: `CONTRIBUTIONS`, `OTHER_INCOME` and `REFUNDS` for money IN, `EXPENSES_REIMBURSEMENTS` and `BANK_FEES` for money OUT (`ck_categories_group_matches_direction`). Every school is created with the default set (`parent_contribution`, `donation`, `other_income`, `apm_revenue`, `refund` in; `teacher_reimbursement`, `director_reimbursement`, `school_supplies`, `services`, `other_authorized` and `bank_fees`, *Tarifas bancárias*, out), which the seed installs and the school may extend.

The **origin** says who the money comes from or who spent it: `origin_type` GUARDIAN, TEACHER, DIRECTOR, EMPLOYEE, MANAGEMENT, APM, BANK (the bank itself: its fees) or OTHER; `origin_user_id` for a person with an account (a guarded foreign key); `origin_name` for a person without one. **A contribution never has an `origin_name`** (`ck_financial_transactions_no_contributor_name`): the ledger freezes when a line settles, and the name of a payer is personal data that must stay erasable, so it lives only in `contributions.guardian_name`. The statement shows `coalesce(origin_name, name of origin_user_id, guardian_name)`.

### Kinds, directions and the state machines

The database enforces **the values, the initial states and every edge** (`ft_08_initial`, `ft_07_change`). A row in a final state changes in no column (`ft_06_freeze`).

| Kind | Direction | Initial | Edges (final states in **bold**) |
| --- | --- | --- | --- |
| CONTRIBUTION | IN | PENDING_PAYMENT (Pix), PAID (cash, transfer, other and PIX_DIRECT: born settled) or REVIEW_REQUIRED (a **PIX_DIRECT** only: a credit seen on the bank and not yet confirmed) | PENDING_PAYMENT → **PAID**, **EXPIRED**, **CANCELLED**, REVIEW_REQUIRED; REVIEW_REQUIRED → **PAID**, **CANCELLED** |
| EXPENSE | OUT | DRAFT or SUBMITTED; **APPROVED** only in a category that needs no approval (the bank fees) | DRAFT → SUBMITTED, **CANCELLED**; SUBMITTED → APPROVED, **REJECTED**, CORRECTION_REQUESTED; CORRECTION_REQUESTED → SUBMITTED, **CANCELLED**; APPROVED → **PAID**, **CANCELLED** |
| REIMBURSEMENT | OUT | PENDING | PENDING → **PAID**, **CANCELLED** |
| REFUND (devolução) | IN | REQUESTED | REQUESTED → AWAITING_CONFIRMATION, **REJECTED**; AWAITING_CONFIRMATION → **CONFIRMED**, **REJECTED** |

- A refund is money coming **back** to the APM, so its direction is always IN. Its parent is optional (an EXPENSE, a REIMBURSEMENT, or none for a payment that was simply wrong). It is settled when it is CONFIRMED.
- `settled_at` is set exactly when the status is PAID or CONFIRMED (`ck_financial_transactions_paid_iff_settled`).
- A `status_label` of REIMBURSED is how the statement names a PAID reimbursement.
- `pix_charges.status` is PENDING, PAID, REVIEW_REQUIRED, EXPIRED or CANCELLED; the last four are final (`pix_charges_06_freeze`). A charge is REVIEW_REQUIRED when the amount received is not the amount expected (below).

### The edit window of an expense

The request of an expense (`amount_cents`, `category_id`, `occurred_at`, and in `expenses` the `description`, `vendor`, `purchase_reason` and `payment_method`) is editable **only while it is DRAFT or CORRECTION_REQUESTED**, and only by the author, which the service decides (`ft_07_change`, `expenses_20_editable`). Sending it back with SUBMITTED freezes it again. `correction_reason` (at least 3 characters) is written while the expense is under review and is required in CORRECTION_REQUESTED.

An expense sent for review (SUBMITTED, CORRECTION_REQUESTED, APPROVED, REJECTED, PAID) must have **at least one attachment**, a `purchase_reason` and a `payment_method` (checked at commit by `ft_90_consistency`). Attachments can be added to a DRAFT, to one under review and to an APPROVED one, never to a final expense (`expense_attachments_20_state`).

### Partial approval and returns

The manager may approve **less than was requested**, but only for an expense paid by a collaborator: `approved_amount_cents` is at most `amount_cents`, and lower only when `paid_by = 'COLLABORATOR'` (an expense paid by the APM is approved in full). The reimbursement then has **exactly the approved amount** (`ft_20_relations`). The requested amount stays in `amount_cents`, the approved one in `expenses.approved_amount_cents`.

A return (REFUND) needs a **PAID** parent, and the returns that are REQUESTED, AWAITING_CONFIRMATION or CONFIRMED add up to at most the approved amount of the expense, or the amount of the reimbursement, whichever is its parent. The parent row is locked while it is checked, so two concurrent returns cannot both fit in what is left. A CONFIRMED refund records `confirmed_by_user_id`.

### Pix, the review of a different amount, and the payment accounts

- A PIX contribution is **PAID only when its charge received exactly its amount**: the charge is PAID with `received_amount_cents = amount_cents`.
- When the provider reports another amount, the charge becomes **REVIEW_REQUIRED** (`received_amount_cents` is set, `divergence_reason` of at least 3 characters says why, and `received_amount_cents <> amount_cents`) and the contribution becomes REVIEW_REQUIRED, which is not settled and not in the balance (it is listed under `REVIEW` in `statement_pending`).
- The management then decides, **writing `contributions.review_decision_reason` first** (at least 3 characters, set once, only while the contribution is REVIEW_REQUIRED). Accepting moves the contribution to PAID **and sets its `amount_cents` to the amount received**: this is the **only** transition in which an amount of a contribution may change, and it is checked against the charge (`ft_07_change`). Cancelling moves it to CANCELLED without changing the amount. Without the reason, neither is allowed. The audit log records the amount before and after.
- `payment_accounts` ties a school to its Pix account: a Pix charge needs the **ACTIVE** account of its school, of the same provider (`pix_charges_20_contribution`), and `uq_payment_accounts_one_active_per_school` allows one ACTIVE account per school. `secret_ref` has the form `scheme:path` (`^[a-z][a-z0-9_]{1,19}:[A-Za-z0-9_./-]{1,200}$`), so a raw secret does not fit.
- `webhook_secret_hash` is the SHA-256 (64 hex) of a 128-bit secret, **unique across all schools** so that a webhook can be resolved without a tenant (TASK-006 reads it through a narrow `SECURITY DEFINER` function, ADR-016). It is a hash like the one of a session token, not a credential, and **`apm_app` has no `SELECT` on that column** (a column-level grant: it can write it, never read it back). The ORM model loads it `deferred`, and the audit log records only whether it is set.

### Direct Pix (PIX_DIRECT)

The parents also pay straight to the Pix key of the APM, outside the charge of the platform, and that credit shows on the bank statement. Without it the statement of the platform never matches the one of the bank. It is a contribution with `method = 'PIX_DIRECT'`; `external_reference` carries the **end-to-end id** of the Pix (`^E[A-Za-z0-9]{31}$`), is required **if and only if** the method is PIX_DIRECT (`ck_contributions_external_reference_iff_pix_direct`), is unique **per school** (`uq_contributions_school_id_external_reference`, never global: the M1 lesson) and never changes. The name of the payer goes in `guardian_name`, which can only be erased.

- **A Pix is never two contributions.** The end-to-end id of a PIX_DIRECT contribution is also kept **distinct from every `pix_charges.end_to_end_id` of the school**, in both directions: `contributions_20_insert` refuses the insert when a charge of the school already has that id, and `pix_charges_20_end_to_end_id` refuses a charge that is confirmed with an id a direct Pix already holds. Both raise `unique_violation` and take the same advisory lock (namespace 7003, per school), so two writers at the same moment cannot both miss the row of the other. A **cancelled** direct Pix keeps its reference taken: it means "seen and refused", and registering it again would be a duplicate.
- **Birth.** A direct Pix is born PAID (with `settled_at`) or REVIEW_REQUIRED, never PENDING_PAYMENT, and REVIEW_REQUIRED is a birth state of a direct Pix **only** (`ft_08_initial` lets the ledger row through, `contributions_20_insert` refuses it for any other method, because only the detail row knows the method).
- **Review.** The management decides a REVIEW_REQUIRED direct Pix to PAID or CANCELLED, **writing `review_decision_reason` first** like for a charge in review. There is no charge to compare with, so the amount is the one registered and **a decision never changes it** (`ft_07_change`).
- **Author.** A direct Pix always records who registered it (`created_by_user_id`, checked at commit; the column never changes, so a direct Pix without an author could not be confirmed later, nor registered again). It ends PAID, in review or CANCELLED, and never EXPIRED.
- The rule "a PIX contribution is PAID only with a charge that received its amount" holds for `method = 'PIX'` and **not** for PIX_DIRECT, which has no charge and never gets one (`pix_charges_20_contribution`). A manual contribution (cash, transfer, other) is still born PAID with an author.
- The audit log records `external_reference_present`, never the id.

### Bank fees: the expense nobody approves

The statement of the bank is full of fees ("Tarifa Pix Enviado", "Tarifa Pacote de Serviços"). Nobody approves them: the bank already took the money. `categories.requires_approval` (default true, written once with the category, audited) is false only for the category whose key is `bank_fees` (`ck_categories_no_approval_only_for_bank_fees`: `requires_approval OR key = 'bank_fees'`; the key is unique per school, so **a school has at most one category without approval**). The report group `BANK_FEES` is for money OUT only (`ck_categories_group_matches_direction`). The default `bank_fees` category is that one.

An expense in a category **without approval**:

- is born **APPROVED** and from the bank (`origin_type = 'BANK'`), and in no other state (`ft_08_initial`); an APPROVED start anywhere else is still refused, and a request cannot reach the exemption by moving a draft into the category (the check at commit refuses a draft or a submitted expense there);
- is paid by the APM (`expenses_12_waiver` refuses a collaborator) and gets its `approved_amount_cents` from the database (the amount the bank charged); it has **no approver** (`approved_by_user_id` and `approved_at` stay NULL, and the check at commit refuses one) and is never sent for review, so it needs no attachment, purchase reason or payment method (an attachment is welcome);
- is settled, APPROVED to PAID, by **whoever records it**, and is audited like every other change; until then it is `PAYABLE` in `statement_pending`; a fee recorded by mistake can be cancelled before it is settled;
- counts as an expense paid by the APM: **`expenses_out_cents` includes the fees**, and the `breakdown` of a closing shows them apart in the group `BANK_FEES`. There is no separate `bank_fees_out_cents` column (decision of the Architect; TASK-009 asks for one if the PDF needs it).

### Reconciliation with the bank

The treasury gives the final balance **according to the bank** when the month is closed: `INSERT INTO monthly_closings (..., bank_balance_reported_cents)` (optional, within ±10^12 cents). The database computes `bank_difference_cents = bank_balance_reported_cents - closing_balance_cents` (a generated column: NULL without a reported balance; **positive when the bank holds more than the platform**, for example a direct Pix nobody registered yet). A difference **does not stop the closing**: it is recorded, audited and printed in the monthly report. The reported balance is written once, with the closing, and never changes (to fix it, reopen the month and close it again); it is **not** part of `entries_hash` and `verify_closing` does not recompute it.

## Where every invariant is enforced

Each one is enforced **in the database**, usually twice (a declarative constraint and a trigger or a column grant). The application's service decides who may do what; the database makes sure that what is written is a valid, consistent and unalterable ledger.

| Invariant | Enforced by |
| --- | --- |
| `amount_cents > 0` and below 10^12 | `ck_financial_transactions_amount_range` (and the same for `pix_charges`) |
| direction follows from the kind; a refund is IN with an optional EXPENSE or REIMBURSEMENT parent | `ck_financial_transactions_shape`, with `parent_kind` tied to the parent by the composite foreign key |
| status is valid for the kind, a new row starts in an allowed state, and only the edges of the machine exist | `ck_financial_transactions_status_for_kind`, `ft_08_initial`, `ft_07_change` |
| settled means PAID or CONFIRMED, and only then | `ck_financial_transactions_paid_iff_settled` |
| the parent is in the same school and of the declared kind | composite FK `fk_financial_transactions_parent` |
| a category applies to the direction of the row, and every movement has one | composite FK `fk_financial_transactions_category`, `category_id NOT NULL` |
| a detail row belongs to a ledger row of the same school and of its kind | composite FK `fk_<detail>_transaction_id_financial_transactions` (it carries a constant `kind`) |
| every ledger row has the detail row of its kind | deferred trigger `ft_90_consistency` (at commit) |
| nobody decides their own expense | `ck_expenses_decider_is_not_submitter`; `ft_90_consistency` requires the decider of an APPROVED, REJECTED or PAID expense |
| a final row never changes | `ft_05_immutable` + `ft_06_freeze`, and the column grants (no `UPDATE` on `id`, `organization_id`, `school_id`, `kind`, `direction`, the parent columns, the origin) |
| kind, direction, school, parent, origin and reference code never change, settled or not | `ft_05_immutable` |
| the amount, the purpose and the date change only in the window of an expense (and the amount in the accepted review) | `ft_07_change`, `expenses_20_editable` |
| nothing is deleted or truncated | no `DELETE` grant, no `DELETE` policy, and the triggers `<table>_no_delete` and `<table>_no_truncate` (they also stop the owner and the admin) |
| the audit log only grows | `audit_logs` has `INSERT` and `SELECT` only, plus `audit_logs_90_no_update` |
| approved amount at most the requested one, lower only for a collaborator | `ft_90_consistency` |
| a reimbursement: an APPROVED collaborator expense, exactly the approved amount, one active per expense | `ft_20_relations` and `uq_financial_transactions_one_active_reimbursement` |
| a return needs a PAID parent and fits in what is left | `ft_20_relations`, which locks the parent row |
| a PAID reimbursement records who paid it and the payment reference; a CONFIRMED refund records who confirmed it | `ft_90_consistency` |
| a manual contribution (cash, transfer, other) is born PAID and records who entered it | `ft_90_consistency` |
| a direct Pix has an end-to-end id (and only it), registered by someone, born PAID or in review; the same Pix is never two contributions | `ck_contributions_external_reference_iff_pix_direct`, `ck_contributions_external_reference_format`, `uq_contributions_school_id_external_reference`, `contributions_20_insert`, `pix_charges_20_end_to_end_id`, `ft_90_consistency` |
| REVIEW_REQUIRED is a birth state of a direct Pix only; its decision keeps the amount and records its reason | `ft_08_initial`, `contributions_20_insert`, `ft_07_change` |
| only the category `bank_fees` (one per school, money OUT) goes without an approver; such an expense is born APPROVED, from the bank, paid by the APM, with no approver | `ck_categories_no_approval_only_for_bank_fees` with `uq_categories_school_id_key`, `ck_categories_group_matches_direction`, the column grant (`requires_approval` is written at insert only, never updated), `ft_08_initial`, `expenses_12_waiver`, `ft_90_consistency` |
| the bank balance is given with the closing and never changes; the difference is computed | `monthly_closings_20_snapshot` leaves it, `monthly_closings_05_immutable`, the generated column `bank_difference_cents`, `ck_monthly_closings_bank_balance_range` |
| a Pix contribution is PAID only with a charge that received exactly its amount; REVIEW_REQUIRED only with a charge in review | `ft_90_consistency` (only the provider's answer confirms) |
| a rejection records its reason; a correction request records what to correct | `ft_90_consistency`, `ck_expenses_decision_reason_length`, `ck_expenses_correction_reason_length` |
| a review decision records its reason, written first and once | `ft_07_change`, `contributions_12_set_once`, `contributions_15_review` |
| a collaborator expense is PAID only with a PAID reimbursement | `ft_90_consistency` |
| an expense under review has an attachment, a reason and a payment method | `ft_90_consistency`; attachments only while not final: `expense_attachments_20_state` |
| a Pix charge needs a PENDING_PAYMENT PIX contribution, its exact amount, and the ACTIVE account of the school; one PENDING charge per contribution | `pix_charges_20_contribution` and `uq_pix_charges_one_pending_per_contribution` |
| one ACTIVE payment account per school; no credential column | `uq_payment_accounts_one_active_per_school`; `ck_payment_accounts_secret_ref_format` and a catalog test |
| personal data of a contribution can only be erased, never rewritten | `contributions_10_anonymize` |
| the decision of an expense is set once | `expenses_10_set_once`, `expenses_15_decision_time` |
| a webhook is idempotent | `uq_webhook_events_school_id_provider_idempotency_key` (insert with `ON CONFLICT DO NOTHING`) |
| the time zone never changes after the first settlement | `school_settings_10_timezone` |
| the suggested amounts fit the minimum and the maximum; the required and optional fields are disjoint and known | `ck_school_settings_suggested_amounts_valid`, `ck_school_settings_identification_fields_valid` |
| a month is closed once, in order, after it ended | `monthly_closings_20_snapshot`, `uq_monthly_closings_active_period` |

## Isolation: the lessons of M1

Foreign key checks run **without** row level security. A single-column foreign key to a tenant table therefore answers "does this id exist anywhere?" to whoever writes it, and a unique index that is not scoped to the school answers the same with a duplicate-key error. Everything below exists to close that:

- **No single-column foreign key to a tenant table.** A parent, a category, a payment account, a detail, a charge, an attachment: every reference carries `organization_id` and `school_id` (and the kind or direction where it matters). Pointing at a row of another school fails with exactly the error of a missing id. The only single-column foreign keys are to `organizations` (the `WITH CHECK` pins the organization to the context) and to `users` (below). A catalog test enforces both.
- **The primary key of a detail is `(transaction_id, organization_id, school_id)`**, not the transaction id alone: a primary key on the id would answer "this id has a detail" before the foreign key refuses an id of another tenant. There is still one detail per transaction, because the composite foreign key ties all three columns to the one ledger row.
- **Every unique index the application can feed carries `school_id`** (`uq_pix_charges_one_pending_per_contribution`, `uq_financial_transactions_one_active_reimbursement`, `uq_payment_accounts_one_active_per_school`, the attachment unique, the Pix and webhook uniques). The two exceptions are the hash of a 128-bit secret, which must be looked up without a tenant: `uq_contributions_receipt_token_hash` (`resolve_receipt`) and `uq_payment_accounts_webhook_secret_hash` (the webhook lookup), both in ADR-016. A catalog test enforces the rule and its two exceptions.
- **The `WITH CHECK` of the write policies also requires that the school belongs to the organization of the row.** The scope predicate alone, in an organization-wide context, only compares the organization: a row with the organization of the caller and a school of another organization passed the policy and reached a unique index first. The subquery runs under the row level security of `schools`, so another tenant's school is simply not there.
- **`*_user_id` columns are foreign keys to `users` (global) guarded by a trigger.** `assert_active_member` raises, before the foreign key, a uniform error unless the user holds an `active` membership of the organization that covers the school of the row **and is visible in the context**. A user of another tenant, a user without membership and a made-up id fail identically. The membership of an organization-wide administrator is only visible in an organization-wide context: in a school context, name a member of that school.
- **Tenant hops.** Updating `organization_id` or `school_id` is refused by the column privileges (`apm_app`) and by `ft_05_immutable` and its siblings (the owner and the admin). Writing a row into another tenant is refused by the policies.
- **The consolidated view of a network** (`org_statement_summary`) is `SECURITY INVOKER`: a network-wide context sees every school of the organization, a school context only its own school, another organization nothing.

## Grants

`apm_app` has `SELECT` on the whole of each financial table (except one column, below), `INSERT` and `UPDATE` **by column**, and nothing else. Columns the database assigns (`id`, `reference_code`, `late_adjustment`, `approved_at`, `reopened_at`, the actor of an audit row, timestamps) are in no `INSERT` list, so the application cannot choose them. The exact lists are asserted from the catalog in `tests/financial/test_grants.py`.

| Table | `INSERT` | `UPDATE` |
| --- | --- | --- |
| `financial_transactions` | scope, `kind`, `direction`, `amount_cents`, `status`, `category_id`, the origin, `occurred_at`, `settled_at`, parent columns, `created_by_user_id` | `status`, `settled_at`, `amount_cents`, `category_id`, `occurred_at`, `updated_at` (the amount, the purpose and the date only where `ft_07_change` allows) |
| `contributions` | key, scope, `method`, `external_reference`, the five identification columns, receipt hash and expiry | the five identification columns (to erase them), `review_decision_reason`, `updated_at` |
| `expenses` | key, scope, `description`, `vendor`, `purchase_reason`, `payment_method`, `paid_by`, `submitted_by_user_id` | `description`, `vendor`, `purchase_reason`, `payment_method`, `approved_by_user_id`, `approved_amount_cents`, `decision_reason`, `correction_reason`, `updated_at` |
| `reimbursements` | key, scope, `beneficiary_user_id` | `payment_reference`, `paid_by_user_id`, `updated_at` |
| `refunds` | key, scope, `reason` | `payment_reference`, `confirmed_by_user_id`, `updated_at` |
| `expense_attachments` | everything but the id | none |
| `categories` | scope, `key`, `name`, `applies_to`, `report_group`, `requires_approval`, `is_active` | `name`, `is_active`, `updated_at` |
| `school_settings` | the business columns | the business columns |
| `payment_accounts` | scope, `provider`, `external_account_id`, `status`, `secret_ref`, `webhook_secret_hash` | `external_account_id`, `status`, `secret_ref`, `webhook_secret_hash`, `updated_at` |
| `pix_charges` | key, scope, `payment_account_id`, `provider`, `txid`, `status`, `amount_cents`, `expires_at`, `emv_payload` | `status`, `end_to_end_id`, `paid_at`, `received_amount_cents`, `divergence_reason`, `emv_payload`, `updated_at` |
| `webhook_events` | scope, `provider`, `idempotency_key`, `end_to_end_id`, `raw_payload`, `signature_valid` | `processed_at`, `processing_error`, `attempts` |
| `audit_logs` | scope, `action`, `entity_type`, `entity_id`, `entity_reference`, `before_data`, `after_data` | none |
| `monthly_closings` | scope, `period_start`, `closed_by_user_id`, `bank_balance_reported_cents` | `reopened_by_user_id`, `reopen_reason`, `report_ref` |

**`payment_accounts.webhook_secret_hash`** is written but never read by `apm_app`: the table has a column list for `SELECT`, which leaves that column out (`has_column_privilege('apm_app', 'payment_accounts', 'webhook_secret_hash', 'SELECT')` is false, and the tests try to read it by many paths: a direct `SELECT`, `RETURNING`, a filter on it, the column statistics, the functions of the schema). The ORM model loads the column `deferred`, so a normal load asks only for the columns the role may read.

Functions: `EXECUTE` goes only to `apm_app`, only for `statement_entries`, `statement_summary`, `org_statement_summary`, `statement_pending`, `closing_entries_hash`, `closing_breakdown` and `verify_closing` (plus the context functions of the tenancy core and of the authentication). Trigger functions need no `EXECUTE`. There is **no `SECURITY DEFINER` function in the financial schema**: every function of revision 0007 is `SECURITY INVOKER` with `search_path = pg_catalog`, owned by `apm_owner`. The only `SECURITY DEFINER` functions of the database are the three of ADR-016 (revision 0006, owned by `apm_definer`). No financial trigger is on `users`, `memberships` or `invitations`, so none fires inside `accept_invitation`; the only financial trigger on a tenancy table is `schools_10_settings` (AFTER INSERT on `schools`), which no definer function reaches. A trigger added on those tables later would run with the rights of `apm_definer` and its `search_path`.

## The reference code

`reference_code` (`bigint`, per school) is the number the school sees. It is assigned **only** by the trigger `ft_10_reference_code` (the application has no `INSERT` on the column, and even an admin asking for a number gets the next one): a transaction-scoped advisory lock of the school, then `max(reference_code) + 1`. It never repeats, it never skips (a rollback gives the number back), and it needs no counter table. `UNIQUE (school_id, reference_code)` is the backstop. It relies on `READ COMMITTED`, the default: under a stricter level a second writer fails with a unique violation, it never duplicates. Creating a ledger row serialises with the others of the **same school** until commit; schools do not wait for each other. Format it in the presentation (`'APM-000042'`). The audit log carries it in `entity_reference`, so a person can look up the history of "APM-000042" without knowing a uuid.

Advisory lock namespaces (first key of `pg_advisory_xact_lock(int, int)`, the second is the hash of the school): `7001` the reference code, `7002` the period of a closing (shared by a settlement, exclusive for a closing), `7003` the end-to-end ids of the Pix (a direct Pix against the charges).

## The booking of a settlement and the late adjustment

`settled_at` is the instant a movement was **booked in the cash ledger** (NULL while pending); `occurred_at` is the real date of the fact. The trigger `ft_30_settle` runs when `settled_at` is first set:

1. it takes the **shared** advisory lock of the period of the school (a month closing takes the exclusive one), so a settlement and a closing never interleave;
2. it refuses a date in the future (more than 5 minutes ahead);
3. if the local date (in the time zone of the school) is on or before the end of the **latest active closing**, it sets `settled_at = clock_timestamp()` and `late_adjustment = true`: the entry is booked in the open period and flagged (ADR-015). The real date stays in `occurred_at`.

So the application must **read `settled_at` back** after settling (`RETURNING`, or a refresh). A closed month can never receive an entry, which is what makes its snapshot reproducible forever. For an expense paid by a collaborator, the cash moves with its reimbursement, never with the expense itself (see below).

## The statement (cash basis)

Functions, all `STABLE`, `SECURITY INVOKER` (row level security applies: another school's id returns nothing):

- `statement_entries(school, from, to, p_type, p_category, p_status, p_person)`: the settled cash entries of the period, ordered by `(settled_at, reference_code)`. It carries `signed_amount_cents` (IN positive, OUT negative), `opening_balance_cents` and `running_balance_cents`, plus what a bank-like statement shows: `display_type` (INCOME, EXPENSE or REFUND), `origin_label`, `description`, `status_label` (REIMBURSED for a PAID reimbursement), `category_key`, `report_group`, `beneficiary_label`, `local_date` and `late_adjustment`. **The four filters** (type, category, status, person; the person is the origin or the beneficiary) **only choose the rows that come back: the opening and running balances are computed over all the entries**, so a filtered statement shows the balance of the account.
- `statement_summary(school, from, to)`: one row, even with no movement: time zone, `opening_balance_cents`, `contributions_in_cents` (report group CONTRIBUTIONS), `other_in_cents` (the other incoming groups), `refunds_in_cents` (confirmed returns), `total_in_cents`, `expenses_out_cents` (paid by the APM, **bank fees included**), `reimbursements_out_cents`, `total_out_cents`, `closing_balance_cents`, `pending_reimbursements_cents`, `balance_after_pending_cents`, `entries_count`;
- `org_statement_summary(organization, from, to)`: the same row for **every school** of the organization that the context can see, with the school id and name. This is the consolidated view of a network;
- `statement_pending(school)`: what is not settled, **outside the balance**, in sections: `AWAITING_APPROVAL` (a submitted expense), `AWAITING_CORRECTION` (an expense sent back), `REVIEW` (a Pix contribution with a different amount, or a direct Pix not yet confirmed), `RECEIVABLE` (a contribution waiting for payment, a return not yet confirmed), `PAYABLE` (an APM expense that is approved, a reimbursement not paid).

What counts as cash: a row with `settled_at` and, for an EXPENSE, `paid_by = 'APM'` (a bank fee is such an expense). An expense paid by a collaborator moves the cash **only through its reimbursement**, so it is never counted twice. A contribution that is PAID comes in; an APM expense that is PAID goes out; a reimbursement that is PAID goes out (for the approved amount); a refund that is CONFIRMED comes in. The period is `[from 00:00, to + 1 day 00:00)` in the time zone of the school. The **opening balance** is the signed sum of the cash entries booked before the start. Do not sum `financial_transactions` yourself.

**Two balances.** `closing_balance_cents` is what is **in cash**. `balance_after_pending_cents` is that minus the reimbursements that are still waiting to be paid (money already committed). The pending figure is the status **now**, not at the end of the period (the history of a status is not stored), so it is a snapshot: it is **not** part of the hash of a closing and `verify_closing` does not recompute it.

## The monthly closing

`INSERT INTO monthly_closings (organization_id, school_id, period_start, closed_by_user_id)`: that is all the application says (and, optionally, `bank_balance_reported_cents`, see *Reconciliation with the bank*). The trigger `monthly_closings_20_snapshot`, under the exclusive lock of the school and in a `READ COMMITTED` transaction:

1. requires the month to have **ended** in the time zone of the school, and the closings to be **sequential** (the next one starts the day after the latest active one; the first may start anywhere and carries everything before it);
2. calls `statement_summary` and **writes** the opening, the split totals (`contributions_in_cents`, `other_in_cents`, `refunds_in_cents`, `total_in_cents`, `expenses_out_cents`, `reimbursements_out_cents`, `total_out_cents`), the closing, the pending reimbursements and the closing after them, the entry count, `entries_hash` and the `breakdown`: whatever the caller passes in those columns is overwritten, so a snapshot that disagrees with the ledger cannot be written (the `CHECK`s also verify `closing = opening + in - out`, `total_in = contributions + other + refunds` and `total_out = expenses + reimbursements`).

`breakdown` (`closing_breakdown`) is a `jsonb` object `{report_group: {in, out, count, categories: {category_key: {in, out, count}}}}` of the cash entries of the period, in cents; it is the section of the monthly report that lists contributions, other income, expenses and reimbursements, returns, and the bank fees (`BANK_FEES`).

`entries_hash` is the SHA-256 (PostgreSQL's built-in `sha256`, hex) of this UTF-8 text, lines joined by LF with no trailing one:

```
<school_id>|<period_start YYYY-MM-DD>|<period_end YYYY-MM-DD>|<opening_balance_cents>
<reference_code>|<transaction_id>|<kind>|<signed_amount_cents>|<settled_at in UTC as YYYY-MM-DDTHH:MM:SS.ffffffZ>|<late_adjustment true|false>
...one line per cash entry of the period, ordered by (settled_at, reference_code)
```

`verify_closing(id)` recomputes the figures, the hash **and the breakdown** from the ledger and compares: `true` for an intact closing. It is only meaningful for an **active** closing (a reopened one is superseded). It does not look at the pending figure (a snapshot) nor at the reconciliation (`bank_balance_reported_cents`, `bank_difference_cents`). The PDF is generated from the stored snapshot, so it is reproducible.

**Reopening** sets `reopened_by_user_id` and `reopen_reason` (at least 10 characters; `reopened_at` is set by the trigger), once, and only on the **latest active** closing. Who may reopen (`organization_admin`) and the audit event are the service's rules. Closing the same month again creates a **new row**: the history is kept, and a partial unique index allows one active closing per school and month. `report_ref` is set once, after the PDF exists.

## The audit log

Every `INSERT` and `UPDATE` of the ledger, the details, `pix_charges`, `payment_accounts`, `expense_attachments`, `categories`, `school_settings` and `monthly_closings` writes an `audit_logs` row from an `AFTER` trigger (`audit_row_change`, trigger `<table>_95_audit`), in the same transaction: a rolled-back change leaves no record and the application cannot forget it. `webhook_events` is itself a log and is not audited. The service may add high-level events (`expense.approved`) with a plain `INSERT`.

**What is stored.** `entity_type` is the table, `entity_id` its key, `entity_reference` the `reference_code` of the movement (of the row itself for the ledger, of its ledger row for a detail, a charge or an attachment; NULL for a table without one), `action` is `<table>.insert` or `<table>.update`. `after_data` holds the audited columns; on `UPDATE`, `before_data` and `after_data` hold **only the keys that changed** (and nothing is written when no audited column changed), so the acceptance of a review shows `amount_cents` before and after. **Free text, personal data and secrets are never copied**: `origin_name`, `guardian_name`, `student_name`, `class_name`, `contributor_email`, `contributor_phone`, `receipt_token_hash`, `review_decision_reason`, `external_reference`, `description`, `vendor`, `purchase_reason`, `decision_reason`, `correction_reason`, `reason`, `payment_reference`, `file_name`, `storage_key`, `emv_payload`, `divergence_reason`, `external_account_id`, `secret_ref`, `webhook_secret_hash`, `report_ref` and `reopen_reason` appear only as `<column>_present: true|false`, so an anonymisation or a rotation is visible without the data. Ids, amounts, statuses and dates are kept.

**Who: the contract with the authentication layer.** The actor never comes from a column the application writes; it comes from transaction-local settings (`set_config(name, value, true)`):

| Setting | Meaning |
| --- | --- |
| `app.user_id` | the authenticated user (a uuid); it must be an **active member** of the school, or the write fails with the uniform membership error |
| `app.actor_type` | `USER`, `SYSTEM` (a job, set explicitly) or `PUBLIC` (the public flow); defaults to `USER` when `app.user_id` is set and `PUBLIC` when it is not |
| `app.request_id` | the id of the request (at most 200 characters) |
| `app.client_ip` | optional: the client address (an `inet`) |

The trigger `audit_logs_05_actor` fills `actor_user_id`, `actor_type`, `request_id`, `ip` and `occurred_at` from them (and `audit_logs` has no `INSERT` privilege on those columns), `ck_audit_logs_actor_matches_type` requires `actor_type = 'USER'` exactly when there is a user, and `audit_logs_10_member` checks the actor. Without the settings the actor is NULL and `PUBLIC`. `app.user_id`, `app.request_id` and `app.actor_type` are set by `app/db/request_context.py` (TASK-004), local to the transaction: `USER` with a session, `PUBLIC` for the login and the acceptance of an invitation (no session), `SYSTEM` only when a job sets it explicitly; `app.client_ip` is proposed here and optional (not set today).

## Trigger inventory

Within a table and a timing, triggers fire in **alphabetical order of their names**, which is why they carry a number. `tests/financial/test_triggers.py` derives this list from the catalog: a missing, disabled or undocumented trigger fails the suite, and so does a function that is not `SECURITY INVOKER` with a fixed `search_path`.

| Table | Trigger | When | Function and why |
| --- | --- | --- | --- |
| `financial_transactions` | `ft_05_immutable` | BEFORE UPDATE | `assert_immutable_columns`: id, scope, kind, direction, parent, origin, reference code and author never change |
| | `ft_06_freeze` | BEFORE UPDATE | `freeze_when_final`: a PAID, EXPIRED, CANCELLED, REJECTED or CONFIRMED row changes in no column |
| | `ft_07_change` | BEFORE UPDATE | `ft_check_change`: the edges of the state machines, the edit window, the review decision |
| | `ft_08_initial` | BEFORE INSERT | `ft_check_initial`: the initial state of each kind (REVIEW_REQUIRED for a contribution, narrowed to a direct Pix by `contributions_20_insert`; APPROVED for an expense of a category without approval) |
| | `ft_10_reference_code` | BEFORE INSERT | `ft_assign_reference_code`: the gapless per-school number (advisory lock) |
| | `ft_20_relations` | BEFORE INSERT (with a parent) | `ft_check_relations`: reimbursement and return rules; locks the parent |
| | `ft_25_member` | BEFORE INSERT | `assert_active_member('created_by_user_id', 'origin_user_id')` |
| | `ft_30_settle` | BEFORE INSERT OR UPDATE | `ft_settle`: the booking and the late adjustment |
| | `ft_90_consistency` | AFTER INSERT OR UPDATE, **deferred** | `ft_check_consistency`: the rules that look at two tables, at commit (including the author of a direct Pix and the shape of an expense without approval) |
| | `financial_transactions_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `contributions` | `contributions_05_immutable` | BEFORE UPDATE | `assert_immutable_columns` |
| | `contributions_10_anonymize` | BEFORE UPDATE | `assert_anonymize_only`: the five identification columns can only go from a value to NULL |
| | `contributions_12_set_once` | BEFORE UPDATE | `assert_set_once_columns`: `review_decision_reason` |
| | `contributions_15_review` | BEFORE UPDATE | `contributions_check_review`: the reason only while REVIEW_REQUIRED |
| | `contributions_20_insert` | BEFORE INSERT | `contributions_check_insert`: a direct Pix is distinct from the charges of the school (advisory lock 7003), is born PAID or REVIEW_REQUIRED, and only it is born REVIEW_REQUIRED |
| | `contributions_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `expenses` | `expenses_05_immutable` | BEFORE UPDATE | `assert_immutable_columns` |
| | `expenses_12_waiver` | BEFORE INSERT | `expenses_apply_approval_waiver`: in a category without approval the approved amount is the requested one and the payer is the APM |
| | `expenses_10_set_once` | BEFORE UPDATE | `assert_set_once_columns`: the decision, its time, the approved amount and its reason are written once |
| | `expenses_15_decision_time` | BEFORE INSERT OR UPDATE | `expenses_set_decision_time`: `approved_at` is the database's clock |
| | `expenses_20_editable` | BEFORE UPDATE | `expenses_edit_only_when_editable`: the texts only in DRAFT or CORRECTION_REQUESTED; the correction reason only under review |
| | `expenses_25_member` | BEFORE INSERT OR UPDATE OF the user columns | `assert_active_member` |
| | `expenses_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `reimbursements` | `reimbursements_05_immutable`, `reimbursements_10_set_once` | BEFORE UPDATE | `assert_immutable_columns`, `assert_set_once_columns` (the payment reference and who paid) |
| | `reimbursements_25_member` | BEFORE INSERT OR UPDATE | `assert_active_member` |
| | `reimbursements_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `refunds` | `refunds_05_immutable`, `refunds_10_set_once` | BEFORE UPDATE | as above (the payment reference and who confirmed) |
| | `refunds_25_member` | BEFORE INSERT OR UPDATE | `assert_active_member` |
| | `refunds_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `expense_attachments` | `expense_attachments_05_no_update` | BEFORE UPDATE | `forbid_update`: insert-only |
| | `expense_attachments_20_state` | BEFORE INSERT | `expense_attachments_check_state`: none on a final expense |
| | `expense_attachments_25_member` | BEFORE INSERT | `assert_active_member` |
| | `expense_attachments_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `categories` | `categories_05_immutable`, `categories_95_audit` | BEFORE UPDATE, AFTER | `assert_immutable_columns` (key, direction, report group and `requires_approval` never change), `audit_row_change` |
| `school_settings` | `school_settings_05_immutable`, `school_settings_10_timezone`, `school_settings_95_audit` | BEFORE UPDATE, AFTER | `assert_immutable_columns`, `school_settings_lock_timezone`, `audit_row_change` |
| `payment_accounts` | `payment_accounts_05_immutable`, `payment_accounts_95_audit` | BEFORE UPDATE, AFTER | `assert_immutable_columns` (id, scope, provider), `audit_row_change` (never the account, the reference or the hash) |
| `pix_charges` | `pix_charges_05_immutable`, `pix_charges_06_freeze`, `pix_charges_10_set_once` | BEFORE UPDATE | `assert_immutable_columns`, `freeze_when_final` (PAID, REVIEW_REQUIRED, EXPIRED, CANCELLED), `assert_set_once_columns` |
| | `pix_charges_20_contribution` | BEFORE INSERT | `pix_charges_check_contribution`: the contribution, the amount and the ACTIVE account |
| | `pix_charges_20_end_to_end_id` | BEFORE INSERT OR UPDATE OF `end_to_end_id` | `pix_charges_check_end_to_end_id`: the id is not that of a direct Pix of the school (advisory lock 7003) |
| | `pix_charges_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| `webhook_events` | `webhook_events_05_immutable`, `webhook_events_10_set_once` | BEFORE UPDATE | the raw event never changes; `processed_at` is set once |
| `audit_logs` | `audit_logs_05_actor` | BEFORE INSERT | `audit_logs_fill_actor` |
| | `audit_logs_10_member` | BEFORE INSERT | `assert_active_member('actor_user_id')` |
| | `audit_logs_90_no_update` | BEFORE UPDATE | `forbid_update` |
| `monthly_closings` | `monthly_closings_05_immutable`, `monthly_closings_10_set_once` | BEFORE UPDATE | the snapshot and the reported bank balance never change; `report_ref`, `reopened_*` are set once |
| | `monthly_closings_15_reopen` | BEFORE UPDATE | `monthly_closings_reopen`: latest active closing only |
| | `monthly_closings_20_snapshot` | BEFORE INSERT | `monthly_closings_snapshot`: computes everything |
| | `monthly_closings_25_member` | BEFORE INSERT OR UPDATE OF the user columns | `assert_active_member` |
| | `monthly_closings_95_audit` | AFTER INSERT OR UPDATE | `audit_row_change` |
| every table above | `<table>_no_delete`, `<table>_no_truncate` | BEFORE DELETE, BEFORE TRUNCATE (statement) | `forbid_delete`, `forbid_truncate` |
| `schools` | `schools_10_settings` | AFTER INSERT | `schools_create_settings`: a school is born with its settings |

Other functions: `statement_entries`, `statement_summary`, `org_statement_summary`, `statement_pending`, `closing_entries_hash`, `closing_breakdown`, `verify_closing` (callable by `apm_app`), and `app_org`, `app_school` (tenancy).

## How to create a new financial table

1. Follow the checklist of `docs/tenancy.md`, with these additions, in a new migration (numbers are asked from the Architect).
2. Columns `organization_id` and `school_id` `NOT NULL`, and the composite foreign key `(school_id, organization_id)` to `schools`. **Every reference to another tenant table is a composite foreign key that carries the organization and the school** (and the kind or the direction of a ledger row). A detail's primary key carries them too.
3. **Every unique index the application can feed carries `school_id`.** If a value must be unique across tenants, write down why it reveals nothing, as `uq_contributions_receipt_token_hash` and `uq_payment_accounts_webhook_secret_hash` do.
4. `ENABLE` and `FORCE ROW LEVEL SECURITY`; `SELECT` with the scope predicate; `INSERT` and `UPDATE` with `WITH CHECK` of **scope and "the school belongs to the organization of the row"**. No `DELETE` policy.
5. `INSERT` and `UPDATE` grants **by column**, never on ids, scope or kind; no `DELETE`. A column that must be written but never read back gets a `SELECT` column list instead of the table grant.
6. Triggers: `assert_immutable_columns` for what never changes, a freeze for final states, `assert_active_member` for every `*_user_id`, `forbid_delete` and `forbid_truncate`, the audit trigger with an allow-list that leaves out free text. Number them for the firing order.
7. No column that holds a credential: a reference (`secret_ref`) or a hash is the most the database stores.
8. A model in `app/models/financial*.py` with the same names; a line in the matrix (`tests/financial/test_isolation.py`), the grants, the immutability, the trigger inventory and the CHECK cases (the coverage tests fail until you add them), and a row in this page.
9. Downgrade that reverses it all.

## Using it from the application

- Create a ledger row and its detail row **in the same transaction**: insert the ledger row first (the foreign key of the detail needs it; `INSERT ... RETURNING id` gives you the id, which the application cannot choose), then the detail. The check that every ledger row has its detail runs at commit.
- Start a movement in an allowed initial state (a cash contribution PAID with `settled_at`; an expense DRAFT or SUBMITTED; a reimbursement PENDING; a refund REQUESTED) and walk the machine: a row that tries a missing edge is refused.
- Load a detail by `Expense.transaction_id == id` (its primary key is the three columns).
- Settle with a compare-and-swap, `UPDATE ... SET status = 'PAID', settled_at = ... WHERE id = :id AND status = :expected RETURNING settled_at`: a second confirmation matches no row. Read `settled_at` back.
- To accept a Pix payment of another amount: write `review_decision_reason`, then set `status = 'PAID'` and `amount_cents` to `received_amount_cents` in one `UPDATE`.
- Webhooks: `INSERT ... ON CONFLICT DO NOTHING` on `(school_id, provider, idempotency_key)`; a returned row count of 0 is a duplicate. Minimise the payload before storing it (below).
- Set the actor settings (`app.user_id`, `app.request_id`, `app.actor_type`) at the start of every transaction, together with the tenant context.
- Do not edit an amount of a sent expense: send it back for correction, or cancel it and create a new row. Correct a settled row with a REFUND (or by reversing the reimbursement of an expense paid by a collaborator).

## Accepted risks

- **Anyone with `DISABLE TRIGGER` can bypass the triggers**: the owner (`apm_owner`, which is `NOLOGIN`), the admin role (a superuser in development), and any superuser through `session_replication_role = replica`. `apm_app` can do none of it (a test proves it). Operationally this means: the admin credential never enters the API process (ADR-014); it is used only by migrations, the seed and a person who restores or repairs data, with the action recorded outside the database; the database log should record DDL (`log_statement = 'ddl'`); and `audit_logs` should be backed up and shipped out of the database in production (a hash chain of the audit log is not implemented). The declarative constraints, the grants and the policies do not depend on the triggers.
- **`webhook_events.raw_payload` may carry the CPF and the name of the payer.** The Pix layer (TASK-006) must **minimise it before storing**: keep only what is needed to process and reconcile (the end-to-end id, the amount, the txid, the time), never the payer's identification. The audit log never holds it.
- **Personal data in a frozen ledger.** `origin_name` of a movement that is not a contribution (a person without an account) stays in the ledger. A contribution has no `origin_name` precisely so that the payer's data (`guardian_name`, the e-mail, the phone) can be erased with `contributions_10_anonymize` after the line settles, without touching the ledger. The service must keep it that way: never copy the name of a contributor into a ledger or audit column.
- **`payment_accounts.webhook_secret_hash` is global**: a probe could learn that a hash exists. The hash is of a 128-bit random secret, so guessing one is not feasible, and `apm_app` cannot read the column; the global unique index is the price of resolving a webhook without a tenant.
- **The context and the actor settings can be forged by arbitrary SQL run as `apm_app`** (ADR-014): row level security and the actor of the audit protect against bugs of the application, not against SQL injection or remote code execution. Mitigations are the same as in `docs/tenancy.md`.
- **A transaction that holds an advisory lock can be held by injected SQL** (the locks of a school are held to the end of the transaction): a denial of service, accepted with the rest of the SQL-injection model.
- **`reference_code` and the closing need `READ COMMITTED`.** Under `REPEATABLE READ` or `SERIALIZABLE` a reference-code collision fails with a unique violation (it never duplicates) and a closing is refused.
- **The pending reimbursements and the balance after them are a snapshot of now**, not of the end of the period, and are outside the hash of a closing and `verify_closing` (the history of a status is not stored). A reader of an old closing sees the figure as it was when it closed.
- **The backfill of `school_settings` for schools that already exist runs as the admin**: it works with a superuser (development). With a non-superuser admin, row level security hides the schools and nothing is inserted, so the first deployment must not have schools created before this migration (there is no flow that creates one without the trigger).
- **A late Pix payment** (the charge is confirmed after the contribution EXPIRED or was CANCELLED) is not forced into a state by the database: the application must record it (for example by refunding), because a closed row cannot change.
- **Who RECORDS a bank fee decides its amount.** The exemption is narrow in the database: a school has one category without approval (`bank_fees`, enforced by a CHECK that not even `apm_app` can get around, by insert or by update), an expense there can only be born APPROVED, from the bank and paid by the APM, and it can never reach a collaborator's expense, a draft or a request under review. What remains is that whoever may record an expense of origin BANK in that category (a permission of the service layer, which should give it to the treasury only) writes the amount, and nobody approves it. The mitigations are the audit (every fee and its actor are recorded by the database), the report group `BANK_FEES` that shows them apart in every closing, and the reconciliation with the bank balance, where a fee that never left the account shows as a difference.
- **A direct Pix is declared, not verified.** The database keeps the id unique and apart from the charges, but it cannot know the Pix exists on the bank: the treasury types it (or, later, the import of the statement from the provider does). The check is the reconciliation, whose bank balance is also typed by a person. It is recorded, audited and outside the hash of the closing, so it is a signal and not a seal.
- **A direct Pix without an author cannot exist** (it would be stuck: `created_by_user_id` never changes), so an import from the bank must act as a user of the school (the treasurer who uploads the statement), not as a bare `SYSTEM`.
- **The advisory locks leak timing, not data.** Taking the lock `7003` (and `7001`) for the school id of a row the context cannot see waits if another transaction holds it, before the policy refuses the row: an observer could learn that a transaction of that school is running, never what it holds.
- **Roles.** A reader sees the data of every school of an organization-wide context. Which role may see personal data of the families, approve, pay or close is the permission map of the code (ADR-016), not the database.

## Tests

`apps/api/tests/financial/`:

| File | Criterion |
| --- | --- |
| `test_migration_cycle.py` | F1: upgrade and downgrade with data, repeated, backfill |
| `test_isolation.py`, `test_references.py` | F2: the matrix (13 tables x operations x 5 contexts x 2 roles), tenant hops, no foreign key or unique index that reveals another school |
| `test_immutability.py`, `test_grants.py` | F3 and F8: every protected column by every path, no delete, the exact grants |
| `test_statement.py`, `test_org_statement.py` | F4 and F12: hand-worked scenarios, the closing and its hash recomputed in Python, the balance property over random ledgers; the consolidated view of a network |
| `test_concurrency.py` | F5: real threads: one settlement, no repeated or skipped reference code, settlement against closing |
| `test_checks.py` | F6: every CHECK is exercised, and a coverage test |
| `test_state_machines.py`, `test_partial_approval.py` | F9 and F10: every valid and invalid edge, the edit window, partial approval, returns |
| `test_payment_accounts.py` | F11: no credential column, one ACTIVE account per school, the column the application cannot read |
| `test_product_scenarios.py` | F13: the dashboard and the example statement of the product requirements, worked out by hand |
| `test_triggers.py`, `test_audit.py` | the trigger inventory and the rules; the audit trigger |
| `test_performance.py` | 100 thousand movements, plans read with `auto_explain` |
| `test_direct_pix.py` | F14: a Pix paid straight to the key (no charge, born PAID or in review, decided with a reason and the same amount), never two contributions with a charge, even at the same moment; as `apm_app` under row level security (own school works, another school is refused with the same error whether or not its id exists) |
| `test_bank_fees.py` | F15: a fee is recorded APPROVED with no approver, settled by whoever records it, audited, in its own report group, and nothing else can use the exemption |
| `test_reconciliation.py` | F16: the difference to the bank balance is computed, visible, does not stop the closing and stays outside the hash |
| `test_models.py`, `test_seed_financial.py`, `test_docs.py` | the models match the database; the seed; this page matches the catalog |

The 100-thousand-row test and the concurrency tests take a few seconds; run a file with `docker compose run --rm tools pytest tests/financial/test_x.py`. `test_docs.py` needs this page, which is not inside the API image: it is skipped (with a message) when `docs/` is not mounted.
