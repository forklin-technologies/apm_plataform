# Expenses, attachments, approval and reimbursement

Decision records: ADR-017 (HTTP contract), ADR-015 (the ledger), ADR-016 (permissions). Code: `apps/api/app/expenses/` (router, service, repository, schemas) and `apps/api/app/storage/` (where the files live). The ledger rules are in [financial-model.md](financial-model.md) and are enforced by the database; this page is what the API adds on top: **who** may do what, the contract, and the files.

A person writes an expense (a DRAFT), attaches the receipt, sends it; someone else approves it, in full or (for an expense a collaborator paid) for less; then the treasury **records** the payment it already made in the bank. The platform moves no money. A return (`REFUND`) is not here (M1.5).

## Routes

All under `/api/v1/schools/{school_id}`. `school_id` is **checked against the session**: a school outside the session's context answers the same `404 not_found` as one that does not exist (an organization-wide membership covers the schools of its organization and no other). The tenant never comes from the body, a header or the query. Every route declares one permission (`x-permission` in the OpenAPI document; `x-permission-any` lists the alternatives of the routes open to more than one). State changes also need the Origin and the CSRF token ([auth.md](auth.md)).

| Method and path | Permission | What it does |
| --- | --- | --- |
| `GET /expense-categories` | `expenses:submit`, `expenses:read_own` or `expenses:read_all` | The categories for the form: money out, active, that need approval (no bank fees). `{items: [{id, key, name}]}` |
| `POST /expenses` | `expenses:submit` | Creates a **DRAFT**. The caller is its author |
| `GET /expenses` | `expenses:read_own` or `expenses:read_all` | `{items, next_cursor}`. With `read_all` every expense of the school, otherwise only the caller's. `?status=`, `?period=YYYY-MM` (the month of the date of the expense, in the school's time zone), `?cursor=`, `?limit=` (1 to 100, default 50). Newest first (by reference code) |
| `GET /expenses/{id}` | same | The expense with its attachments and, once paid, its reimbursement |
| `PATCH /expenses/{id}` | `expenses:submit` | Edits it. Only fields present change. Its author only, only in DRAFT or CORRECTION_REQUESTED. `paid_by` cannot change |
| `POST /expenses/{id}/attachments` | `expenses:submit` | Multipart: `file` and optional `kind` (`INVOICE`, `PAYMENT_PROOF`, `OTHER`; default `OTHER`). Author only, and only while the expense is a DRAFT or CORRECTION_REQUESTED (`409 attachments_closed` otherwise) |
| `GET /expenses/{id}/attachments/{attachment_id}` | `expenses:read_own` or `expenses:read_all` | Downloads the file, for whoever can see the expense |
| `POST /expenses/{id}/submit` | `expenses:submit` | DRAFT or CORRECTION_REQUESTED to SUBMITTED. Author only. Needs an attachment, `purchase_reason` and `payment_method` |
| `POST /expenses/{id}/approve` | `expenses:approve` | SUBMITTED to APPROVED. Body optional: `approved_amount_cents`, `reason` |
| `POST /expenses/{id}/reject` | `expenses:approve` | SUBMITTED to REJECTED (final). Body: `reason` (3 to 500 characters) |
| `POST /expenses/{id}/request-correction` | `expenses:approve` | SUBMITTED to CORRECTION_REQUESTED. Body: `reason` (3 to 500) |
| `POST /expenses/{id}/reimburse` | `reimbursements:register` | APPROVED, paid by a collaborator: creates the REIMBURSEMENT for the approved amount, records `payment_reference` and who paid, and the expense goes to PAID. Body: `payment_reference` |
| `POST /expenses/{id}/pay` | `reimbursements:register` | APPROVED, paid by the APM: records the payment, the expense goes to PAID. Not by the author (`403 self_payment_forbidden`) |
| `POST /expenses/{id}/cancel` | `expenses:submit` or `expenses:approve` | CANCELLED (final). The author, a DRAFT or CORRECTION_REQUESTED; an approver, an APPROVED one |

