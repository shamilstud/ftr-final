# Demo runbook: Final Project, Fault Tolerance and Dependable Computing

Shamil Yakupov, ID 255193. Controlled local lab only: one kind cluster on the author's laptop, namespace `university`,
synthetic data, no production system. Expected results below are measured values from 6 November 2026
(`evidence/results/summary.md`). A live demo is one more run on one machine, so it can differ from the ranges. Say so if
it does; do not explain a difference away.

Commands are for Git Bash, from `AITU/coursework/ftr-final/`. Use `127.0.0.1`, not `localhost`.

## 1. Before the demo (about 5 minutes)

Commit the lab state first, so anything the demo overwrites can be restored:

```bash
git status --short                 # must be empty
kubectl -n university get pods     # gateway x2, api x2, api-baseline, postgres-0, prometheus, grafana: all Running
python scripts/demo.py status      # pods, URLs, "Alerts now: none"
python scripts/setup_users.py      # only if evidence/lab-users.json is older than 24 h
```

Open three things and leave them open:

| Window | Address or command |
|---|---|
| Grafana dashboard, refresh 5 s | http://127.0.0.1:30300/d/howallet-endterm |
| Prometheus alerts | http://127.0.0.1:30090/alerts |
| Terminal | large font, this folder |

Have ready as a recorded fallback if the lab misbehaves: `evidence/exp1.txt`, `exp2.txt`, `exp4.txt`, `exp7.txt`,
`evidence/screenshots/`, and the raw `evidence/results/exp*.json`.

Timing rules, so scenarios do not disturb each other:

- Wait until `python scripts/demo.py status` shows all pods Ready and no alert before each scenario.
- Do not kill the same API container repeatedly. A killed container enters kubelet's restart back-off (10 s, 20 s, 40 s, ...),
  which measurably lengthened recovery in Exp 4. Scenarios 1 and 3 are about 10 minutes apart if possible.
- `/auth` allows 10 requests per minute per IP per replica. Each `demo.py` command logs in, so do not run them in a tight loop.

## 2. Scenario 1: application instance failure (Exp 1)

**Point to make:** another instance keeps the workflow available.

```bash
python scripts/demo.py failure
```

**On screen:** the terminal's one line per second (`ok`, `failed`, `ready api pods`); Grafana "Replicas up"; the final
`RESULT:` line.

| Measured | Improved | Baseline |
|---|---|---|
| Requests OK after injection | 418/418 over 3 runs | 538/840 over 3 runs |
| Endpoint drop | 0.2–0.3 s | 0.2 s |
| Recovery | 1.3–1.7 s, outage 0.0 s | 25.0–25.6 s outage graceful, 2.0 s forced |
| p95 | 47.4–151.2 ms | 1244.9–6016.2 ms |

**Expected:** ready pods go 2 → 1 → 2, no failed request, a p95 that may rise while the pod is replaced.

**Optional baseline contrast** (costs about 30 s; a graceful delete of the single replica):

```bash
kubectl -n university delete pod -l app=api-baseline --wait=false
for i in $(seq 1 40); do curl -s -o /dev/null -m 6 -w "%{http_code} %{time_total}s\n" http://127.0.0.1:30081/health; sleep 1; done
```

`/health` does not touch the database, so this loop shows only the pod coming back. To see the client-visible outage,
show the recorded Exp 1 baseline in `evidence/exp1.txt` (25.0–25.6 s although endpoints returned in 0.9–1.1 s). The cause of
that gap is not established; say so.

**Restore:** nothing to do. The ReplicaSet replaced the pod.

```bash
kubectl -n university rollout status deploy/api
kubectl -n university get pods -l app=api
```

## 3. Scenario 2: database outage (Exp 2)

**Point to make:** the system fails safely and recovers without intervention. **Not** that it is more available: it is not.

Terminal 1, a simple sequential probe through the gateway:

```bash
export TOKEN=$(python -c "import sys; sys.path.insert(0,'scripts'); import demo; print(demo.login('main')['token'])")
export HH=$(python -c "import sys; sys.path.insert(0,'scripts'); import demo; print(demo.load_users()['main']['household_id'])")
while true; do curl -s -o /dev/null -m 12 -w "%{http_code} %{time_total}s\n" \
  -H "Authorization: Bearer $TOKEN" -H "X-Household-ID: $HH" http://127.0.0.1:30080/api/accounts; sleep 0.5; done
```

Terminal 2, the fault, held for about 20 s:

