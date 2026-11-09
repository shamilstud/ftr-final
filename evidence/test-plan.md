# Test plan: final project experiments

Written before the runs, on 2026-11-06. Numbers in the hypotheses are predictions, not results.
Measured values are in `results/summary.md` and `results/*.json`.

## Common to all experiments

**Environment.** One-node kind cluster `ftr-endterm` on Docker Desktop (Windows 11 laptop, WSL2), namespace
`university`. All traffic comes from one Python client on the same machine, through `127.0.0.1` NodePorts. The hoWallet
Go API image `howallet-api:endterm` stands in for the university records service. All data is synthetic.

**Configurations.**

| | baseline (`k8s/baseline/api-baseline.yaml`) | improved (`k8s/30-api.yaml`, `k8s/40-gateway.yaml`) |
|---|---|---|
| API replicas | 1 | 2, `maxUnavailable: 0`, `maxSurge: 1` |
| entry point | direct NodePort 30081 | nginx gateway x2, NodePort 30080 |
| gateway timeouts and retries | none | connect 1 s, read 5 s, at most 2 tries, idempotent methods only |
| degraded reply | none | JSON 503 with `Retry-After` |
| correlation id | only when the client sends one | adopted or minted by the gateway, shared with the API |
| readiness probe | every 2 s, `failureThreshold` 3 | every 2 s, `failureThreshold` 1 |
| liveness probe, preStop | none | liveness 10 s x 3, preStop `sleep 5` |
| alert rules (`job="api"`) | none of its own | ApiHighErrorRate, ApiHighLatencyP95, ApiReplicaDown |

Both share one Postgres and the same application code, so `client_id` idempotency and database transactions are present
in **both**. The comparison measures the infrastructure mechanisms, not the application's.

**One change from endterm.** The gateway log now also records `$upstream_status`, so retries can be counted. No timeout
or retry setting changed.

**Rules for every run.**
- Each run starts from a known state: all workloads ready, no pending or firing alerts.
- Each run records whether the fault was really in place. A run where it was not, or which was cut off, is kept, marked
  INVALID, excluded from results and rerun.
- Client retries use the same `client_id`.

**Recorded.** Fault, start and end, detection time, recovery time, successful and failed requests, latency p50/p95/max,
retries and failovers, data-integrity result.

## Amendment, 2026-11-06 16:10, after the first runs

The first run of experiment 8 (improved) failed for a reason other than its fault. About 2 s after the broken release started,
database queries on the healthy API pods began to hang, and Postgres was later OOM-killed at its 256Mi limit (peak 268 MB,
68 connections). The endterm had also seen a Postgres OOM kill. That run measured the memory limit, not the rollout.

**Change.** The Postgres memory limit goes from 256Mi to 1Gi in `k8s/10-postgres.yaml`. CPU limit and all resilience settings stay
the same. This applies to both configs, because they share the database.

**Consequences.**
- The confounded run is kept as `results/exp8-improved-rep1-INVALID-confounded-postgres-oom.json`.
- The runs made under 256Mi (exp3 and exp4, rep 1 of each config) are valid measurements of a different environment. They move to
  `results/pre-pg-memory-fix/`, are not used in the summary, and all repetitions are rerun, so every result shares one environment.
- `run_exp.py` now stops and writes `anomalies.txt` if Postgres is OOM-killed during a run.

**Hypotheses are unchanged**, including the exp3 baseline prediction of 8-10 s per request, which the set-aside run already
contradicted (about 15 s average, 30.8 s max, 46 % failed). Experiment 8 now also tests this explanation: if it is right, the same
broken release shows no such failure at 1Gi.

## Amendment, 2026-11-06 17:10, after the first full pass

Two measurement defects in my scripts, found while checking results. Neither changes a hypothesis or a criterion.

- **Experiment 1 stop condition.** The run stopped 15 s after endpoints returned. In two baseline runs, requests were still timing
  out when the probe stopped, so the "23 s" was window + client timeout, a lower bound. The endterm's E1 figure has the same defect. The
  run now also waits until clients have 14.5 s of clean answers (limit 180 s) and records `clients_settled_before_stop`. All six runs
  are repeated; the old ones move to `results/superseded/`.
- **Experiment 6 clear time.** The alert watcher could stop just before seeing an alert clear. It now takes a last look. The two latency
  runs are repeated, because clearing is part of their criterion; the old ones move to `results/superseded/`.

**Not changed.** In experiment 7, the case `B GET /api/households/{A}` expected 404 and receives 405 because that route has no GET. The
expectation is my test's defect, but it was written before the run, so the case stays counted as failed and is explained in the summary.

## Experiment 1: application instance failure

- **Question.** Does another instance or recovery mechanism keep the critical workflow available?
- **Hypothesis.** Improved: at most a few failed requests per pod loss, because the gateway retries idempotent requests on
  the other replica. Baseline: an outage lasting until the replacement pod is ready.
