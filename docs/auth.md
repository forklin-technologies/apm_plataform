# Authentication, sessions, invitations and permissions

Decision records: ADR-016 and its revision (`architectural-decisions`), ADR-014 (isolation), ADR-009 (same origin behind a proxy, no CORS). Code: `apps/api/app/auth/`, `apps/api/app/routers/v1/`, `apps/api/migrations/versions/0006_auth_sessions.py`. This page explains the flows, the cookies, the CSRF defence, what each `SECURITY DEFINER` function does and why, the threats considered, the risks accepted on purpose, and how to add a route.

[docs/tenancy.md](tenancy.md) explains the tenant, the row level security and the roles. Read it first; this page builds on it.

## What this adds, in one paragraph

A person logs in with an e-mail and a password and gets a **server-side session**. The browser holds an opaque token in an `httpOnly` cookie; the database holds only its SHA-256. The session points at one **membership** (organization or school plus a role). On **every** request the API re-reads that membership and builds the `TenantContext` from it, so the tenant is never taken from a header, a query string or a body. Accounts are created only through **invitations**. A closed list of three narrow `SECURITY DEFINER` functions covers the three reads that cannot happen inside a tenant context.

## Endpoints

All under `/api/v1`, JSON in and out, errors as `application/problem+json` (see [Errors](#errors)). The OpenAPI document (`/api/openapi.json`) is the contract; the web client is generated from it.

| Method and path | Access | What it does |
| --- | --- | --- |
| `POST /auth/login` | public | `{email, password}`. Starts a session; answers with the user, the active membership (if any), the list of memberships, the CSRF token and the expiry. |
| `POST /auth/logout` | public, session optional | Always `204` and clears the cookies; revokes the session when a valid one is presented. |
| `GET /auth/me` | any session | The same answer as login, for the current session. Used by the page on load. |
| `POST /auth/context` | any session | `{membership_id}`. Switches the active membership among the user's **own** active ones; issues a new token. |
| `POST /auth/password` | any session | `{current_password, new_password}`. Changes the password, revokes **every** session of the user, starts a new one, writes a notice to the outbox. |
| `POST /invitations` | `invitations:create` | `{email, role, school_id?}`. Creates an invitation for the ACTIVE organization; the token goes by e-mail, never in the response. |
| `POST /invitations/accept` | public, session optional | `{token, full_name?, password?}`. The token is the credential. |
| `GET /health`, `GET /health/ready` | public | Probes. |

The active membership in a response carries the **permissions** of its role, so the page can hide what the person cannot do. The server never trusts that list: it checks again on every request.

## The flows

### Invitation, then acceptance

1. An administrator calls `POST /invitations`. The organization is the one of the **active membership**. An `organization_admin` may invite any role; a `school_admin` only into their school and never an `organization_admin` (`INVITABLE_ROLES` in `app/auth/permissions.py`). A school-scoped inviter cannot name another school, and an `organization_admin` invitation cannot carry a school.
2. The API stores `SHA-256(token)` (the token has 256 bits and is shown once), valid for **72 hours**, single use. Only one live invitation exists per (organization, e-mail, school): an expired one is revoked to free the slot, a live one answers `409 invitation_pending`, an existing member answers `409 already_member`.
3. The link (`PUBLIC_BASE_URL/accept-invitation?token=...`) is written to the **outbox** (see [E-mail](#e-mail-the-outbox)).
4. The invitee calls `POST /invitations/accept`.
   - **New person** (no account with that e-mail): sends `token`, `full_name`, `password`. The function creates the user and the membership and consumes the invitation in one transaction. There is **no automatic login**; the person logs in next.
   - **Existing account**: must be **logged in** as that account and sends only `{token}`. The token alone never lets anyone act as an existing account (anti-takeover). Without a session, **or logged in as a different account than the invited e-mail's**, the answer is `409 account_exists_login_required` (not `400`).
   - Every other failure (unknown, malformed, already used, revoked, expired, inactive account, already a member) is the **same** `400 invitation_invalid`: no oracle on which tokens exist. (The one exception is the `409` above, which only says that an account exists for the e-mail the token is for, to someone who already holds the token.)

### Login, `me`, switching context

1. `POST /auth/login`: Origin check, then the rate limit (before any hashing), then `find_login_identity`, then **one** Argon2id verification (also when the e-mail is unknown, inactive or has no hash), then a **new** session. A token presented by the client is revoked, never adopted (fixation). With exactly one active membership it becomes the active one; with none or several the session starts **without a context** and the person picks one.
2. Routes that need a role answer `409 context_required` until a membership is active. `GET /auth/me` and `POST /auth/context` work without one.
3. `POST /auth/context` accepts only the user's own active memberships. An unknown id and somebody else's get the **same** `403 context_not_allowed`. It creates a new session (new token) and revokes the old one with reason `rotated`; the **absolute expiry is kept**, so switching never extends a login.

### Password change and logout

- `POST /auth/password` checks the current password (rate limited, kind `password`), requires 12 to 128 characters and a value different from the current one, updates the hash, revokes **all** the user's sessions (reason `password_changed`), issues a fresh session in the same response and writes a notice to the outbox (no password in it). The audit event for this belongs to the financial task's audit log; the hook is the line that sends the notice.
- `POST /auth/logout` is idempotent. With a valid session it requires the CSRF token (a foreign page must not be able to log the user out) and revokes the session (reason `logout`).

## Sessions

| | |
| --- | --- |
| Token | `secrets.token_urlsafe(32)`: 256 bits, 43 characters of the URL-safe alphabet. Anything that does not look like one is rejected before the database is touched. |
| In the database | `sessions.id = SHA-256(token)`. Knowing it authenticates nobody (reading it; **writing** a session row is another matter, see R7). |
| Idle expiry | 30 minutes without a request (`last_seen_at`, written at most once a minute). |
| Absolute expiry | 12 hours from login; context switches keep it. |
| Rotation | on login and on context switch (new token, old one revoked). |
| Revocation | logout, password change, membership no longer active, user inactive. `revoked_reason` is one of `logout`, `rotated`, `password_changed`, `membership_inactive`, `user_inactive`. |
| Stored besides | the IP (`inet`) and a SHA-256 of the user agent, for forensics only; they do not bind the session. |
| Integrity | the composite foreign key `(membership_id, user_id) -> memberships (id, user_id)` makes a session pointing at **another user's** membership impossible. `membership_id` NULL means "no context chosen yet" (`MATCH SIMPLE`). |

### What every authenticated request does

`app/auth/deps.py`, in this order, inside **one** database session:

1. cookie, check its shape, `SHA-256` it, set `app.session_id`;
2. read the session row (not revoked, not past its absolute end, not idle) — row level security shows it only because `app.session_id` names it;
3. set `app.user_id` and the actor `USER`;
4. for methods that change state, verify the CSRF token (before anything else is trusted);
5. the user must still be active (else the session is revoked, `401 session_revoked`);
6. read the user's active memberships (`list_memberships_for_user`); the session's membership must be one of them, otherwise the session is revoked **in that same request** (`401 session_revoked`). This is what drops access the moment an administrator suspends or revokes a membership;
7. bind the tenant context (`TenantContext(organization_id, school_id)`) of THAT membership to the database session. The role comes from the same row.

Nothing in the request can change step 7: there is no code that reads a tenant from a header, a query or a body, and a test forges `X-Organization-Id` and similar names to prove it.

## Cookies

| | `COOKIE_SECURE=true` (production, default) | `COOKIE_SECURE=false` (development and test only) |
| --- | --- | --- |
| Session cookie | `__Host-apm_session` | `apm_session` |
| CSRF cookie | `__Host-apm_csrf` | `apm_csrf` |
| Attributes | `Path=/`, no `Domain`, `Secure`, `SameSite=Lax`, `Max-Age` = what is left of the session | the same, without `Secure` |
| Session cookie | `HttpOnly` | `HttpOnly` |
| CSRF cookie | readable by the page (it must send it back in a header) | the same |

Responses that set or clear them carry `Cache-Control: no-store`. The prefix `__Host-` makes the browser refuse a cookie that is not `Secure`, has a `Domain` or a different `Path`, which stops a sibling subdomain from planting or shadowing it.

Why the switch exists: the product is previewed in WebKit (Safari, the Maestri portal), which does **not** accept a `Secure` cookie over `http://localhost`. `COOKIE_SECURE=false` therefore removes both `Secure` and the prefix, and the API **refuses to start** with `ENV=production` unless it is `true` (tests cover both modes).

## CSRF

Two layers, both on every method that changes state (`POST`, `PUT`, `PATCH`, `DELETE`):

1. **Origin check** (`OriginCheckMiddleware`, before routing). The request's `Origin` header, or its `Referer` when there is no `Origin`, must be one of the site's own origins (`PUBLIC_ORIGINS`; in development, when empty, the local dev origins; in production it is required and must be `https`). A missing header, `Origin: null`, another scheme, port or host, and suffix or path tricks are all refused with `403 origin_not_allowed`, **before** anything else, and set no cookie. It applies to login and invitation acceptance too, where there is no session to bind a token to (login CSRF). `ORIGIN_EXEMPT_PATHS` lists only the two webhooks of the Pix provider (`/api/v1/webhooks/pix/sandbox` and `/bb`): a server calls them, not a page, and they authenticate by their own secret in `X-Webhook-Secret`.
2. **Double submit bound to the session.** The CSRF token is `HMAC(key derived from AUTH_SECRET for "csrf", session id)`. The page reads it from the readable cookie (or from `csrf_token` in the login, `me` and `context` answers) and sends it in `X-CSRF-Token`. The request must carry the cookie **and** the header, equal to each other **and** equal to the value derived from the session in use. A cookie planted by an attacker does not pass, because forging the value takes the secret. A failure is `403 csrf_failed`.

`SameSite=Lax` is the third, browser-side layer. Reading (`GET`) is never subject to the CSRF token or the Origin check.

## Passwords

- Argon2id (`argon2-cffi`) with the OWASP minimum parameters: 19 MiB of memory, 2 iterations, 1 lane. A hash made with other parameters is rewritten at the next successful login.
- 12 to 128 characters (the upper bound so a huge body cannot burn CPU), not blank. Errors are generic and carry a fixed code (`too_short`, `too_long`, `blank`, `same_as_current`).
- **Constant work**: `check_password` always performs exactly one verification. When there is nothing to compare (unknown e-mail, inactive user, no password set) it verifies against a dummy hash of the same cost, so neither the answer nor the time tells those cases from a wrong password. The test does not measure clocks: it spies on the verification function and asserts it ran in every case.
- Nothing logs, formats or raises with a password or a hash.

## Rate limiting and blocking (no Redis)

Counters are rows in `login_attempts`, which stores only HMACs (of the e-mail or user id, and of the IP), never the values. A transaction-level advisory lock on the (subject, IP) pair serialises simultaneous attempts. Blocking is decided **before** any password is hashed and from the submitted value only, so it is identical whether the account exists or not.

| Kind | Keyed by | Limits |
| --- | --- | --- |
| `login` | (e-mail, IP) | 5 failures free in 15 minutes, then a block of 30 s that doubles with each further failure up to 15 minutes, counted from the last failure |
| `login` | IP | 20 failures in 15 minutes block that IP for the rest of the window |
| `login` | e-mail, any IP | 30 failures in an hour block that e-mail for the rest of it |
| `password` | (user id, IP) and the same two aggregates | as above |
| `invitation` | IP alone | as above: guessing tokens must not be able to lock other people out |

A success ends the (subject, IP) streak. The answer is `429 rate_limited` with `Retry-After`. Old rows are purged opportunistically (5% of the writes), and the policy lets the application delete only rows older than 24 hours (see [the policies](#row-level-security-of-the-new-tables)).

### The client IP

The IP is `request.client`. uvicorn replaces it with `X-Forwarded-For` **only** for peers listed in `FORWARDED_ALLOW_IPS` (the images run `--proxy-headers`; compose passes `TRUSTED_PROXY_IPS`, default `127.0.0.1`). Set it to the address of the reverse proxy and nothing else. Without a proxy in front, leave the default: forwarded headers from any other peer are ignored, so a client cannot choose its own IP. If it is set wrong behind a proxy, every client shares the proxy's address and therefore one block (accepted risk R5).

## The `SECURITY DEFINER` functions

ADR-016 allows reading **without a tenant context** only through a **closed list** of narrow functions. This task owns three; the others in the ADR (public school, webhook target, receipt) belong to the financial tasks. A test enumerates every function in the schema (signature, owner, `prosecdef`, `proconfig`, ACL) and every permissive policy, and fails on anything not on the list: a new function needs a new ADR.

Common to all three: owner `apm_definer`; `SECURITY DEFINER`; `search_path = pg_catalog`; static SQL with every object qualified (`public.users`); `REVOKE ALL FROM PUBLIC`; `EXECUTE` only for `apm_app`; the arguments are data, never SQL.

### The role `apm_definer`

`NOLOGIN`, `NOSUPERUSER`, `NOBYPASSRLS`, `NOCREATEROLE`; it owns **only** these three functions, and `apm_app` is not a member of it (it cannot `SET ROLE` to it). It has privileges **per column**, only for what the functions read and write, and **explicit policies** `TO apm_definer`, each as narrow as the function allows.

Why a role of its own: with `FORCE ROW LEVEL SECURITY` the table owner is bound by the policies too, so a function owned by `apm_owner` would read nothing. Why not `BYPASSRLS`: creating such a role needs a superuser (the production administrator is not one), and it would ignore row level security on any table the role ever touched. The only permissive policies in the system that are not tenant-scoped are the ones `TO apm_definer` below.

### `find_login_identity(p_email text)`

Returns `(user_id, password_hash, is_active)` for **one** e-mail (case-insensitive), or no row. **Why it exists**: logging in happens before any tenant is known, and `users` is invisible without one. **Reads**: `users(id, email, password_hash, is_active)`. It hands the hash to `apm_app` (accepted risk R1).

### `list_memberships_for_user(p_user_id uuid)`

Returns the user's **active** memberships with the organization and school names and slugs, ordered. **Why**: the memberships of a user span organizations, so no single tenant context can see them all; this is also what the page uses to offer a choice. The `user_id` comes from the validated session, never from the client. **Reads**: `memberships`, `organizations(id, name, slug)`, `schools(id, organization_id, name, slug)`, only rows tied to a membership.

### `accept_invitation(p_token_hash bytea, p_full_name text, p_password_hash text, p_existing_user_id uuid)`

Returns one row `(outcome, user_id, membership_id, organization_id, school_id, role)`; `outcome` is `accepted_new_user`, `accepted_existing_user`, `login_required` or `invalid`. **Why**: the invitee has no membership yet, so there is no tenant to act inside, and the application role must not insert memberships directly (ADR-016: membership creation only by invitation). What it guarantees:

- the invitation row is locked (`FOR UPDATE`) and consumed in the same transaction that creates the user and the membership: **single use, atomic**;
- revoked and expired invitations are hidden by the policy, accepted ones by the `WHERE`: every failure is the same `invalid`, no oracle;
- the role and the scope come from the invitation, **never from an argument** (there is no argument for them);
- an existing account is accepted only when `p_existing_user_id` (the logged-in session's user) equals it, and an inactive account never; the account is never modified;
- a duplicate membership (a race, or an old row) creates nothing and answers `invalid`.

It writes `users(email, full_name, password_hash)`, `memberships(user_id, organization_id, school_id, role, status)` and `invitations(accepted_at, accepted_user_id, updated_at)`. The token never reaches the database; only its hash does.

### The helpers (not `SECURITY DEFINER`)

`app_session_id()` and `app_user_id()` (owner `apm_owner`, `STABLE`, `SECURITY INVOKER`, `search_path = pg_catalog`) read the settings `app.session_id` and `app.user_id`, like `app_org()` and `app_school()`. `apm_definer` may call all four, because evaluating the existing policies calls them; it has no tenant context, so they match nothing for it.

## Row level security of the new tables

| Table | Policy | For | Rule |
| --- | --- | --- | --- |
| `sessions` | `sessions_select`, `sessions_update` | `apm_app` | `id = app_session_id() OR user_id = app_user_id()` |
| `sessions` | `sessions_insert` | `apm_app` | `user_id = app_user_id()` |
| `login_attempts` | `login_attempts_select` | `apm_app` | last 7 days |
| `login_attempts` | `login_attempts_insert` | `apm_app` | `attempted_at` within a minute of now |
| `login_attempts` | `login_attempts_delete` | `apm_app` | older than 24 hours (the select window is wider on purpose: a `DELETE ... WHERE` also has to pass the `SELECT` policy) |
| `invitations` | `invitations_select`, `_update` | `apm_app` | the usual scope: `organization_id = app_org()` and, in a school context, `school_id = app_school()` |
| `invitations` | `invitations_insert` | `apm_app` | the scope, `invited_by_user_id = app_user_id()`, not accepted, not revoked |
| `users` | `users_select_self`, `users_update_self` | `apm_app` | `id = app_user_id()` (the person's own row; the update is granted for `password_hash, updated_at` only) |
| `users` | `users_definer_select`, `users_definer_insert` | `apm_definer` | read all; insert only active users with a hash |
| `memberships` | `memberships_definer_select`, `_insert` | `apm_definer` | `status = 'active'` |
| `organizations`, `schools` | `*_definer_select` | `apm_definer` | only rows with a membership |
| `invitations` | `invitations_definer_select`, `_update` | `apm_definer` | not revoked and not expired; the update only on a live invitation and only to mark it accepted |

Grants are per column. `apm_app` cannot read `invitations.token_hash` (an invitation is found by its hash, never listed), cannot delete sessions, and can update only `last_seen_at`, `revoked_at`, `revoked_reason` on them. It still has no `INSERT` on `memberships`.

## What the database is told about each request

Four transaction-local settings, applied with `set_config(..., true)` and bound parameters, so they end with the transaction and never reach the next user of a pooled connection (`app/db/request_context.py`; tests cover the pool):

| Setting | Value |
| --- | --- |
| `app.request_id` | the id of the request: the same one in the `problem+json` and in `X-Request-ID` (always generated by the server, never taken from the client) |
| `app.actor_type` | `USER` for an authenticated session; `PUBLIC` for the anonymous flows (login, accepting an invitation with no session); `SYSTEM` for jobs. Always set explicitly, never defaulted |
| `app.user_id` | the authenticated user, set only after the session (or the password) is verified |
| `app.session_id` | hex SHA-256 of the token, so row level security shows that one session row |

The financial schema's audit triggers read `app.request_id`, `app.actor_type` and `app.user_id` (ADR-015). Each value can be bound **once** per database session: binding a different one raises, so a session never changes who it acts for midway.

## Logs

No log line, error or traceback of the API carries a password, a hash, a token or a cookie: error texts are fixed, the API's own log line for an unexpected error carries the exception **class and the request id only**, and the engine is built with `hide_parameters=True`, so even a failing statement does not print the values bound to it. Three things to know:

- **uvicorn also logs the full traceback** of an unexpected error, with the **message of the exception** (`ServerErrorMiddleware` re-raises after the API answered 500). The code therefore must never put a request value in an exception message; a static test covers the API's own log calls, not the text of exceptions that libraries raise.
- SQLAlchemy at **DEBUG** on `sqlalchemy.engine` prints the **result rows** of every query, and those hold password hashes (`find_login_identity`) and session ids. When the application builds its engine it raises `sqlalchemy.engine` and `sqlalchemy.engine.Engine` to INFO if they were lower, so an operator who turns the SQL log up to DEBUG still does not get rows. A level set **after** the engine was built (a late `dictConfig`) is not undone: do not set it.
- INFO (what `echo=True` does) prints the statements with the parameters hidden. The leak test runs under it.

## E-mail: the outbox

`EmailSender` is an interface; the only implementation, for development, writes each message as a file with mode `0600` in the directory `OUTBOX_DIR` (mode `0700`; a docker volume in compose, outside the repository). An invitation message contains the accept link, hence the token: that is why it is **development only** and the API **refuses to start with `ENV=production`** until a real sender exists. The password-changed notice never contains a password. A failing outbox is logged without content and does not undo the operation.

## Seed

`python -m app.seed` creates demo users with a password. The password is **not in the repository**: it is `SEED_PASSWORD` (at least 12 characters) or a random one printed **once**, only when users were just created. The seed still refuses `ENV=production`.

## Settings

| Variable | Meaning |
| --- | --- |
| `AUTH_SECRET` | HMAC key for the CSRF token and the rate-limit digests. At least 32 random characters (`openssl rand -hex 32`). Required; never in the repository |
| `COOKIE_SECURE` | `true` by default. `false` only with `ENV` `development` or `test` |
| `PUBLIC_ORIGINS` | the site's origins, comma-separated. Required (https) in production |
| `PUBLIC_BASE_URL` | where the e-mail links point |
| `TRUSTED_PROXY_IPS` | becomes `FORWARDED_ALLOW_IPS`: only the reverse proxy's address |
| `OUTBOX_DIR` | development e-mail files |

## Errors

`application/problem+json` with `type`, `title`, `status`, a stable `code`, `detail` when useful, and `request_id`. Messages are fixed text: no value from the request, a password, a hash, a token or a cookie appears in an error, a log line or a traceback.

| Status | `code` | When |
| --- | --- | --- |
| 400 | `invitation_invalid` | any reason an invitation cannot be accepted |
| 401 | `unauthenticated` | no session, or one that is unknown, revoked, expired or idle |
| 401 | `session_revoked` | the user or the membership stopped being active; the session was just ended |
| 401 | `invalid_credentials` | wrong password, unknown e-mail or inactive account (the same answer) |
| 403 | `permission_denied` | the role does not hold the permission |
| 403 | `csrf_failed`, `origin_not_allowed` | the CSRF defences above |
| 403 | `context_not_allowed` | switching to a membership that is not the caller's |
| 403 | `current_password_incorrect` | changing the password |
| 409 | `context_required` | the route needs a role and no membership is active |
| 409 | `already_member`, `invitation_pending`, `account_exists_login_required` | invitations |
| 422 | `validation_error`, `weak_password` | the body (`errors[]` carries `field` and a fixed `code`) |
| 429 | `rate_limited` | with `Retry-After` |
| 500 | `internal_error` | nothing else is revealed |

## Permissions

The map role to permissions is **code**, `ROLE_PERMISSIONS` in `app/auth/permissions.py` (ADR-016): `organization_admin` everything; `school_admin` everything except `months:reopen`; `treasurer` cash contributions, reading and approving expenses, reimbursements, refunds, closing months, statement and reports; `staff` submitting and reading their **own** expenses; `viewer` aggregate reports only. The financial permissions exist now and get their routes in the financial tasks.

## Threats considered

| Threat | What stops it |
| --- | --- |
| Theft of the sessions table (a backup, a read-only SQL injection) | only `SHA-256(token)` is stored; a hash does not authenticate. This is about **reading** the table; forging a row through SQL is R7 |
| Session fixation | the server never accepts an id from the client: login always issues a new token and revokes a presented one |
| Reuse of a revoked, expired, idle or rotated token | the session read excludes them; tests present each |
| Stolen cookie | `HttpOnly`, `Secure`, 30 min idle, 12 h absolute, revoked on password change |
| CSRF, including login CSRF | Origin check on every write (login and accept included), the double submit token bound to the session, `SameSite=Lax` |
| Cookie planted from a sibling subdomain | `__Host-` prefix; and a planted CSRF cookie fails the HMAC check |
| Account enumeration | the same `401` for unknown, inactive and wrong password; one Argon2id verification in every case; blocking before hashing and from the submitted value only; one `400` for every bad invitation; the same `403` for an unknown and a foreign membership |
| Brute force, credential stuffing | progressive block per (e-mail, IP), per IP and per e-mail; tokens are 256 bits; the invitation limiter is per IP so it cannot be used to lock someone out |
| Reusing, forwarding or guessing an invitation | single use under a row lock, 72 h, hash only, same answer for every failure, per-IP limit |
| Taking over an existing account with an invitation token | an existing account must be logged in as itself |
| Privilege escalation by body or header | the role and the tenant come from the session's membership, re-read on every request; the invited role and scope come from the invitation row; extra body fields are rejected; a test forges `X-Organization-Id` and similar |
| Escalating by inviting | `INVITABLE_ROLES`: a school administrator cannot invite an organization administrator or into another school |
| Access after an administrator revokes a membership | membership re-read on every request: the session is revoked in that request |
| A session pointing at someone else's membership | the composite foreign key `(membership_id, user_id)` |
| Cross-tenant read after login | row level security bound to the active membership's tenant (docs/tenancy.md) |
| Secrets in logs, errors, tracebacks, alembic output | fixed error texts, `SecretStr` for every secret, no SQL built from strings (a static test), canary tests over responses, logs and the migration output |
| Rate limits that become a lock-out of everyone | decisions from the submitted value only; a wrong `FORWARDED_ALLOW_IPS` is the one way (R5) |
| A new route that forgot its access rule | the route-walk test (below) |

Not done here, on purpose: a **retention job** (see [Known limits](#known-limits-and-operational-notes)), **2FA** (mandatory for `treasurer`, `school_admin` and `organization_admin` before any production with real money, ADR-016, planned for a later phase), **password recovery by e-mail**, OAuth, a real e-mail sender, auditing of the authentication events (the hook is in the password change), and a breached-password list.

## Accepted risks

1. **R1. `find_login_identity` hands the password hash to `apm_app`.** An attacker with arbitrary SQL as the application role can fetch the hash of a known e-mail and attack it offline. The ADR accepted it; Argon2id makes it expensive. Mitigation: SQL is always parameterised, and there is no way to list e-mails.
2. **R2. A cookie that is not `Secure` in development.** `COOKIE_SECURE=false` exists for WebKit over http and removes the `__Host-` protection. It is allowed only for `ENV` `development` and `test`; the API refuses to start in production without `true`. The proper fix is the TLS proxy of the deployment task.
3. **R3. A session with no membership (`membership_id` NULL).** A user with several memberships has a valid session before choosing one. Such a session has no tenant context, sees nothing tenant-owned, and every role-protected route answers `409 context_required`. It can read `me`, switch context and change its password.
4. **R4. `users_update_self` lets `apm_app` change its own password hash.** The column grant is `password_hash, updated_at`, the policy limits it to the row of `app.user_id`. With arbitrary SQL it is the forged setting of R7 that picks the row. Through the **endpoint** a password change revokes every session and tells the user through the outbox; through SQL it does neither (R7).
5. **R5. The client IP behind a proxy.** If `FORWARDED_ALLOW_IPS` does not hold exactly the proxy's address, the IP limits become a limit on the proxy (everyone shares one block) or, with too wide a list, the IP can be forged. It is documented above and tested for the case without a proxy.
6. **R6. An invitation for an existing account needs a login.** Anyone who holds the token of an invitation addressed to an e-mail that already has an account cannot use it without being that account. The cost is one extra step for that person; the benefit is that a leaked link cannot take over an account.
7. **R7. A forgeable setting (same nature as ADR-014), and what it buys an attacker here.** With arbitrary SQL as `apm_app` (a SQL injection, say), an attacker can set `app.user_id` to any user's id, exactly as they could set `app.organization_id` in ADR-014. Row level security protects against **application bugs**, not against SQL injection. In this task the forged setting is worth more than it was there:
   - **A forged session.** The policy `sessions_insert` only asks that `user_id = app_user_id()`, so the attacker can `INSERT` a session for **any user, with a token of their own choosing**. That is complete and persistent impersonation: no password is needed or changed, the user is not notified, nothing is revoked, and the session lives until it expires.
   - **Reading memberships.** `list_memberships_for_user(p_user_id)` does not tie its argument to `app.user_id`, so the memberships (organizations, schools, roles) of any user can be read by uuid.
   - Reading or changing the password hash of any user, through `users_select_self` and `users_update_self` (R4).
   - What does **not** hold: "the session id in the database is a hash, so knowing it authenticates nobody" is true only for **reading** that column. It says nothing about **writing** a row. And "a password change revokes every session and notifies the user" is a property of the **endpoint** `POST /auth/password`; the SQL path does neither.
   - Mitigations that do hold today: parameterised SQL everywhere (a static test), per-column grants, no `INSERT` on `memberships` or `DELETE` on `sessions` for the application role.
   - **Mitigation decided and deferred (before 2FA and before production):** `sessions.id` becomes `HMAC-SHA256(key derived from AUTH_SECRET, token)` instead of a plain SHA-256. It needs **no migration** (the column stays 32 bytes). A session row forged through SQL would carry an id the attacker cannot compute without the secret, which the database never holds, so it would never validate. It matters most for 2FA: a forged session would **skip** the second factor.

## Known limits and operational notes

Known and accepted for this task; the first two are for the retention job that must exist before production.

- **`login_attempts` older than 7 days are never deleted by the application.** The `SELECT` policy shows 7 days and a `DELETE ... WHERE` has to see the row it deletes, so the application removes rows older than 24 hours **that it can still see**; anything older than a week is invisible to it and stays. Rows hold only HMACs, never an e-mail or an IP.
- **`sessions` is never deleted and keeps the IP and a hash of the user agent without a time limit.** The application role has no `DELETE` on it, and nothing purges old rows. That is personal data kept with no end date (LGPD): a retention job (with its own role) must purge expired and revoked sessions before production.
- **No limit on the size of a request body.** The reverse proxy must set one (`client_max_body_size` in nginx, the equivalent elsewhere); the API only bounds the length of each field.
- **The invitation limit is per IP**, so a whole school behind one NAT address shares it; guessing tokens must not be able to lock other people out, which is why it is not per token.
- **The per-e-mail login limit lets a distributed attacker lock one e-mail** (30 failures an hour from any addresses). The cost of that is a delayed login for one person, not access; the alternative (limiting only by IP) is worse against credential stuffing.
- **`ENV=test` turns the posture check off and accepts a cookie without `Secure`.** It exists for the test suite and must never be used where real people log in.
- **There is no cap on the number of live sessions per user.**
- **With the database down the routes answer `500`**; only `GET /api/health/ready` answers `503`.

## Adding a route

Every route declares **exactly one** of three markers (`app/auth/deps.py`):

| Marker | Meaning |
| --- | --- |
| `require(Permission.X)` | an authenticated user, with an active membership, whose role holds that permission |
| `authenticated()` | any session, whatever the role (the route is about the user themself) |
| `public()` | no session; it must also be listed in `PUBLIC_ROUTES` with the reason |

```python
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import Principal, require
from app.auth.permissions import Permission
from app.auth.responses import problem_responses
from app.routers.deps import get_tenant_db

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.get(
    "",
    operation_id="expenses_list",
    response_model=ExpenseList,
    responses=problem_responses(401, 403, 409),
    openapi_extra={"x-permission": Permission.EXPENSES_READ_ALL.value},
)
def list_expenses(
    principal: Annotated[Principal, Depends(require(Permission.EXPENSES_READ_ALL))],
    db: Annotated[Session, Depends(get_tenant_db)],
) -> ExpenseList:
    ...  # db is already bound to the tenant of the active membership
```

1. Pick the marker. A new permission goes into `Permission` and into `ROLE_PERMISSIONS` for the roles that hold it.
2. Put `x-permission` in `openapi_extra` (it must equal the permission the route requires; the walk checks it) and list the error responses with `problem_responses(...)`. Give the operation a unique `operation_id`: the frontend client is generated from the document.
3. Use `get_tenant_db` for tenant data. Never read an organization or school from the path, a header, the query or the body; take it from `principal`. A route whose path carries a tenant id is a bug.
4. A handler that writes **commits explicitly**; the dependency does not.
5. A public route is an exception: add `(method, path): "why"` to `PUBLIC_ROUTES`, rate limit it, and keep the Origin check. Exempting a path from the Origin check (`ORIGIN_EXEMPT_PATHS`) is only for a route that authenticates by a secret of its own, such as a webhook.
6. A new table that holds tenant data needs `organization_id`, row level security (enabled **and** forced) and per-column grants, as in docs/tenancy.md. A new `SECURITY DEFINER` function needs an ADR first.
7. Run the tests. `tests/test_auth_routes.py` walks every registered route and fails when one has no marker or two, is public without being listed, has a stale entry in `PUBLIC_ROUTES`, carries the wrong `x-permission`, or when a non-public route answers anything but `401` without a session. It also fails when a route that changes state accepts a request without a valid `Origin`.
