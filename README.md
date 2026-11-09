# FTR Final Project: dependable API with gateway, metrics, alerts and fault injection

Final Project of the course "Fault Tolerance and Dependable Computing". Builds on the endterm lab. Old endterm results
are in `evidence/endterm-results/`, for reference only; the final report uses `evidence/results/`.

Individual work: Shamil Yakupov, ID 255193. Report, slides and demo runbook are in `report/`; secrets in the manifests are
synthetic lab placeholders. The API image `howallet-api:endterm` is built from the hoWallet repository at commit `06aea177`
plus the opt-in `/metrics` patch (commit `60cd5791`, branch `feat/metrics`), using `deployments/Dockerfile.api`. The
application source and the patch are private and not part of this repository; both are available to the instructor on request.

| Deliverable | Where |
|---|---|
| Final report | `report/Final_FTR_Yakupov_255193.docx`, `.pdf` (`python report/build_report.py`) |
| Slides | `report/Final_FTR_Yakupov_255193.pptx` (`report/build_deck.js`) |
| Demo runbook | `report/demo-runbook.md` |
| Reliability calculations | `python analysis/reliability.py --json-out analysis/reliability-computed.json`, explained in `notes/reliability.md` |
| Requirements, fault and threat models, architecture | `notes/` |

## Reproduce the experiments