- **Fault.** Delete one API pod: graceful (`kubectl delete pod`) or forced (`--grace-period=0 --force`).
- **Workload.** `GET /api/accounts`, open loop, 8 req/s.
- **Duration.** 5 s before the fault, then until the replacement has been in endpoints for 15 s (limit 120 s).
- **Repetitions.** 3 per config: graceful, graceful, forced.
- **Baseline vs improved.** One replica without a gateway vs two replicas behind gateway retries.
- **Success criteria.** Improved availability at least 99 % after injection, and at least 99.9 % for the two graceful runs.
  Baseline has no target and is measured for comparison.

## Experiment 2: database unavailable

- **Question.** Does the system fail safely, recover, and avoid corrupting data?
- **Hypothesis.** Both configs fail during the outage and recover without intervention, with no lost or duplicate write. Under
  a frozen database, the improved config answers within its bound (about 10 s) while baseline requests wait until the client
  gives up (12 s). ApiHighErrorRate or ApiHighLatencyP95 fires.
- **Fault.** `stop`: scale the Postgres StatefulSet to 0. `freeze`: SIGSTOP on every Postgres process. Held for 20 s.
- **Workload.** `GET /api/accounts` at 6 req/s per config, timeout 12 s, plus one logical create per 1.5 s per config, retried with
  the same `client_id` until acknowledged (limit 120 s).
- **Duration.** 8 s before, 20 s fault, then until both probes have been all-200 for 15 s (limit 180 s).
- **Repetitions.** 4 runs (2 stop, 2 freeze). Both configs are probed at once in every run, under the same fault.
- **Baseline vs improved.** Same outage; the difference is the error reply, gateway bound and alerts.
- **Success criteria.** Every acknowledged write has exactly one row, balance equals minus the sum of amounts, recovery within
  30 s of the restore command, improved errors are JSON. An alert fires (improved).

## Experiment 3: network delay or timeout (new)

- **Question.** Are timeout and retry policies bounded and effective?
- **Hypothesis.** Improved: requests hitting the slow replica are cut at the 5 s read timeout and retried once on the healthy
  replica, so success stays at 99 % or more and no request exceeds the 12 s configured bound (2 x (1 s connect + 5 s read)).
  `/health` does not touch the database, so readiness will **not** remove the slow replica; only the gateway's passive
  check (`max_fails=1`, `fail_timeout=2s`) steers traffic away. Baseline: every request takes about 8-10 s, because nothing
  bounds the wait except the API's own 15 s `WriteTimeout`.
- **Fault.** `tc netem delay 2000ms` on egress to port 5432 inside the network namespace of one API pod (the only pod for
  baseline), so every database round trip from that pod takes 2 s longer. A pilot with 500 ms on the baseline pod (5 requests)
  gave 2.0-4.5 s per request, that is 4-5 round trips per request; 2000 ms was chosen so a request exceeds the 5 s read timeout.
  The pilot is calibration only and is not a result.
- **Workload.** `GET /api/accounts` at 2 req/s, client timeout 60 s so the client is not the bound, plus one logical create
  per 1.5 s retried with the same `client_id`. The rate keeps expected concurrency below the 40-connection database pool.
- **Duration.** 10 s before, 40 s fault, 30 s after.
- **Repetitions.** 3 per config.
- **Baseline vs improved.** Same delay on the only replica vs one of two replicas behind gateway timeouts and retries.
- **Success criteria (improved).** netem counters show delayed packets; availability at least 99 %; max latency at most 12 s; at
  most 2 tries per request in the gateway log; every acknowledged write has one row and a correct balance; latency back to normal
  within 30 s of removal. Baseline has no target; the question is whether anything bounds its wait.

## Experiment 4: interrupted multi-step transaction (new, extends endterm E7)

- **Question.** Can the system recover without losing or duplicating a critical record?
- **Hypothesis.** A process killed between the two steps of the create (insert row, move balance) leaves nothing behind:
  Postgres rolls the transaction back, and the retry with the same `client_id` produces exactly one row and the right
  balance, in both configs. Improved: the retry succeeds in about 1-2 s through the other replica. Baseline: only after the
  container restarts.
- **Fault.** A separate session holds `FOR NO KEY UPDATE` on the account row, so the foreign-key check on the INSERT passes
  but the balance UPDATE blocks. Once `pg_stat_activity` shows the API backend waiting on that UPDATE, the API process that
  owns the backend gets SIGKILL on the node. The lock is released 1 s later. `pg_locks` is saved at the moment of the kill as
  proof the transaction was mid-way.
- **Workload.** 2 normal creates, then the interrupted create with retries every 0.5 s, plus background `GET /api/accounts` at
  4 req/s and one create per 1.5 s on another household's account.
