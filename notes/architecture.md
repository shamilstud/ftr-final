# Architecture and redundancy (supports work packages C and E)

Diagram source: `scripts/architecture.html`, carried over from the endterm and relabelled; rendered to `report/fig/architecture.png`. Lab: single-node kind cluster, namespace
`university`, all data synthetic. Manifests: `k8s/`.

## 1. Overview

```
            host :30080                 host :30090      host :30300
                |                            |                |
          +-----v-----+                +-----v-----+   +------v------+
          |  gateway  |   scrape       |Prometheus |-->|   Grafana   |
          | nginx x2  |<---------------|  (2 s)    |   | (dashboard) |
          +-----+-----+                +-----+-----+   +-------------+
    proxy, retries, JSON 503,                | dns_sd per pod, /metrics
    X-Request-Id|                            |
          +-----v-----+ <--------------------+
          |    api    |  2 replicas, preStop drain, probes
          |  x2 :8080 |
          +-----+-----+
                | 5432 (pgx pool, 40 connections per replica)
          +-----v-----+
          | postgres  |  StatefulSet x1, PVC 1 Gi; schema from the migration Job
          +-----------+

baseline: client --> api-baseline x1 (NodePort :30081, no gateway) --> same postgres
```

## 2. Services (independently identifiable)

| Service | Kind / replicas | Responsibility | Health check | Depends on | Recovery |
|---|---|---|---|---|---|
| gateway (nginx 1.27) | Deployment ×2, NodePort 30080 | Single entry; timeouts, retries, JSON 503/413, correlation id, IP header overwrite | readiness and liveness `/healthz` | api, kube-dns | kubelet restart; other pod serves |
| api (hoWallet Go) | Deployment ×2, `maxUnavailable 0`, `maxSurge 1` | Business logic, auth, jobs, `/metrics` | readiness `/health` 2 s; liveness 10 s × 3 | postgres; Frankfurter (optional) | ReplicaSet replacement; liveness restart; preStop `sleep 5` |
| postgres 16 | StatefulSet ×1, PVC 1 Gi | Persistent store | readiness probe, period 5 s | volume | restart in place; `pg_restore` from a dump |
| university-migrate | Job (`backoffLimit 10`) | Applies schema migrations | – | postgres | Job retry |
| prometheus 2.54.1 | Deployment ×1, NodePort 30090 | Scrape every 2 s, 3 alert rules, 1 h retention | – | api pods | restart; history lost |
| grafana 11.2.0 | Deployment ×1, NodePort 30300 | Dashboard `howallet-endterm` | – | prometheus | dashboard provisioned from a ConfigMap |
| api-baseline | Deployment ×1, NodePort 30081 | Comparison path | readiness only | postgres | replacement pod only |

## 3. Modules inside the API

Layers are one-way: `handler → service → repository → db`.

| Module | Code | Role | Failure containment |
|---|---|---|---|
| Auth | `internal/service/auth.go`, `internal/middleware/auth.go`, `internal/middleware/session.go` | Login, tokens, membership | Membership check fails closed; session liveness fails open on a DB error |
| Transactions | `internal/service/transaction.go`, `internal/service/money.go`, `internal/db/transactions.go` | Records and balances, the critical workflow | One DB transaction, `client_id`, refusal on a missing rate |
| Background jobs | `internal/service/jobs.go`, registered in `internal/app/app.go:594-717` | Periodic work (recurring, exchange rates, retention and others) | A failed pass is logged and retried on the next tick. Jobs run in **every** replica; only the recurring job takes an advisory lock |
| Observability | `internal/middleware/logger.go`, metrics patch (`feat/metrics`, `60cd5791`, private) | JSON logs, `app_logs`, `/metrics` | The metrics patch applies to the lab image only, not to the repo's `main` |

## 4. Dependencies

| From → to | Protocol | Required? | If it fails |
|---|---|---|---|
| client → gateway | HTTP | yes | no service |
| gateway → api | HTTP, per-pod DNS (`api-headless`) | yes | retry on the other pod, then JSON 503 |
| gateway → kube-dns | DNS, `valid=2s` | for pod changes | keeps last resolved addresses; new pods not discovered |
| api → postgres | pgx | yes | critical functions fail; no rows half-written |
| api → Frankfurter | HTTPS, background job only | no | same-currency writes unaffected; foreign currency uses the archive, else `422 no_rate` |
| prometheus → api | HTTP `/metrics` | no | no metrics or alerts; service unaffected |
| grafana → prometheus | HTTP | no | no dashboard |

## 5. Data flows

**Critical workflow: post a record.**

1. Client sends `POST /api/transactions` with a bearer token, `X-Household-ID` and `client_id`.
2. Gateway sets `X-Request-Id`, overwrites `X-Real-IP`, and proxies to one pod. It does not replay the POST once sent.
3. API middleware runs: request id, logger, rate limit, JWT, live session (Postgres), per-user limit, verified e-mail,
   membership (Postgres).
4. `CreateOnce` looks up `client_id`; on a miss it resolves the money and runs one DB transaction with the insert and
   the balance update.
