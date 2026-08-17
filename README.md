# Payment Gateway API

A payment gateway that validates card payment requests, forwards them to an
acquiring bank, and lets a merchant retrieve a payment it made earlier.

- `POST /payments` — process a card payment
- `GET /payments/{id}` — retrieve a previously made payment
- `GET /` — liveness probe, no API key required (liveness only: it answers "is the
  process up", not "can this instance take a payment" — see
  [Concurrency and scaling](#concurrency-and-scaling))

Interactive API docs are served at http://localhost:8000/docs once the app is running.

---

## Running it

```bash
make install          # poetry install (Python 3.13)
make simulator        # docker compose up -d bank_simulator
make run              # http://localhost:8000
```

Or run the whole stack — gateway and simulator — in containers:

```bash
docker compose up --build
```

### Tests

```bash
make test                # unit + component tests: fast, no network needed
make test-integration    # integration tests against the running bank simulator
make test-all            # everything
make lint                # ruff check + format check
make format              # ruff check --fix + format
```

`make test` runs 156 tests in under two seconds. The 6 integration tests are marked
`integration` and deselected by default (`addopts = -m 'not integration'`); they need
the simulator to be up.

### Configuration

Every setting lives in [config.py](payment_gateway_api/config.py) and can be overridden
with a `GATEWAY_`-prefixed environment variable or a `.env` file — see
[.env.example](.env.example) for the full list with its defaults (bank URL and timeouts,
retry budget, supported currencies, amount bounds, merchant API keys, log level and
format).

---

## Using the API

Every request needs an `X-Api-Key` header identifying the merchant. Two keys are
configured out of the box: `sk_test_alpha` and `sk_test_beta`.

```bash
curl -X POST http://localhost:8000/payments \
  -H 'Content-Type: application/json' \
  -H 'X-Api-Key: sk_test_alpha' \
  -d '{
        "card_number": "2222405343248877",
        "expiry_month": 4,
        "expiry_year": 2030,
        "currency": "GBP",
        "amount": 1050,
        "cvv": "123"
      }'
```

```json
{
  "id": "0470143c-9a4a-4912-9dd8-c79d11df3a06",
  "status": "Authorized",
  "last_four_card_digits": "8877",
  "expiry_month": 4,
  "expiry_year": 2030,
  "currency": "GBP",
  "amount": 1050
}
```

```bash
curl http://localhost:8000/payments/0470143c-9a4a-4912-9dd8-c79d11df3a06 \
  -H 'X-Api-Key: sk_test_alpha'
```

The simulator decides the outcome from the card number's **last digit**: odd
authorizes, even declines, and `0` returns a 503.

### Responses

| Situation | Status | Body |
|---|---|---|
| Bank authorized the payment | `201` | the payment, `status: "Authorized"` |
| Bank declined the payment | `201` | the payment, `status: "Declined"` |
| Invalid payment details | `400` | `{"status": "Rejected", "errors": [{"field": "cvv", "message": "..."}]}` |
| Missing or unknown API key | `401` | `{"error": "unauthorized", ...}` |
| Unknown payment id, or one belonging to another merchant | `404` | `{"error": "payment_not_found", ...}` |
| Idempotency key reused with a different body, or still in flight | `409` | `{"error": "idempotency_conflict", ...}` |
| Acquiring bank unreachable (payment definitely not taken) | `502` + `Retry-After` | `{"error": "acquiring_bank_unavailable", ...}` |
| Acquiring bank answered unusably (our integration is at fault) | `502` | `{"error": "acquiring_bank_error", ...}` |
| Acquiring bank timed out (outcome unknown) | `504` | `{"error": "acquiring_bank_timeout", ...}` |
| Anything unexpected on our side | `500` | `{"error": "internal_error", "message": "An unexpected error occurred"}` |

### Validation rules

| Field | Rules |
|---|---|
| `card_number` | required, string, 14–19 characters, digits only |
| `expiry_month` | required, integer, 1–12 |
| `expiry_year` | required, integer, 2000–2100; with the month, must not be in the past |
| `currency` | required, 3 characters, one of `GBP`, `USD`, `EUR` |
| `amount` | required, integer ≥ 1, in the **minor** currency unit (`1050` = £10.50), below a configurable ceiling |
| `cvv` | required, string, 3–4 characters, digits only |

Validation is **strict**: `"4"` is not accepted where an integer is expected, so a
client bug is reported rather than guessed at. Unknown fields are rejected rather than
ignored, so a typo like `"ammount"` is reported instead of silently becoming a
different payment.

### Idempotency

`POST /payments` accepts an optional `Idempotency-Key` header. It must be a 36-character
UUID (with hyphens) — an arbitrary string is rejected with a `400`, which keeps merchants
from using a key space that collides across their own orders. Replaying a request with
the same key returns the original payment instead of taking a second one:

```bash
curl -X POST http://localhost:8000/payments \
  -H 'X-Api-Key: sk_test_alpha' \
  -H 'Idempotency-Key: 0c8f6f3a-3e9a-4f0e-9a19-9d3f3b1f5b6a' \
  -H 'Content-Type: application/json' -d '{...}'
```

Keys are scoped per merchant. Reuse is checked against an HMAC-SHA256 fingerprint of the
request (merchant, card number, expiry, currency and amount), so replaying a key with a
different payment is a `409` rather than a silent wrong answer. A second request arriving
while the first is still with the bank is a `409` too. If the bank could not be reached
at all, the key is released so the merchant can safely retry with it.

### Traceability

Every response carries an `X-Request-Id` header, echoing the client's if one was sent and
generating a UUID otherwise. Logs are JSON by default and every line carries that request
id, so one payment can be followed end to end across the API layer, the service and the
bank call. Only the route template is logged, never the query string.

---

## Design

### A payment, end to end

```mermaid
sequenceDiagram
    participant M as Merchant
    participant A as API layer
    participant S as PaymentService
    participant I as Idempotency store
    participant B as Acquiring bank
    participant R as Payment repository

    M->>A: POST /payments + X-Api-Key
    A->>A: authenticate, then validate
    Note over A,M: unknown key → 401 · invalid body → 400 Rejected<br/>(the bank is never called)
    A->>S: ProcessPaymentCommand
    opt Idempotency-Key present
        S->>I: reserve(merchant, key, fingerprint)
        alt already completed, same fingerprint
            I-->>S: record + payment id
            S->>R: get(payment id, merchant)
            R-->>M: 201, the original payment
        else different fingerprint, or still in flight
            I-->>M: 409 idempotency_conflict
        end
    end
    S->>B: authorize(card, expiry, currency, amount, cvv)
    alt authorized / declined
        B-->>S: {authorized: true|false}
        S->>R: add(payment)
        S->>I: complete(key, payment id)
        R-->>M: 201 Authorized | Declined
    else connection error or 503, after retries
        B--xS: definitely not processed
        S->>I: release(key)
        S-->>M: 502 acquiring_bank_unavailable + Retry-After
    else read/write timeout
        B--xS: outcome unknown
        Note over S,I: key deliberately kept
        S-->>M: 504 acquiring_bank_timeout
    else unusable answer
        B--xS: bad status or unparsable body
        S-->>M: 502 acquiring_bank_error
    end
```

Nothing is persisted on any of the three failure branches: a bank failure is never
recorded as a decline.

### Structure

```
payment_gateway_api/
├── api/               HTTP concerns only
│   ├── routers/         endpoints
│   ├── schemas/         request/response models and the service command
│   ├── services/        orchestration: idempotency → bank → persist
│   ├── middlewares/     request id, timing, access log
│   └── errors.py        domain error → HTTP status mapping
├── domain/            models, errors, and the ports (Protocols) the service needs
│   ├── models/
│   └── protocols/
├── infrastructure/    adapters
│   ├── clients/         HTTP acquiring-bank client
│   └── db/in_memory/    payment repository and idempotency store
├── config.py          settings, overridable via GATEWAY_* env vars
├── dependencies.py    auth and FastAPI wiring of the adapters into the service
├── logger_config.py   JSON logging with the request id attached
└── app.py             composition root: builds the concrete adapters in the lifespan
```

Dependencies point inwards. `domain` imports nothing; the service depends only on domain
ports; `infrastructure` implements those ports; `api` is the only layer that knows about
HTTP. The ports are `typing.Protocol`s, so adapters need no inheritance and tests pass
plain fakes.

The practical payoff: replacing the in-memory repository with Postgres, or the
simulator with a real acquirer, means one new class and one changed line in
`app.py`. Nothing in the service or API layer moves.

**Why layers and not domain modules.** This project is one bounded context with two
endpoints, so a technical layering is the honest structure: it is easy to navigate and
there is nothing to separate. On a larger system I would slice by domain first —
`payments/`, `refunds/`, `disputes/`, `payouts/`, each owning its own api / service /
domain / infrastructure internally, communicating through published contracts rather than
shared tables. Layer-first structure stops scaling exactly when a "services" package
becomes a bag of unrelated things and a change to one feature makes you touch four
packages; that point is far past the size of this exercise.

### Key decisions

**Rejected is a `400`, not a `422`.** The brief treats rejection as a first-class
outcome of the payment API, so it gets a payment-shaped response
(`{"status": "Rejected", "errors": [...]}`) rather than FastAPI's default
validation body. All invalid fields are reported at once, so a merchant can fix
them in one pass. `PaymentStatus` therefore contains only `Authorized` and
`Declined`: a rejected request never became a payment, so it is never stored.

**A bank failure is never recorded as a decline** — and the two kinds of failure
are told apart, because the difference matters financially:

| | Evidence | Response | Idempotency key |
|---|---|---|---|
| `503` after retries, or a connection failure | the payment was **definitely not taken** | `502` + `Retry-After`, "safe to retry" | released, so the same key can be reused |
| Read/write timeout | the payment **may have been taken** and we never saw the answer | `504`, "outcome unknown" | **kept**, so replaying the key returns `409` instead of charging twice |
| Unexpected status or unparsable body | the bank answered, but **we** could not use it | `502` `acquiring_bank_error`, no retry advice | kept |

Recording either as `Declined` would be a lie the merchant cannot detect, so
nothing is persisted in both cases. But telling a merchant a timed-out payment is
"safe to retry" would be the more expensive lie: the same evidence that decides
whether we may retry the bank call also decides whether we may free the merchant's
idempotency key. An unexpected error from our own code is treated as unknown too —
the safe default.

Resolving a genuinely unknown outcome needs reconciliation against the acquirer,
which is listed in the roadmap below; guessing is not an option.

**The bank's status codes and messages are never relayed to the merchant.** Our public
contract is ours: we map every acquirer outcome onto our own small, stable set of error
codes (`acquiring_bank_unavailable`, `acquiring_bank_timeout`, `acquiring_bank_error`) and
the bank's own wire format stops at the client adapter.

Passing it through would be a coupling bug waiting to happen. The day the acquirer renames
a field, changes a status code, or reworks its error taxonomy, every merchant integration
built against *our* API breaks — even though nothing in our contract changed — and the same
gateway fronting a second acquirer would start speaking two different error languages for
the same situation. It also leaks integration detail merchants have no business seeing.

The concrete case here: the simulator answers `400` when a required field is missing, but
our validation makes every field required, so the bank can only say that if *we* built the
request wrongly. Handing "a required field is missing" back would send the merchant hunting
for a bug in their own perfectly valid request. Instead it is logged loudly for us and
reported as `acquiring_bank_error` with no invitation to retry — a retry fails identically
until we deploy a fix. The bank's response body is for our logs and our alerts, not for the
merchant's error handler.

**Retries are deliberately narrower than the HTTP convention.** Submitting a
payment is not idempotent and the simulator offers no idempotency key of its own,
so a blind retry can charge a shopper twice. We retry only failures that prove the
request never reached processing — connection errors, connect timeouts, pool
timeouts, and `503` — with exponential backoff and full jitter.

Everything a general-purpose HTTP client would also retry is excluded, on purpose:
a **read timeout** means the request was delivered and the payment may already have
been taken; **502** and **504** come from an intermediary that had already
forwarded the request upstream, so they carry exactly the same ambiguity; and a
**500** is deterministic, so retrying only reproduces it. A double charge is worse
than a retry the merchant makes themselves.

**Only the last four digits are stored.** The full PAN exists in the request object
and the outbound call to the bank, and nowhere else. The domain models exclude the
card number and CVV from their `repr`, so a stray log line or an exception message
that interpolates a request cannot leak them — there are tests asserting exactly
that. Responses never contain a full card number or a CVV.

**Payments are scoped to a merchant.** Every request resolves an `X-Api-Key` to a
merchant id, and retrieving another merchant's payment returns `404`, not `403`:
a `403` would confirm that the id exists.

**Immutable domain models.** The in-memory repository hands out references to the
objects it stores, so frozen dataclasses prevent a caller from rewriting stored
state by accident — the classic in-memory-repository bug.

### Concurrency and scaling

Within one process the design is safe under load: the idempotency store serialises
`reserve`/`complete`/`release` behind an `asyncio.Lock`, so two concurrent requests with
the same key cannot both reach the bank — the loser gets a `409` — and there are unit
tests that hammer the in-memory adapters concurrently to prove it. Domain models are
frozen, so a caller cannot mutate stored state through a reference the repository handed
out.

Across processes it does not scale yet, and that is the honest limitation of in-memory
storage:

- **One worker only.** Two uvicorn workers would each hold their own repository and
  idempotency store, so a replayed key could land on the worker that has never seen it
  and charge the shopper twice. `main.py` pins `workers=1` deliberately.
- **No horizontal scaling, no zero-downtime deploy.** A second instance behind a load
  balancer has the same problem, and a restart loses every payment made so far.
- **`GET /` is a liveness probe, not a readiness one.** It reports that the process is
  answering, which is the right signal for "restart me if I stop responding". It says
  nothing about whether this instance can actually take a payment. A real deployment wants
  a separate `/health/ready` that fails when the acquirer is unreachable or the circuit
  breaker is open, and that is flipped to unhealthy at the start of a shutdown so traffic
  drains before the process goes away (roadmap items 4 and 5).

All three disappear together once the repository and idempotency store move to Postgres
behind their existing ports — the unique index on `(merchant_id, key)` gives across the
cluster exactly what the `asyncio.Lock` gives within one process. Nothing in the service
or API layer changes.

### Threat model, and what is deliberately not defended against

What the code does defend:

| Threat | Defence |
|---|---|
| Cardholder data leaking into logs, tracebacks or responses | Only the last four digits are ever stored; PAN and CVV are excluded from every model's `repr`; responses never carry them; tests assert all of it |
| One merchant reading another's payments | Every read is scoped by the merchant resolved from the API key, and a foreign id returns `404`, not `403`, so existence is not confirmed |
| A merchant's client bug charging a shopper twice | Idempotency keys with an HMAC fingerprint of the request |
| A retry storm charging a shopper twice | Retries restricted to failures that prove the request never reached processing |
| Internal state disclosure on an error | Unhandled exceptions are logged with a traceback and answered with a fixed, contentless `500` |
| Upstream detail leaking into our contract | The acquirer's status codes and bodies stop at the client adapter |

What is **out of scope here**, and would need to exist before real money moves:

- **Credential strength.** A static key-to-merchant map in configuration. Keys are neither
  hashed nor rotatable, comparison is not constant-time, and possession of a key is
  currently sufficient to move money — request signing (roadmap 3) is what fixes the last
  one.
- **Transport security.** The app speaks plain HTTP and assumes TLS is terminated in front
  of it. No HSTS, no certificate pinning on the outbound acquirer call.
- **Abuse and enumeration.** No rate limiting, no per-merchant quotas, no lockout. Card
  testing (a fraudster probing stolen numbers through a merchant's key) and brute-force
  CVV attempts are not detected here.
- **Fraud and compliance screening.** No velocity checks, no 3-D Secure, no AVS, no
  sanctions screening.
- **PCI DSS.** The gateway touches the PAN in memory, which puts it squarely in scope; the
  real answer is to never hold it (roadmap 2). Nothing here constitutes an attestation.
- **Infrastructure concerns.** Secrets come from environment variables, not a secret
  manager; there is no audit log of administrative access; the in-memory store means no
  encryption at rest to reason about, and no key management once there is.

### Assumptions

- **Currencies.** `GBP`, `USD` and `EUR` are supported, per the brief's "no more
  than 3 currency codes". The list lives in configuration, not in code.
- **Expiry.** A card is valid through the *end* of its expiry month, evaluated in
  UTC. A card expiring this month is accepted.
- **Amounts** are integers of at least 1 minor unit, with a configurable ceiling so
  an absurd value is caught by us rather than by the bank.
- **Authorization codes** are stored but not returned: the brief's response tables
  do not include them and the merchant has no use for one here.
- **Authentication** is a static key-to-merchant map, which is enough to
  demonstrate merchant scoping. It is not a real credential system.
- **Storage is in-memory**, as the brief permits, so payments do not survive a
  restart — and the process runs with a single worker, since two workers would not
  share the repository or the idempotency store.
- **Currency codes are accepted case-insensitively** and normalised to uppercase.

### Testing

| Suite | What it covers |
|---|---|
| `tests/unit/` | Every validation rule; service orchestration and idempotency against fakes; the bank client against a mocked transport (wire format, retries, malformed responses); the in-memory adapters under concurrency; that cardholder data never reaches a log or a traceback |
| `tests/unit/test_validation_properties.py` | Property-based (hypothesis): arbitrary JSON input never crashes the validator, and anything it accepts really does satisfy the rules |
| `tests/components/` | The full application over HTTP with only the bank stubbed: status codes, response shapes, auth, merchant scoping, idempotency, correlation ids, and that internal errors leak nothing |
| `tests/integration/` | The real Mountebank simulator: authorize, decline, the 503 → 502 path, and retrieval |

Tests are named after the behaviour they protect rather than the method they call,
and warnings are errors.

---

## What would come next in production

Deliberately out of scope here, in roughly the order I would tackle them:

1. **Real storage** — Postgres behind the existing `PaymentRepository` port, with
   migrations. The idempotency store becomes a table with a unique index on
   `(merchant_id, key)`, which gives the same atomicity the in-memory lock does
   today, plus a TTL (`idempotency_key_ttl_seconds` is already in configuration, unused
   while storage is in-memory).
2. **Never hold the PAN at all** — tokenize at the edge (or take card details via a
   hosted field / client-side SDK) so the gateway handles a token and drops out of
   PCI DSS scope almost entirely.
3. **Signed requests, not just a bearer key** — hashed keys in the datastore, rotation,
   scopes and per-merchant rate limiting, but more importantly **a stolen API key alone
   should not be enough to move money**. Each merchant gets a signing secret; the client
   sends `X-Signature: HMAC-SHA256(secret, timestamp + nonce + raw body)` and the gateway,
   holding the same secret, recomputes it and rejects any mismatch. That proves the request
   really came from the merchant and that nobody altered the amount in flight; the
   timestamp and nonce (with a short acceptance window) defeat replay. Larger merchants
   would get mutual TLS, or asymmetric signing so we hold only their public key and cannot
   forge their traffic ourselves. Note that the idempotency fingerprint described above
   does *not* do this — it is computed from the request we already received, so it detects
   client bugs, not forgery.
4. **Resilience** — a circuit breaker around the acquirer so a sustained outage
   fails fast instead of burning the request budget, plus a reconciliation job that
   settles payments whose outcome was left unknown by a read timeout. With a real
   acquirer that accepts an idempotency key, the retry policy could safely widen
   beyond `503`, since the acquirer itself would deduplicate. Rate limiting
   (`429`) should also be retried, honouring `Retry-After`.
5. **Graceful shutdown** — uvicorn already traps `SIGTERM`/`SIGINT` and runs the lifespan
   teardown that closes the HTTP client, but that is the floor, not the goal. Killing the
   process mid-authorize manufactures exactly the "outcome unknown" case the design works
   hardest to avoid, so a deploy should: flip a readiness flag to unhealthy first so the
   load balancer stops sending new requests (a separate `/health/ready` alongside the
   liveness probe, plus a preStop delay under Kubernetes), then let in-flight requests
   drain within a bounded window (`--timeout-graceful-shutdown`), and only then close the
   bank client. Anything still in flight when the window expires must be logged with its
   payment id so reconciliation can pick it up rather than it vanishing with the process.
6. **Observability** — OpenTelemetry traces spanning the bank call, and a `/metrics`
   endpoint for Prometheus. Beyond the usual RED metrics (request rate, error rate,
   latency histograms per route), the ones that actually matter here are business and
   integration signals: authorization vs decline rate, decline reasons, bank-call latency
   percentiles, retry and timeout counters, circuit-breaker state, idempotency replays and
   conflicts. Alerting on an authorization-rate drop is how a broken acquirer integration
   is usually noticed first — long before error rates move, because a decline is a
   perfectly successful HTTP request.
7. **A full audit trail, not just successful payments** — today only authorized and
   declined payments are persisted; a rejected request or a bank failure leaves nothing
   behind for the merchant. I would record every *attempt* (rejected, failed, timed out,
   authorized, declined) as an immutable append-only record, exposed as
   `GET /payments?status=…&created_after=…` or a dedicated attempts resource, so a merchant
   can reconcile their own side and answer "what happened to order 1234?" without opening a
   support ticket. The same table is what a reconciliation job reads, what dispute handling
   needs, and what a regulator would ask for. Two constraints: it stores no PAN or CVV
   (last four, a token, and a fingerprint only), and writing the attempt record must not be
   able to fail the payment itself.
8. **Product surface** — refunds, voids, captures, 3-D Secure, multi-acquirer
   routing, and webhooks so merchants learn about asynchronous outcomes.
9. **CI** — lint, unit/component and integration suites on every PR. Omitted here only to
   avoid consuming Actions minutes on a take-home.
10. **Load and soak testing** before anyone puts real money through it.

---

## Template notes

The original scaffold has been reorganised, as the template invites. Its health-check
endpoint (`GET /`) and conventions are preserved: `make install`, `make run` and
`make test` work as documented, `main.py` is still the entrypoint, and `imposters/` and
`.editorconfig` are untouched. Python was moved from 3.8 (end of life) to 3.13, and
pydantic from v1 to v2.
