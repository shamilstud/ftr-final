# Requirements and dependability model (work package A)

System: the hoWallet Go API used as a stand-in **university records service** (synthetic data only).
Mapping: *household* = a student's record space, *account* = a ledger (e.g. tuition ledger), *transaction* = a
record write (e.g. a fee payment), *member* = a person allowed to see that record space.
Code references are to the hoWallet repository at commit `06aea177`; lab configuration is in `ftr-final/k8s/`.
"Exp 1–8" below are the final-project experiments (assignment §6); "endterm E1–E8" are the earlier runs whose raw
data is in `evidence/endterm-results/`.

## 1. Users, workflow, data, consequences of failure

| Item | Description |
|---|---|
| Users | Students (own record space), staff/members invited into a record space, operators (cluster admin) |
| Main workflow | Sign in → choose record space (`X-Household-ID`) → read ledgers and records → post a record (fee payment) that moves a ledger balance |
| Data handled | Synthetic users (e-mail, bcrypt hash), record spaces, ledgers with balances, records (amount, currency, date, `client_id`), request/audit logs |
| Consequence of failure | Unavailable: students cannot see or pay fees (deadline risk). **Wrong or duplicated record: a fee charged twice or a payment lost – worse than an outage** (repo rule: "a wrong number is worse than an honest error"). Disclosure: one student sees another's records |

## 2. Critical functions

| ID | Critical function | Route(s) | Components on the path | Multi-step? |
|---|---|---|---|---|
| CF1 | **Authenticate** (log in, refresh, verify token on every call) | `POST /auth/login`, `POST /auth/refresh`; JWT check on all `/api/*` (`internal/middleware/auth.go:90-160`) | gateway → api (auth service, session check in Postgres) → postgres | yes: credential check + session row + token issue |
| CF2 | **Post a record and update the ledger balance** | `POST /api/transactions` (`internal/router/router.go:397-398`, `internal/handler/transaction.go:27-68`) | gateway → api (membership check, money resolution, DB transaction: insert row + update balance, then audit) → postgres | **yes** – insert and balance update in one DB transaction (`internal/service/transaction.go:501-536`), idempotent by `client_id` |
| CF3 | **Read records and balances** | `GET /api/accounts`, `GET /api/transactions` | gateway → api (JWT, live session, membership) → postgres | no (read-only, safe to retry) |
| CF4 | **Tenant isolation** (only members see/change a record space) | `HouseholdCtx` on every household route (`internal/middleware/auth.go:180-217`) | api → postgres | – (cross-cutting) |
| CF5 (supporting) | Observe and alert | Prometheus scrape of `/metrics`, 3 alert rules (`k8s/50-prometheus.yaml`) | prometheus → api pods; grafana → prometheus | – |

## 3. Service-level objectives (SLOs) and acceptance criteria

All SLOs are measured by the client-side probe/writer scripts of the experiments (open-loop requests through the
gateway, `127.0.0.1:30080`). A request **fails** if it gets 5xx, a timeout or a connection error; 429 is the limiter
working as designed and is counted separately (excluded). Windows are defined in `reliability.md` §2.

| ID | Objective | Metric | Target (improved config) | Window | Checked in |
|---|---|---|---|---|---|
| SLO-1 | Availability of CF3 in steady state | request-success ratio `ok/(ok+failed)` | ≥ 99.5 % | 120 s quiet window at 5 req/s | Exp 5 (warm phase), Exp 6 (quiet window) |
| SLO-2 | Availability of CF3 during a single API-instance failure | request-success ratio | ≥ 99 % (target 100 %) | injection −5 s … +20 s | Exp 1 |
| SLO-3 | Latency of CF3 | p95 latency | ≤ 500 ms at 30 req/s | 60 s load step | Exp 5 |
| SLO-4 | Bounded failure latency under any dependency fault | max latency of any answer | GET ≤ 10.5 s (2 tries × `proxy_read_timeout 5s` + margin; endterm E2 measured a 10.0 s maximum), POST ≤ 5.5 s (one try); no hung connection | fault window | Exp 2, Exp 3 |
| SLO-5 | Recovery time (RTO) | Exp 1: injection to replacement ready and no failures. Exp 2: restore command to end of the last failed request. Exp 8: undo to bad pod gone | ≤ 10 s instance; ≤ 30 s database | per injection | Exp 1, Exp 2, Exp 8 |
| SLO-6 | Write integrity of CF2 | lost acknowledged writes; duplicate `(household_id, client_id)`; `balance = −Σ amounts` | **0 lost, 0 duplicates, balance check passes** | whole run | Exp 2, Exp 4, Exp 8 |
| SLO-7 | Detection time | fault onset → alert `firing` | ≤ 30 s; 0 alerts in a quiet window (no false positive) | per injection | Exp 6 |
| SLO-8 | Access control | share of unauthenticated / foreign-tenant requests denied; data changed by them | 100 % denied (401/404), 0 rows changed, each denial logged with `request_id` | per test case | Exp 7 |
| SLO-9 | Backup recoverability (RPO) | records missing after restore vs. records at dump time | 0 records lost since the last `pg_dump`; RPO = time since that dump | restore drill | **Not tested in the final project.** Exp 8 as run is a release rollback. The only drill is the earlier one in `ftr-a3` |