Every action answers `200` with the full expense. Money is integer cents (`_cents`), instants are ISO 8601, the states are the database's (`DRAFT`, `SUBMITTED`, ...), with no translation. `occurred_at` is an instant with its UTC offset **or** a plain day, which means noon of that day in the school's time zone.

## States and who moves them

```
DRAFT --submit(author)--> SUBMITTED --approve(approver, never the author)--> APPROVED --reimburse | pay--> PAID
  |                          |  \--reject--> REJECTED                              \--cancel(approver)--> CANCELLED
  |                          \--request-correction--> CORRECTION_REQUESTED --edit, submit(author)--> SUBMITTED
  \--cancel(author)--> CANCELLED                                             \--cancel(author)--> CANCELLED
```

A SUBMITTED expense is **not** cancelled: it is decided or sent back. Nothing is ever deleted.

## The rules the service adds

- **The author never decides on their own expense**: `approve`, `reject` and `request-correction` answer `403 self_approval_forbidden` to the author (the database also refuses an approver equal to the submitter). Nor does the person to be reimbursed register their own reimbursement (`403 self_reimbursement_forbidden`), nor the author the payment of their own expense (`403 self_payment_forbidden`).
- **Only the author** edits, attaches and sends (`403 author_only` for someone else who can see the expense; a colleague who reads only their own gets the `404`).
- **Reading**: `expenses:read_all` (treasurer, administrators) sees every expense of the school, everyone else only their own; somebody else's is the same `404` as one that never existed.
- **Partial approval**: `approved_amount_cents` is omitted (the requested amount), at most the requested amount (`422 validation_error`, `above_requested`), and lower only when a collaborator paid (`422 partial_approval_not_allowed`). The reimbursement is **exactly** the approved amount; the request stays in `amount_cents`.
- **Sending** needs one attachment, a purchase reason and a way it was paid (`422 expense_incomplete`, with the missing fields).
- **A category** for an expense is money out, active and needing approval; any other (another school's, a revenue, the bank fees, an unknown id) is `422 validation_error` with `errors: [{field: category_id, code: not_available}]`.
- **The origin** of the ledger line (who spent it) comes from the author's role: staff `TEACHER`, administrators `DIRECTOR`, treasurer `MANAGEMENT`; the reimbursement goes to `teacher_reimbursement` or, for a director, `director_reimbursement`.
- **Races**: every action locks the row and moves the state with a compare-and-swap, so two approvers at the same moment: one `200`, one `409`.

## Errors

`application/problem+json` as in [auth.md](auth.md#errors): `type`, `title`, `status`, `code`, `request_id`, and `errors[{field, code}]` for a field. The text is fixed; nothing of the request or of the database is in it.

| Status | `code` | When |
| --- | --- | --- |
| 403 | `permission_denied` | the role does not hold the permission |
| 403 | `author_only`, `self_approval_forbidden`, `self_reimbursement_forbidden`, `self_payment_forbidden` | the rules above |
| 404 | `not_found` | unknown, another school or organization, someone else's (for who reads only their own), a file that is not of that expense |
| 409 | `invalid_state` | the action does not apply in the current state (`detail` names it) |
| 409 | `attachments_closed`, `attachment_limit`, `attachment_duplicate` | attachments (`attachments_closed`: the expense was already sent; `detail` names its state) |
| 409 | `reimbursement_not_applicable`, `payment_by_reimbursement`, `reimbursement_category_missing`, `reimbursement_exists` | payment |
| 409 | `conflict`, `state_conflict`, `retry` | the database refused and none of the above says why (a race) |
| 411, 413, 415 | `length_required`, `payload_too_large`, `attachment_type_not_allowed` | the upload |
| 422 | `validation_error` | the body or the query (`errors[]`) |
| 422 | `expense_incomplete`, `partial_approval_not_allowed`, `rule_violation`, `reference_invalid`, `value_out_of_range` | rules |
| 500 | `attachment_unavailable` | the row exists and the file is not in the store |

The service checks every rule it knows before writing; `app/expenses/db_errors.py` is the net under it: a violation the database raises (a constraint, a trigger, a deadlock) becomes a `409` or `422` with a `code`, by constraint name or SQLSTATE, and never quotes the database.

## Attachments and the store

- **Upload**: `multipart/form-data` with `file` (and `kind`). The request must declare its size (`Content-Length`, ASCII digits, else `411`); it is refused with `413` before it is read when it is bigger than the limit plus 64 KiB of envelope, and again while the file is streamed. The form is read **after** the permission and the school were checked.
- **Limit**: `ATTACHMENT_MAX_BYTES` (default 10 MiB, at most 20 MiB: what the database accepts). At most 10 attachments per expense, and the same file (same `sha256`) only once per expense.
- **Type**: from the first bytes, never from the name or the `Content-Type` the client sent: PNG, JPEG, WebP and PDF. Anything else, an SVG or an HTML included, is `415`. The type stored (and served) is the recognised one.
- **Hash and name**: `sha256` of the bytes, computed while streaming. The name is only a label: no path, no control characters, at most 200 characters. The store key is `<organization>/<school>/<expense>/<random>`, made by the server; nothing the client sent is part of it.
- **Order**: the bytes are stored first, then the row is written and committed. If writing the row fails the file is removed (and a failing removal is logged, never allowed to replace the error the caller sees). If the **commit** fails the file is kept: the commit may have reached the server and only the answer got lost, and a row without its file is worse than a file without a row. A crash or a refused commit leaves an orphan file, never a row without a file.
- **Download**: `Content-Disposition: attachment`, the recognised `Content-Type`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`, `Cache-Control: private, no-store`. Only whoever can see the expense; any other (and a file of another expense) is `404`.
- **Where**: `app/storage/` is an interface (`AttachmentStore`: `put`, `open`, `exists`, `delete`) and one implementation, `LocalDiskStore`: one file per key under `ATTACHMENTS_DIR` (opened only when the first chunk is read, so a download nobody reads holds no descriptor) (`/attachments`, a docker volume in compose), files `0600`, directories `0700`, written once (`O_EXCL`), keys validated segment by segment. No cloud. A new backend is a new class with the same four methods, returned by `get_attachment_store`.

### Adding a type of attachment

Add an `AttachmentType` to `ALLOWED_TYPES` in `app/expenses/attachment_types.py`: the media type, the extension (for a file with no usable name) and a function that recognises the first bytes of the file. The upload, the database (`content_type` is any `type/subtype`) and the download read that table; nothing else changes. Add a sample to the test of `sniff`. Do not add a type a browser would run (HTML, SVG, scripts).

## Audit

The actor is never a column the service writes. The database takes it from the settings of the transaction (`app.user_id`, `app.actor_type`, `app.request_id`, set for every request by the authentication), and the triggers record every row change (`financial_transactions.update`, `expenses.update`, ...). The service adds one high-level event per action (`expense.created`, `.updated`, `.submitted`, `.approved`, `.rejected`, `.correction_requested`, `.cancelled`, `.reimbursed`, `.paid`, `.attachment_added`) with ids, amounts and states only, never free text, and the reference code, so the history of "APM-000042" reads in one place.

## Settings

| Variable | Meaning |
| --- | --- |
| `ATTACHMENTS_DIR` | where the files go (default `/attachments`; a docker volume in compose) |
| `ATTACHMENT_MAX_BYTES` | the largest file (default 10485760; at most 20971520) |

The reverse proxy must still set its own body limit above the file limit (see the notes in [auth.md](auth.md#known-limits-and-operational-notes)).

## Not done, on purpose

- Returns (`REFUND`): M1.5. Bank fees and Pix direct: other tasks.
- The **name** of an author or approver: the routes return user ids only. The application role can read the users of its own school (policy `users_select`; the statement already shows a name that way), so adding `submitted_by_name` to the list and the detail is a join away, but it is a contract change for the web to ask for.
- Rate limit of uploads, scanning of files for malware, an expiry of the files of a cancelled expense, a job that removes orphan files, a copy of the volume.
- `Idempotency-Key` (ADR-017 asks it for **public** money): a person who sends the same expense twice gets two drafts; every action is safe to repeat (a second one is `409 invalid_state`).