Needs Docker, kind, kubectl, Python 3 and Node (Playwright from the repo's `web/`, for screenshots only).

```bash
bash scripts/up.sh --baseline     # create the cluster, load images, apply manifests, wait for Ready
python scripts/setup_users.py     # synthetic users; tokens go to the git-ignored evidence/lab-users.json (valid 24 h)
```

Each command runs every repetition of one experiment. Its readable record is appended to `evidence/expN.txt`, and its
raw data lands in `evidence/results/expN-*.json`. Add repetition numbers to run only those, for example
`python scripts/run_exp.py 3 2`.

| # | Scenario | Command | Script and function | About |
|---|---|---|---|---|
| 1 | Application instance failure | `python scripts/run_exp.py 1` | `faults.py` `e1_once` | 10 min |
| 2 | Database unavailable | `python scripts/run_exp.py 2` | `faults.py` `e2_once` | 6 min |
| 3 | Network delay or timeout | `python scripts/run_exp.py 3` | `final.py` `exp3` | 10 min |
| 4 | Interrupted multi-step transaction | `python scripts/run_exp.py 4` | `final.py` `exp4` | 4 min |
| 5 | High load | `python scripts/run_exp.py 5` | `exps.py` `e3` | 9 min |
| 6 | Monitoring and alert trigger | `python scripts/run_exp.py 6` | `exps.py` `e4*` | 15 min |
| 7 | Unauthorized or invalid operation | `python scripts/run_exp.py 7` | `exps.py` `e5`, `e6` | 6 min |
| 8 | Recovery and rollback | `python scripts/run_exp.py 8` | `final.py` `exp8` | 7 min |

`bash run_all.sh` runs all eight, then builds the summary. Rerunning a repetition overwrites its JSON.

| Output | Built by | Contents |
|---|---|---|
| `evidence/test-plan.md` | written before the runs | hypothesis, workload, duration and criteria per experiment |
| `evidence/results/summary.md` | `python scripts/build_summary.py` | consolidated table with pass or fail |
| `evidence/results/reliability-inputs.json` | same | raw inputs for MTTF, MTBF, MTTR and availability, with field descriptions |
| `evidence/log-samples.txt` | `python scripts/log_samples.py "<title>" <id prefix> <n> [retried\|failed\|any]` | gateway and API lines with one request id; run it before the pods restart |
| `evidence/screenshots/` | `AlertWatcher` during runs (Prometheus), `python scripts/grafana_shot.py <result name>` (Grafana) | dashboards and firing alerts. The dashboard shows `job="api"` only, so it has no baseline traffic and baseline numbers come from the result files |

A run that did not really inject its fault is renamed `*-INVALID-*.json` or saved with `injection_verified: false`. Either
way `build_summary.py` leaves it out and lists it under "Excluded runs".

`final.py` and two faults reach into the kind node with `docker exec` (`nsenter`, `tc`, `kill`), so they work only on kind.
The cluster keeps its endterm name, `ftr-endterm`.

**Differences from the endterm lab:** the config label `endterm` is now `improved`; the gateway log also records
`$upstream_status`, so retries can be counted; the Postgres memory limit is 1Gi instead of 256Mi, which was OOM-killed under normal lab
load (`evidence/anomalies.txt`, amendment in `evidence/test-plan.md`). No timeout or retry value changed.


A local Kubernetes lab (kind) demonstrating fault-tolerance and observability
patterns in front of a synthetic university service. The service itself is the
hoWallet Go API, run only as a stand-in application; **no application source is
modified here** and **all secrets in this repo are synthetic lab placeholders**.

## Architecture

```
            host :30080                 host :30090      host :30300
                │                            │                │
          ┌─────▼─────┐                ┌─────▼─────┐   ┌──────▼──────┐
          │  gateway  │   scrape       │Prometheus │   │   Grafana   │
          │ nginx x2  │◄───────────────│  (2s)     │──►│ (dashboard) │
          └─────┬─────┘                └─────┬─────┘   └─────────────┘
         proxy  │ (retries, 503,              │ dns_sd per pod
          req-id│  correlation id)            │ /metrics
          ┌─────▼─────┐   ◄─────────────────  │
          │    api    │  (2 replicas, hardened, preStop, probes)
          │  x2 :8080 │
          └─────┬─────┘
                │ 5432
          ┌─────▼─────┐
          │ postgres  │  (StatefulSet, migrated by a Job)
          └───────────┘

baseline (midterm): api-baseline x1  ──direct NodePort :30081──►  (no gateway)
```

Namespace: `university`.

## Ports (use `127.0.0.1`, not `localhost`, on Windows)

| URL | What |
|---|---|
| http://127.0.0.1:30080 | Gateway – the single API entry point |
| http://127.0.0.1:30090 | Prometheus |
| http://127.0.0.1:30300 | Grafana (anonymous Admin, lab only) |
| http://127.0.0.1:30081 | Baseline API (only with `--baseline`) |

## Cluster commands

```bash
bash scripts/up.sh              # main stack
bash scripts/up.sh --baseline   # also deploy the midterm baseline
bash scripts/down.sh            # delete the cluster
```

`up.sh` creates the kind cluster `ftr-endterm`, loads images, applies the
manifests in order (namespace → postgres → migrate Job → api → gateway →
prometheus → grafana → network policies), and waits for readiness.

### Images

- **api**: `howallet-api:endterm` (branch `feat/metrics`, exposes `GET /metrics`
  when `METRICS_ENABLED=true`). If that image is not built yet, `up.sh` falls
  back to `howallet-registration:ftr` (no `/metrics`) so the rest of the stack
  can be validated. Switch over later with:
  ```bash
  bash scripts/switch-api-image.sh
  ```
- **migrate**: `ftr-migrate:endterm` (repo migrations baked in). If absent,
  `up.sh` builds it from `Dockerfile.migrate` against the repo root.
- **monitoring**: `prom/prometheus:v2.54.1`, `grafana/grafana:11.2.0` (pinned).

## Getting an auth token

`POST /auth/register` answers 201 with a token, but the account has
`email_verified=false` and household routes answer 403 for it. The lab has no
mail server, so the script marks the synthetic account verified in Postgres
and logs in again:

```bash
bash scripts/get-token.sh          # register -> UPDATE users SET email_verified=true -> login
TOKEN=$(cat evidence/token.txt)    # git-ignored
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:30080/api/households
HH=$(curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:30080/api/households | python -c 'import sys,json;print(json.load(sys.stdin)[0]["id"])')
curl -s -H "Authorization: Bearer $TOKEN" -H "X-Household-ID: $HH" http://127.0.0.1:30080/api/accounts
```

## Metrics (names are fixed)

- `http_requests_total{method,route,status}` – counter
- `http_request_duration_seconds_bucket{method,route,le}` – histogram
- `http_requests_in_flight` – gauge
- `db_pool_acquired_conns`, `db_pool_total_conns` – DB pool (may be absent)

## Alerts (Prometheus, names are fixed)

| Alert | Expr (summary) | For | Severity |
|---|---|---|---|
| `ApiHighErrorRate` | 5xx rate / total rate > 0.05, `job="api"`, `route!="/health"` | 10s | critical |
| `ApiHighLatencyP95` | p95 of request duration > 0.5s, `job="api"`, `route!="/health"` | 10s | warning |
| `ApiReplicaDown` | `(count(up{job="api"} == 1) < 2) or absent(up{job="api"} == 1)` | 6s | critical |

## Grafana dashboard

"hoWallet API – Endterm" (uid `howallet-endterm`), refresh 5s, last 15m:
request rate by status, error rate %, in-flight, latency p50/p95/p99, replicas
up per instance, firing alerts, DB pool connections.

## Gateway behaviour

- `proxy_connect_timeout 1s`, `proxy_read_timeout 5s`
- Upstream = the per-pod A records of `api-headless` (`server ... resolve`,
  nginx >= 1.27.3), not the single ClusterIP: with one server nginx has no
  "next upstream", so retries could never reach the other replica.
- `client_max_body_size 2m`; an oversized body gets a JSON 413.
- `X-Real-IP` is overwritten and `True-Client-IP` cleared: the API reads the
  client IP from True-Client-IP > X-Real-IP > X-Forwarded-For, so passing them
  through would let a client spoof its IP past rate limits and login lockout.
- Bounded retries: `proxy_next_upstream error timeout http_502 http_503 http_504`,
  `proxy_next_upstream_tries 2`. Only idempotent methods are retried (nginx
  default – `non_idempotent` is deliberately **not** added, so a POST register
  is never replayed).
- Correlation id: `X-Request-Id` is adopted if present, otherwise nginx mints
  one; it is forwarded to the API, whose chi `RequestID` middleware adopts it,
  so the same id appears in both gateway and API logs.
- JSON access log with `request_id`, `status`, `upstream_addr`, `request_time`,
  `upstream_response_time`.
- Graceful degradation: a dead upstream returns a JSON 503 with `Retry-After`,
  not nginx's HTML error page.

## Network policies

Default-deny ingress, then: gateway→api:8080, prometheus→api:8080,
api/baseline/migrate→postgres:5432, NodePort ingress to gateway/grafana/
prometheus, and an api egress allowlist (DNS + postgres + external HTTPS for
the Frankfurter exchange-rate provider). kind's `kindnet` CNI enforces these.

`k8s/optional/block-egress.yaml` replaces the api egress policy (same name) to
cut external HTTPS for a graceful-degradation experiment; re-apply
`k8s/70-network-policy.yaml` to restore.

## Baseline vs hardened

| | baseline (`api-baseline`) | hardened (`api`) |
|---|---|---|
| replicas | 1 | 2, `maxUnavailable: 0` |
| preStop drain | no | `sleep 5` |
| entry | direct NodePort 30081 | gateway (retries, 503, req-id) |
| Prometheus job | `api-baseline` | `api` |

## API settings worth knowing

- `enableServiceLinks: false` on the api pods: a Service named `api` makes
  Kubernetes inject `API_PORT=tcp://<ip>:80`, which the API reads as its listen
  address and crashes on.
- `SALES_PAUSED=true`: otherwise free-plan limits (1 household per user, 150
  records) answer 403 `plan_limit_reached` during load tests.
- `METRICS_ENABLED=true` turns on `GET /metrics`.

## Known limitations

- `/health` does not touch the database. Fine for liveness, but readiness
  stays green while Postgres is down; the DB failure shows up as 5xx instead.
- All host traffic reaches nginx from one SNAT address (the kind node), so the
  API sees one client IP and per-IP rate limits (10/min on `/auth`, 1200/min
  overall) are shared by every load-test client.
- The error-rate and latency alerts were first unscoped (baseline traffic and
  kubelet `/health` probes counted). Before the experiments they were
  restricted to `job="api"` and `route!="/health"` (`k8s/50-prometheus.yaml`,
  reloaded with `kill -HUP 1` in the Prometheus pod; evidence
  `05-alert-rules.txt`). The baseline therefore has no error/latency alert of
  its own.
- `ApiReplicaDown` was originally `up{job="api"} == 0`. Experiments E1 (forced
  pod kill) and E4 rep1 (hung process, SIGSTOP) showed it can never fire here:
  a dead or unready pod leaves the headless-Service DNS within ~2 s, so its
  Prometheus target disappears (series goes stale) instead of staying at
  `up == 0` for the 6 s `for`. It went pending and cleared on its own while a
  replica was down for ~47 s. It now counts healthy replicas
  (`k8s/50-prometheus.yaml`, evidence `05-alert-rules.txt`, `e4-alerts.txt`).
- Kubelet `/health` probes are counted in `http_requests_total`.
- `kind load` of public images sometimes fails on Docker's containerd store
  ("content digest ... not found"); `up.sh` then lets the node pull them.

## Evidence

See "Reproduce the experiments" above. `scripts/rec.sh <name> <command...>` saves `$ command` and its output to
`evidence/<name>.txt`.