```bash
kubectl -n university scale statefulset postgres --replicas=0
curl -si -m 12 -H "Authorization: Bearer $TOKEN" -H "X-Household-ID: $HH" http://127.0.0.1:30080/api/accounts   # show the JSON error
```

**On screen:** statuses change to 500 and 503, response times stop at about 5 s or 10 s, or are immediate once `max_fails=1`
marks both replicas down; the JSON 503 with `Retry-After: 2` and `request_id`; Grafana latency; Prometheus alerts going
pending, then firing. "Replicas up" stays at 2 because `/health` does not touch the database.

| Measured | Improved | Baseline |
|---|---|---|
| OK during the outage | 40/522 (7.663 %) | 182/489 (37.219 %) |
| p95 / max | 5012.1–9179.7 ms / 10010.5–10015.8 ms | 12013.4–12014.4 ms / 12020.2–12031.8 ms |
| Alerts firing | 15.2–21.2 s | no rule of its own |
| Recovery after restore | 4.0–12.3 s | 3.2–8.0 s |
| Writes | 146 acknowledged, 146 rows, 0 duplicates | 143, 143, 0 |

**Say explicitly:** the baseline's success ratio is higher because its requests wait up to the 12 s client timeout and some
finish after the restore. The gain from the improved configuration is bounded, signalled failure, at the cost of fewer late
successes and slower recovery.

**Restore:**

```bash
kubectl -n university scale statefulset postgres --replicas=1
kubectl -n university rollout status statefulset/postgres --timeout=120s
```

Watch terminal 1 return to 200, then stop it with Ctrl+C. Wait for the alerts to clear (53–58 s after injection in Exp 6) before
the next scenario:

```bash
python scripts/demo.py status      # "Alerts now: none"
```

## 4. Scenario 3: interrupted transaction (Exp 4)

**Point to make:** a process killed between the two steps of a create leaves nothing behind, and the retry with the same
`client_id` gives exactly one row.

This runs the real experiment once, improved configuration, repetition 2. **It overwrites
`evidence/results/exp4-improved-rep2.json`, so the file is restored from git afterwards. Never commit the demo's copy.**

```bash
python scripts/final.py exp4 improved 2
```

**On screen:** the terminal log: the kill, the attempts (`503`, then `201`), and the integrity block. Then show the same
facts in the raw result:

```bash
python -c "import json; d=json.load(open('evidence/results/exp4-improved-rep2.json')); print({k:d[k] for k in ('insert_done_at_kill','committed_rows_visible_at_kill','client_attempts','final_status','duplicates_db')}); print(d['integrity_target']); print(d['postgres_log'][:1])"
```

| Measured | Improved | Baseline |
|---|---|---|
| State at the kill | insert done, 0 committed rows visible | same |
| Client tries | 2 | 5–7 |
| Retry succeeds | 1.2–1.7 s | 3.6 s first crash, then 22.2 and 34.1 s (likely kubelet back-off) |
| Probe after the kill | 376/376 | 277/400 |
| Rows for the interrupted id | 1 | 1 |
| Balance, duplicates | balance = −Σ, 0 duplicates | same |

**Say explicitly:** integrity is the same in both configurations because the transaction and the idempotency key live in the
application. The improved configuration changes retry time and what other clients see. ApiReplicaDown may fire here; it did in
2 of the 3 runs, correctly.

**Restore:**

```bash
git restore evidence/results/exp4-improved-rep2.json
git status --short                 # empty; the run also left a few synthetic accounts in the lab database, which is harmless
kubectl -n university get pods -l app=api
```

Should the real experiment be too slow or fail, the shorter fallback is `python scripts/demo.py integrity`: 50 idempotent writes
with a pod deleted in the middle, then a SQL count (about 1 minute). It touches no evidence file. It is a pod delete and not a kill
between the two steps, so say that.

## 5. Scenario 4: unauthorized and invalid access (Exp 7)

**Point to make:** access controls and validation are enforced and logged, and one recorded case failed for an honest reason.

```bash
python scripts/demo.py security
```

**On screen:** each line with status and `request_id`; the before/after count of household A's transactions; the denied request's
line in the API log.

| Case | Expected status (measured in Exp 7) |
|---|---|
| No token | 401 |
| Garbage token | 401 |
| User B reads household A | 404 `household not found` |
| User B writes into household A | 404 |
| Malformed JSON | 400 |
| 3 MB body | JSON 413 from the gateway; no API log line |