5. After the commit, audit, budget notification and cashback run (not atomic with the write).
6. API answers 201, or 200 for a replay; the client retries with the same key if the answer was lost.

**Read:** gateway (retries allowed) → JWT, session, membership → SELECT scoped by `household_id` → 200.

**Telemetry:** the same `request_id` appears in the gateway JSON log, the API JSON log and `app_logs`; Prometheus
scrapes each pod; alert rules are evaluated in Prometheus; Grafana reads Prometheus.

## 6. Redundancy and backup strategy

| Layer | Strategy | Protects against | Does not protect against |
|---|---|---|---|
| Gateway, API | Active-active ×2, preferred anti-affinity | Loss or hang of one pod | Node loss (one node); shared DB failure |
| PostgreSQL | **No replica.** Restart in place, data on a PVC | Process crash | Volume loss, logical corruption |
| Data | Logical backup: `pg_dump -Fc`, restore with `pg_restore --clean --if-exists` | Volume loss, bad write | Writes after the last dump (RPO = time since the dump) |
| Backup immutability | Object-lock copy with a sha256 check was shown on the earlier lab (`ftr-a4/scripts/ransomware-drill.sh`) | Deletion of the backup | Not deployed in this lab |
| Release | Rolling update, `maxUnavailable 0`, `kubectl rollout undo` | Bad release | Bad migration (no automatic down-migration) |

Earlier measurement (`ftr-a3/evidence/08-dr-drill.txt`, a different namespace and a very small database): dump 0.98 s;
StatefulSet and PVC deleted, then recreated and restored in 19.2 s; API answered 200 afterwards. A restore time was not
measured in the final lab: Exp 8 as run is a release rollback. Backups here are manual, not scheduled.

## 7. Single points of failure

| SPOF | Scope | Status |
|---|---|---|
| PostgreSQL | All critical functions | Accepted for the lab; the main residual risk |
| kind node, host | Everything | Out of scope |
| Prometheus, Grafana | Monitoring only | Accepted |
| JWT secret | Security | One key, no rotation |
| kube-dns | Gateway discovery of new pods | Partial |

The full list with effects is in `requirements.md` §8.

## 8. Baseline vs improved

From `k8s/baseline/api-baseline.yaml`, `k8s/30-api.yaml` and `k8s/40-gateway.yaml`. Both use the same image and share
one PostgreSQL, so **the database has no baseline/improved difference**. The application mechanisms (rollback,
idempotency, rate limits, validation) exist in both; the idempotency benefit depends on the client sending `client_id`.

| Mechanism | Baseline (`api-baseline`) | Improved (`gateway` + `api`) |
|---|---|---|
| API replicas | 1 | 2, `maxUnavailable 0`, `maxSurge 1` |
| Entry | Direct NodePort 30081 | Gateway ×2, NodePort 30080 |
| Timeouts at the edge | None; the client's timeout applies | Connect 1 s, read 5 s |
| Bounded retries | None | 2 tries; not for a POST already sent |
| Passive ejection | None | `max_fails=1 fail_timeout=2s` |
| Failure answer | Hang, connection reset or API 500 | JSON 503 with `Retry-After` |
| Liveness probe | **None** | `/health`, 10 s × 3 |
| Readiness probe | `/health`, 2 s, default threshold 3 | `/health`, 2 s, threshold 1, timeout 1 s |
| Graceful drain | None (default grace period) | preStop `sleep 5`, grace period 20 s |
| Pod hardening | No security context in the manifest | Non-root, read-only root filesystem |
| Anti-affinity | None | Preferred |
| Client IP | Trusts client-supplied `X-Real-IP` (Exp 7) | Overwritten at the gateway |
| Correlation id | API id only; adopts the client's header | Gateway id, same in both logs |
| Body cap at the edge | None (API 1 MB decode, 16 MB ceiling) | 2 MB, JSON 413 |
| Alerts | **None of its own** (scraped as `api-baseline`) | 3 rules on `job="api"` |
| Network policy | Own ingress policy, same DB access | Gateway and Prometheus to API only |

Checked against `evidence/test-plan.md` and the manifests as of the runs of 06.11.2026. One gateway change since the endterm: the log also records `$upstream_status`. One shared change: the Postgres memory limit went from 256Mi to 1Gi (dated amendment), for both configurations.

## 9. Resource and load assumptions

| Item | Value |
|---|---|
| API | requests 50m / 64 Mi; limits 500m / 256 Mi |
| Gateway | requests 50m / 32 Mi; limits 300m / 128 Mi |
| PostgreSQL | requests 50m / 128 Mi; limits 500m / 1 Gi. At 256 Mi it was OOM-killed in the endterm and in the first run of Exp 8 (`evidence/anomalies.txt`) |
| DB connections | pool maximum 40 per replica; up to 80 + baseline 40 against the PostgreSQL default of 100 |
| Reference load | 30 req/s (Exp 5, one sample per run, see `results/exp5-*.json`) |
| Scaling | Manual; no metrics-server, so no HorizontalPodAutoscaler |
