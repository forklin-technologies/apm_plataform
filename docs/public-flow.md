# The public contribution flow (TASK-006, ADR-010, ADR-011, ADR-018)

A family contributes without an account: the page of the school, a Pix charge, the confirmation by
the provider, and a receipt. This page explains the routes, the order of events and how to try it in
development. The database side (the three `SECURITY DEFINER` functions, the idempotency key,
`secret_ref`) is in [financial-model.md](financial-model.md#the-public-flow-0008-adr-018).

## The rules that shape it

- **The frontend never decides that something was paid.** Only the webhook of the provider, after
  the API asked the provider again, settles a contribution.
- **The school comes from the slug of the URL**, resolved by `resolve_school_public`. From then on
  the session is bound to that school and everything runs as `apm_app` under row level security with
  the actor `PUBLIC` (`SYSTEM` for the webhook).
- **One answer for everything unknown.** A school, a token, a receipt that does not exist, expired,
  belongs to another school or is malformed: the same `404 not_found`.
- **The money is the school's.** The API only creates a charge on the ACTIVE payment account of the
  school and records what the provider says; it never moves money.

## Routes (all under `/api/v1`)

| Route | What it does |
| --- | --- |
| `GET /public/schools/{slug}` | The page: name, colors, suggested amounts, limits, which identification fields are `REQUIRED`, `OPTIONAL` or `HIDDEN`. No internal id. |
| `POST /public/schools/{slug}/contributions` | Header `Idempotency-Key` (a uuid per attempt). Body: `amount_cents` and the identification fields. Revalidates the amount (minimum, maximum, only the suggested ones when the school says so) and the fields, **stores only the fields the school asks for**, creates the contribution (`PENDING_PAYMENT`) and its first charge. Answers `201` with the opaque `token` and the state. The same key answers with the same token and contribution; the same key with another amount is `409 idempotency_key_reused`. At most 30 new contributions per client address in 15 minutes (`429`). |
| `GET /public/schools/{slug}/contributions/{token}/charge` | The state of the contribution and of its latest charge (the page polls it every 2 seconds, with backoff). An old charge is marked `EXPIRED` here. |
| `POST /public/schools/{slug}/contributions/{token}/charges` | A new charge when the previous one expired. While one is `PENDING` it answers with that one (no duplicate). `409 contribution_closed` once the contribution is no longer waiting. |
| `GET /public/schools/{slug}/contributions/{token}/receipt` | `200` only when `PAID`, `409 payment_not_confirmed` otherwise. |
| `POST /webhooks/pix/sandbox` (and `/bb`, not available until M2) | The notification of the provider. Header `X-Webhook-Secret` (never in the URL; only its SHA-256 is compared with the one of the account). Body `{event_id, txid}`. Answers `200 {"status": ...}`: `PAID`, `REVIEW_REQUIRED`, `NOT_CONFIRMED`, `IGNORED` or `DUPLICATE`. Wrong secret: `401`, counted against the address. |
| `POST /schools/{school_id}/contributions` | The treasury records a contribution in cash, transfer or other (`contributions:record_cash`). Born `PAID`, records who entered it. |
| `POST /dev/sandbox/pix/{txid}/pay` | Development and tests only (`404` otherwise): makes the fake bank pay a charge, with another amount if the body has `amount_cents`, and runs the same code as the webhook. |

Every public response carries `Cache-Control: private, no-store` and `Referrer-Policy: no-referrer`.
The token is a secret of the family (256 bits, derived from the Idempotency-Key so that a replay
answers with the same one); only its SHA-256 is stored, and the receipt link is valid for 30 days.

## What the webhook does, in order

1. Counts a failure against the address when the secret is unknown, and blocks after repeated ones.
2. `resolve_webhook_target` turns the hash of the secret into the school and the payment account.
3. Records the event once (`webhook_events`, unique per school, provider and event id): a delivery
   already seen is `DUPLICATE` and changes nothing.
4. Finds the charge by `txid`; a charge that is not `PENDING` is `IGNORED`.
5. **Asks the provider again.** If it does not confirm a payment, the event is `NOT_CONFIRMED` and
   nothing changes.
6. Same amount: the charge and the contribution become `PAID` (the contribution is booked in the cash
   with the time the provider gave). Different amount: both become `REVIEW_REQUIRED`, outside the
   balance, for the management to decide.

## Trying it in development

The sandbox provider is a fake bank in the memory of the API process (a restart forgets its
charges). With the stack running (`docker compose up -d --wait`):

1. `GET /api/v1/public/schools/demo-aurora`, then `POST .../contributions` with an
   `Idempotency-Key` (Swagger UI at `/api/docs` sends the right `Origin`).
2. The `emv_payload` of the charge is `PIX-SANDBOX:<txid>:<amount>`: take the `txid`.
3. `POST /api/v1/dev/sandbox/pix/<txid>/pay` (add `{"amount_cents": 99900}` to see the review of a
   different amount), then `GET .../charge` and `GET .../receipt` with the token.

## Not here yet

The real provider (Banco do Brasil, mTLS, the credentials behind `secret_ref`), the job that expires
old contributions (the API already marks an expired charge when it is read), and the screens: the
site still uses its prototype data for this flow.