Measured in Exp 7: access-control cases 20/22 as expected, recorded **FAIL**; input and limits 38/38; victim snapshot unchanged;
22/22 requests in both logs; first 429 for wrong passwords at attempt 11, because each replica counts its own 5 failures.

**Say explicitly:** the 2 failed cases are one case in both repetitions: `B GET /api/households/{A}` expected 404 and got 405,
because that route has no GET. Nothing was disclosed. The expected value was my error, and it stays as written before the run.

**Restore:** nothing to do. The requests changed no data.

## 6. Optional fifth scenario: detection (Exp 6)

```bash
python scripts/demo.py alert       # freezes one API process; about a minute
```

Measured: ApiReplicaDown firing at 11.2–12.2 s, cleared at 52.6–55.5 s; replica out of endpoints at 2.4–2.7 s, back at 43.2–45.8 s. 34 of
1076 requests got a 503 after 5 s with no failover. Restore: automatic, the liveness probe restarts the container.

## 7. Final check after the demo

```bash
kubectl -n university get pods
python scripts/demo.py status
git status --short
```

Do not delete the cluster until after the defence: `bash scripts/down.sh` removes it.

## 8. Likely instructor questions

**Why is your baseline shown with 37 % and your improved with 8 % during the database outage? Isn't that worse?**
On that ratio, yes, and I report it. No configuration can serve these reads with its only database down. Baseline requests wait up
to the 12 s client timeout, so some finish after the restore. Improved answers within 10.0 s with a machine-readable error. The
gain is bounded response time, and the cost is fewer late successes and slower recovery (4.0–12.3 s vs 3.2–8.0 s).

**Is the load result a real improvement?**
Partly an artefact. The baseline fails because of the 1200/min per-process rate limiter, with API CPU near 61 millicores, not because of
capacity. Each improved replica counts separately, so its limit is effectively doubled. The 41 connection errors have no established
cause.

**Why did Exp 7 fail, and why not fix it?**
One case expected 404 and got 405 because that route has no GET. Nothing was disclosed. The expected value was written before the
run, so changing it afterwards would be re-scoring.

**Your MTTF for Exp 1 baseline is about 22 s. Is that the reliability of the system?**
No. I chose when to inject, so MTTF mostly reflects injection timing, and λ is close to 1 over the window. I compute them because the
assignment asks; the comparable quantities are MTTR, downtime and success ratio. The 0.999 per-instance calculation is illustrative only.

**Does idempotency make the improved configuration better on integrity?**
No. It is in the application, so both have it, and Exp 4 shows the same integrity in both. The difference is retry time and probes.

**How do you know the transaction was really interrupted between its steps?**
The test session holds a row lock so the balance update blocks after the insert. `pg_locks` is saved at the kill and shows the insert
done, no committed rows are visible, Postgres logs `unexpected EOF on client connection with an open transaction`, and the retry is
answered 201, not 200. Two first runs where the kill had no effect are kept as INVALID.

**Why did ApiHighErrorRate fire in Exp 3 if clients saw no failure?**
The API records a request the gateway timed out as a 500, while the gateway's retry succeeded. It is a false positive of measuring
errors at the API. A gateway-side error rate is a candidate fix. It is untested.

**What single failure takes everything down?**
PostgreSQL, and the node. Replicas on one node do not protect against node failure. No database replica exists, and no restore was run
in this project: SLO-9 is not tested. The only drill is the earlier one in the `ftr-a3` lab.

**Why raise the Postgres limit in the middle of the experiments? Isn't that changing the conditions?**
It is a dated amendment with the reason. The INVALID run is kept. Runs at 256 Mi are set aside, and all experiments were repeated at 1 Gi so
they share one environment. The same broken release then ran at 100 %. That supports memory pressure and does not prove it: one run pair.

**How repeatable is this?**
The commands are in the README and `run_all.sh`. Each experiment has 2–4 repetitions, so ranges describe behaviour and are not statistics.
Everything ran on one laptop with the load generator on it. Reproduction needs the API image built from hoWallet at `06aea177` with the
metrics patch.

**Which mechanisms have no automated test?**
M1 (gateway) and M4 (probes) have no Go test; Exp 1, 3 and 6 are their evidence. For M3 the unit test only proves that an error propagates; the
integration tests were cited, not run, so Exp 4 is the evidence.

**Why did the endterm figure change from 23 s to 25 s?**
The endterm's probe stopped while requests were still timing out, so 23 s was a lower bound. The run now waits for clean answers. Why a graceful
delete is slower than a forced one (2.0 s) is still not established.