## 4. Assumptions

1. Single-node kind cluster on Docker Desktop/WSL2; node, zone and host failures are **out of scope**.
2. One PostgreSQL instance (StatefulSet, 1 replica, PVC 1 Gi). No streaming replica: database failover is not
   claimed; protection is restart-in-place plus logical backup (`pg_dump`).
3. Fault model is one fault at a time (single-fault assumption); faults are injected deliberately by `kubectl`,
   `docker exec`, SQL locks or config changes.
4. Clients reach the cluster through NodePorts; kind SNATs them to one address, so per-IP limits are shared by all
   test clients.
5. Clients retry writes **with the same `client_id`** (as the real web client's outbox does); the gateway never
   replays POST.
6. The exchange-rate provider is a non-critical external dependency; records in the base currency must not depend
   on it.
7. All data and secrets are synthetic lab placeholders; tests run only in this authorised local environment.
8. Clocks of client and cluster are the same host clock (timestamps comparable to ~1 ms).

## 5. Terminology (Avizienis, Laprie, Randell, Landwehr 2004)

| Term | Definition (2004 taxonomy) | Example in this system |
|---|---|---|
| **Fault** | The adjudged or hypothesised cause of an error. Active when it produces an error, dormant otherwise | API pod killed; Postgres stopped; 3 s injected delay; a client sending a forged token; code path that writes a rate of 1 when a rate is missing (design fault) |
| **Error** | The part of the system state that may lead to a failure | gateway upstream list still contains a dead pod; pgx pool connections broken; a transaction row inserted but balance not yet updated; a duplicate row from a replayed POST |
| **Failure** | The delivered service deviates from correct service (observable at the service interface) | client gets 5xx/timeout; a fee shown twice; a balance that differs from the sum of records; another tenant's data returned |
| Chain | fault → (activation) → error → (propagation) → failure; a failure of a component is a fault for the system that uses it | Postgres down (fault) → membership query fails (error in api) → 500 to client (failure of api) → fault for the user's workflow |
| Means | fault prevention, **fault tolerance**, fault removal, fault forecasting | tolerance: replicas, retries, idempotency, rollback; removal: 2 894 Go test functions (static count, `test-inventory.md`); forecasting: reliability calculations, experiments |
| Attributes | availability, reliability, safety, confidentiality, integrity, maintainability | SLO-1..5 availability/reliability, SLO-6 integrity, SLO-8 confidentiality, SLO-5 maintainability (repair time) |
| Failure modes used here | content failure (wrong value), timing failure (late/hung), halt/silent, erratic; signalled vs. unsignalled | JSON 503 = **signalled** halt (good); hung connection = timing failure; duplicate record = content failure (worst) |

## 6. Fault model

| Class (2004) | Fault | Persistence | Injected in |
|---|---|---|---|
| Physical / operational, crash | API process or pod lost (delete, SIGKILL) | transient (replaced) | Exp 1, Exp 4 |
| Operational, hang | API process frozen (SIGSTOP) – stops answering but keeps TCP | transient (liveness restart) | Exp 6 |
| Dependency, crash/hang | PostgreSQL stopped (scale 0) or frozen | transient (restored) | Exp 2 |
| Interaction, timing | 2000 ms delay on the API → Postgres link, beyond the gateway read timeout; table locks | transient | Exp 3, Exp 6 |
| Interaction, overload | traffic step 5 → 30 → 5 req/s | transient | Exp 5 |
| Interaction, malicious / invalid input | missing/forged token, foreign `X-Household-ID`, malformed body, brute force | permanent while attacker acts | Exp 7 |
| Development / configuration | bad release rolled out (`API_PORT=9999`) | permanent until rolled back | Exp 8 |
| Physical, storage | lost database volume | permanent until restored | not injected in the final project |

## 7. Failure scenarios and failure-impact analysis

Severity: **H** = data integrity or confidentiality at risk, **M** = critical function unavailable, **L** =
degraded but correct.

| # | Scenario (Exp) | Fault | Error | Failure (if not tolerated) | Impact (sev.) | Detection | Mitigation (improved config) |
|---|---|---|---|---|---|---|---|
| FS1 | Instance failure (Exp 1) | one API pod deleted or crashes | gateway/kube-proxy still route to the dead endpoint for ≤ 2 s; in-flight requests cut | 5xx / connection reset; baseline: 25.0–25.6 s outage after a graceful delete, 2.0 s after a forced one (Exp 1; the endterm's 23 s was understated) | CF1–CF3 unavailable (M) | readiness probe (2 s), endpoint drop, `ApiReplicaDown` (counts healthy replicas), error-rate alert | 2 replicas, `maxUnavailable 0`, preStop drain 5 s, gateway bounded retry on the other pod (`proxy_next_upstream … tries 2`), passive ejection `max_fails=1 fail_timeout=2s` |
| FS2 | Database unavailable (Exp 2) | Postgres scaled to 0 or frozen | pool connections fail; membership/session queries fail | baseline: requests hang until client timeout (12 s); risk of half-applied write | CF1–CF3 down (M); integrity risk (H) | `ApiHighErrorRate`, `ApiHighLatencyP95`, ERROR log "failed to check household membership" | fail fast: gateway 5 s read timeout → JSON 503 + `Retry-After`; membership check fails **closed** (500); write is one DB transaction → nothing half-applied; client retries with same `client_id` |
| FS3 | Network delay / timeout (Exp 3) | `tc netem delay 2000ms` on egress to Postgres in one API pod (the only pod for baseline) | requests exceed their deadline; upstream slots held | unbounded waits; retry storm amplifying load | latency SLO broken (M) | `ApiHighLatencyP95`, gateway log `upstream_response_time` | `proxy_connect_timeout 1s`, `proxy_read_timeout 5s`; **retries bounded to 2 tries** and only for idempotent methods; API server timeouts (read 15 s, write 15 s, header 5 s, `cmd/api/main.go:81-88`) |
| FS4 | Interrupted multi-step transaction (Exp 4) | API process SIGKILLed between "insert record" and "update balance". A loss after commit but before the client saw the answer is a further risk, handled by the same key, and not injected here | uncommitted DB transaction; client does not know whether the write happened | **lost** record (client gives up) or **duplicate** record (client retries a committed write); balance ≠ Σ records | fee lost or charged twice (H) | integrity check: rows vs. acknowledged writes, duplicate `client_id`, balance = −Σ | `RunInTx` (insert + balance in one transaction, rollback on any error), idempotency key `client_id` + unique index → retry returns the first row (200) |
| FS5 | Load step (Exp 5) | offered load 5 → 30 → 5 req/s | queueing in pool (40 conns/replica), CPU throttling, limiter counters fill | p95 above SLO; 429 for everyone behind one IP; 5xx | slow/denied service (M/L) | p95 alert, `http_requests_in_flight`, `db_pool_acquired_conns` | 2 replicas share load; per-user/per-IP rate limits answer JSON 429 + `Retry-After` instead of collapsing |
| FS6 | Monitoring / alert (Exp 6) | a replica hangs (SIGSTOP), the database stops for 25 s, or lock bursts slow it for about 40 s | the system degrades without users reporting it | incident unnoticed (false negative) or alert noise (false positive) | prolonged outage (M) | 3 Prometheus rules (`for` 6–10 s), Grafana dashboard, correlated `request_id` in gateway + API logs | rule `ApiReplicaDown` fixed to count healthy replicas (endterm finding); alerts scoped to `job="api"`, `route!="/health"` |
| FS7 | Unauthorized / invalid operation (Exp 7) | request without token, forged JWT, user B targets A's record space, malformed or huge body, password brute force | request reaches business logic with wrong identity or bad input | disclosure or tampering of another student's records; crash on bad input | confidentiality/integrity (H) | 401/403/404/400/413/429 in logs with `request_id`, `user_id`; `app_logs` rows | JWT (HMAC, `iss`, `exp`), live-session check, verified-email gate, membership check → 404, input validation, 1 MB decode / 2 MB gateway cap, login lockout, rate limits (see `security-controls.md`) |
| FS8 | Recovery / rollback (Exp 8) | release listening on the wrong port (`API_PORT=9999`) | bad pod never ready; rollout stalls | service down after a deploy, if old pods were removed | M | readiness failure (2.1–2.4 s); **no alert watches rollouts**, measured as a monitoring gap | rolling update keeps old pods until new ones are ready; `kubectl rollout undo`. A default RollingUpdate with 1 replica does the same. Database loss is not part of Exp 8: `pg_dump` + `pg_restore` was drilled only in ftr-a3 (19.2 s) |

## 8. Single points of failure (SPOF)

| Component | SPOF? | Effect when it fails | Accepted because / mitigation |
|---|---|---|---|
| PostgreSQL (1 instance, 1 PVC) | **yes** | every critical function fails (FS2); data loss if the volume is lost | lab scope; restart in place, fail-fast + JSON 503, `pg_dump` backup and restore drill (RPO = time since last dump) |
| kind node / host | **yes** | everything down | out of scope (assumption 1) |
| kube-dns | partly | gateway cannot re-resolve `api-headless` (cached `valid=2s`), new pods not discovered | kube-system replicas; nginx keeps last resolved addresses |
| Prometheus (1 replica) | yes, for **monitoring only** | no alerts, service itself unaffected | documented; would be a pair + Alertmanager in production |
| Grafana (1 replica) | yes, for dashboards only | no dashboard; alerts unaffected | – |
| Migration Job | start-up only | new schema not applied; the API has no schema-version check, so affected queries fail at run time | applied before the API starts; `migrate up` applies only versions above the stored one |
| JWT secret (one HMAC key) | yes (security) | if leaked, any token can be minted | Secret in K8s, no rotation in lab (`threat-model.md` A3) |
| Login lockout / rate-limit counters | not a SPOF, but **not shared** | each replica counts separately (2× limits), reset on restart | documented limitation; Exp 7: first 429 at attempt 11 instead of 6 |
| API, gateway | **no** (2 replicas each) | – | redundancy + retries |