- **Duration.** 6 s before the kill, then 30 s after it.
- **Repetitions.** 3 per config.
- **Baseline vs improved.** Same kill; one replica without a gateway vs a surviving replica. nginx never replays a POST in
  either config, so the client's retry does the recovery.
- **Success criteria.** Exactly 1 row for the interrupted `client_id`, 3 rows on the account, balance equal to minus the sum,
  no duplicate `(household_id, client_id)` anywhere in the database, background writes intact. Retry succeeds within 60 s
  (improved target: within 10 s).

## Experiment 5: high load

- **Question.** How do throughput, latency and errors change?
- **Hypothesis.** At 30 req/s the improved config serves everything with p95 under 500 ms and pays a small gateway cost in
  latency at low load. Baseline also holds this rate, because the step is below the single pod's CPU limit, so the difference may
  be small.
- **Fault.** None: load step 5 -> 30 -> 5 req/s.
- **Workload.** `GET /api/accounts`, 5 users, open loop, timeout 6 s. 429 is counted separately.
- **Duration.** 30 s + 60 s + 30 s.
- **Repetitions.** 2 per config.
- **Baseline vs improved.** One replica vs two behind the gateway.
- **Success criteria.** Availability at least 99 % excluding 429; p95 below 500 ms in the step phase; no alert fires.

## Experiment 6: monitoring and alert trigger

- **Question.** Can the incident be detected and diagnosed from available telemetry?
- **Hypothesis.** Each fault fires the alert meant for it, within about 45 s, and no alert fires in a quiet window. Other
  alerts may fire as a consequence of the same fault; these are reported rather than hidden. Baseline has no alert rules of its own,
  so this experiment measures the improved config only.
- **Faults.** Hung replica (SIGSTOP on one API process) -> ApiReplicaDown. Database stopped 25 s -> ApiHighErrorRate. 22 lock
  bursts on `accounts` over about 40 s -> ApiHighLatencyP95. Quiet window: no fault.
- **Workload.** `GET /api/accounts` through the gateway at 6 req/s (quiet window: 5 req/s), Prometheus polled every 1 s.
- **Duration.** Quiet 120 s. Faults: until the alert has cleared (limit 150-200 s).
- **Repetitions.** Quiet 2, replica 3, error rate 2, latency 2.
- **Success criteria.** Expected alert reaches `firing` within 45 s of injection and clears after recovery. Zero alerts in the
  quiet windows.

## Experiment 7: unauthorized or invalid operation

- **Question.** Are access controls and validation enforced, and is the event logged?
- **Hypothesis.** Missing or forged tokens get 401, another tenant's data gets 404, malformed and oversized input gets 400/413,
  repeated wrong passwords get 429, the victim's data does not change, and each request has a gateway and an API log line with the same
  request id. The client-supplied `X-Real-IP` reaches the API directly on baseline but is overwritten by the gateway.
- **Fault.** None: 11 access-control cases (user `other` attacks user `main`), then input and limit cases, then 12 wrong-password
  logins.
- **Workload.** Sequential requests; a 5 req/s probe runs during the input cases.
- **Duration.** About 1 minute per set.
- **Repetitions.** Each set twice, 70 s apart, with a fresh account for the lockout (counters live in API memory).
- **Baseline vs improved.** Cases go through the gateway; only the `X-Real-IP` case compares the two. The application's
  controls are identical in both.
- **Success criteria.** Each case returns its expected status, an md5 snapshot of the victim's accounts and transactions is
  unchanged, every access-control request has both log lines, and the X-Real-IP spoof is not honoured through the gateway.

## Experiment 8: recovery, rollback (new)

- **Question.** Does the system return to an acceptable state and meet defined acceptance criteria?
- **Hypothesis.** A release that listens on the wrong port (`API_PORT=9999`) never becomes ready, so it receives no traffic and the
  old pods keep serving; `kubectl rollout undo` restores the previous template. Improved: no failed request. Baseline:
  **probably the same**, because a default RollingUpdate with 1 replica rounds `maxUnavailable` down to 0 and `maxSurge` up to 1.
  If so, this mechanism adds nothing over the Kubernetes default, and that gets reported. Monitoring is expected **not** to
  notice the stuck rollout, because no alert watches rollouts.
- **Fault.** `kubectl set env deploy/<d> API_PORT=9999`, left for 45 s, then `kubectl rollout undo`.
- **Workload.** `GET /api/accounts` at 5 req/s, timeout 12 s, plus one create per 1.5 s with `client_id`.
- **Duration.** 10 s before, 45 s broken, undo, 15 s after.
- **Repetitions.** 2 per config.
- **Baseline vs improved.** Same broken release; 1 vs 2 replicas, with and without gateway.
- **Acceptance criteria.** Availability at least 99 % across the fault window; the bad pod is never ready; after the undo the
  template hash, image and environment equal the originals; recovery within 60 s of the undo; every acknowledged write has one row and the
  balance is correct; no duplicates in the database.
