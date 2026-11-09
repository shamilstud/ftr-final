# Software fault tolerance (work package D)

Six mechanisms that already exist in the code or lab configuration; none was added for this project.
Go references are to the hoWallet repository at commit `06aea177`. `k8s/` lines are from `ftr-final/k8s/` as of
06.11.2026; the directive names are given too, because those files may shift.

Test output: `analysis/ft-unit-tests.txt` (20 unit tests, **20 passed, 0 failed**). Integration tests need Docker and
were **cited but not run** here. M1 and M4 have no Go test: the experiments are their evidence. For M3, the experiment (Exp 4) is the evidence, because its unit test proves only error propagation. "Endterm" results are earlier measurements; final results come from Exp 1–8. The `k8s/` line references below were rechecked on 06.11.2026 and did not move.

## Summary

| # | Mechanism | Fault handled | Code / config | Unit test run | Exp |
|---|---|---|---|---|---|
| M1 | Gateway timeouts and bounded retries | Dead, hung or slow API replica | `k8s/40-gateway.yaml:59-60,66-67` | none (nginx config) | 3, 1 |
| M2 | Idempotent create via `client_id` | Client retry of a write whose answer was lost; concurrent duplicates | `internal/service/transaction.go:278-316` | 16 passed | 4, 2 |
| M3 | Database transaction rollback | Interruption between record insert and balance update | `internal/repository/postgres/postgres.go:130-174` | error propagation only; real rollback needs integration | 4, 2 |
| M4 | Health and readiness checks | Crashed or hung instance receiving traffic | `k8s/30-api.yaml:64-74`, `internal/router/router.go:129-131` | none | 1, 6 |
| M5 | Graceful degradation | Upstream or database down; exchange rate missing | `k8s/40-gateway.yaml:73,79-83`, `internal/service/rates.go:265-271` | 2 passed | 2 |
| M6 | Rate limiting with `Retry-After` | Overload, abusive client, brute force | `internal/router/router.go:115,182,209,397-398,778-788` | 1 passed | 5, 7 |

## M1. Gateway timeouts and bounded retries

| Item | Detail |
|---|---|
| Fault | An API replica is dead, hung or slower than the deadline |
| Config | `proxy_connect_timeout 1s`, `proxy_read_timeout 5s` (`k8s/40-gateway.yaml:59-60`); `proxy_next_upstream error timeout http_502 http_503 http_504`, `proxy_next_upstream_tries 2` (`:66-67`); per-pod upstream with passive ejection `resolve max_fails=1 fail_timeout=2s` (`:35`) |
| Behaviour | A failed attempt moves to the other replica, at most 2 tries. Worst case for GET ≈ 10 s (2 × 5 s), so the bound is 10 s and not 5 s |
| Writes | nginx does not pass a non-idempotent request to the next server once it was sent to an upstream, and `non_idempotent` is deliberately absent. A POST that timed out is not replayed; the client retries with `client_id` (M2) |
| Proof | No Go test: this is nginx configuration, proved by experiment. The access log field `upstream_status` (e.g. `504, 200`) counts the retries |
| Demonstrated | Exp 3: 31 retries, all `504, 200`, maximum 5.08 s, at most 2 tries. Exp 1: 0 failed of 418, 1 failover. Exp 2: maximum latency 10.0 s |
| Limit | Recovery after a database restore is slower than the baseline's (Exp 2: 4.0–12.3 s vs 3.2–8.0 s), likely because of passive ejection. A request in flight on a replica that leaves DNS may get no failover (Exp 6: 34 of 1076 got 503 after 5 s; the DNS explanation is a hypothesis). There is no full circuit breaker in the API |

## M2. Idempotent transaction create via `client_id`

| Item | Detail |
|---|---|
| Fault | The write committed but the answer was lost, so the client retries; or two attempts arrive together |
| Code | `CreateOnce` (`internal/service/transaction.go:278-316`), `priorWrite` (`:366`); 200 for a repeat, 201 for a new row (`internal/handler/transaction.go:60-67`); lookup scoped to the household (`internal/db/transactions.go:168-171`); partial unique index `idx_transactions_client (household_id, client_id)` (`migrations/000106_transaction_client_id.up.sql:18-20`) |
| Behaviour | The lookup comes first, so a retry returns the first row with no side effects. A concurrent loser hits 23505 on the index, rolls back, and reads the winner's row. A zero UUID is ignored |
| Unit tests (run, passed) | `internal/service/transaction_client_id_test.go`: `TestTransaction_CreateOnce_NewClientIDWritesOnce`, `TestTransaction_CreateOnce_RepeatReturnsFirstRowWithoutSideEffects`, `TestTransaction_CreateOnce_LostRaceRereadsWinner`, `TestTransaction_CreateOnce_OtherUniqueViolationIsNotARetry`, `TestTransaction_CreateOnce_DifferentBodyFirstWinsAndWarns`, `TestTransaction_CreateOnce_ZeroClientIDIsIgnored`, `TestTransaction_CreateOnce_LookupFailureIsAnError` and 8 more; `TestCreateTransaction_RepeatedClientIDAnswers200` (`internal/handler/transaction_test.go:146`) |
| Integration (cited, not run) | `TestTransactionService_ClientIDLostRaceMovesNothing`, `TestTransactionService_ClientIDConcurrentBurstWritesOnce` (`internal/repository/postgres/transaction_client_id_test.go:102,180`) |
| Demonstrated | Exp 4: exactly 1 row for the interrupted `client_id` in all 6 runs. Exp 2: 146 and 143 acknowledged writes, the same number of rows, 0 duplicates. The mechanism is in the application, so these results do not separate the configurations |
| Limit | Only `POST /api/transactions` has a key. A client that omits `client_id` can create duplicates |

## M3. Database transaction rollback

| Item | Detail |
|---|---|
| Fault | Process, connection or statement failure between the steps of a multi-step write |
| Code | `RunInTx` (`internal/repository/postgres/postgres.go:130-174`): deferred `Rollback` (`:135`), commit only if the function returns no error (`:170-173`). Create wraps the insert and balance update in one transaction (`internal/service/transaction.go:501-536`) |
| Behaviour | Any error, or loss of the connection before commit, leaves neither the record nor the balance change. After a crash, PostgreSQL discards the uncommitted transaction |
| Unit test (run, passed) | `TestRegister_TxError` (`internal/service/auth_extra_test.go:143`) uses a fake runner and proves only that a transaction error propagates to the caller. **It does not prove rollback** |
| Integration (cited, not run) | `TestTransactionService_ClientIDLostRaceMovesNothing` (`transaction_client_id_test.go:102`) is the real proof against PostgreSQL: the losing attempt's balance is unmoved. No test forces a failure specifically between the insert and the balance update |
| Demonstrated | Exp 4: killed with the insert done and nothing committed (`pg_locks`), Postgres logged `unexpected EOF on client connection with an open transaction`, balance = −Σ afterwards. This is the only evidence: the integration tests were cited, not run |
| Limit | Post-commit steps are not atomic: audit, budget notification and cashback run after the commit (`transaction.go:541-543`), and audit is best-effort |

## M4. Health and readiness checks

| Item | Detail |
|---|---|
| Fault | A crashed or hung instance still in the rotation |
| Code / config | `GET /health` (`internal/router/router.go:129-131`); API readiness every 2 s, failure threshold 1; liveness every 10 s, threshold 3 (`k8s/30-api.yaml:64-74`); gateway `/healthz` (`k8s/40-gateway.yaml:44-47`); Postgres readiness (`k8s/10-postgres.yaml:26-28`) |
| Behaviour | An unready pod leaves the headless Service, so nginx and Prometheus stop using it; a hung pod is restarted by liveness |
| Proof | No dedicated Go test of the probe behaviour. `/health` appears only in route-class and security-header tests (`test/api/authz_routes_test.go:94`, `test/api/platform_security_test.go:123`). Proved by experiment |
| Demonstrated | Exp 1: endpoint removal in 0.2–0.3 s. Exp 6: hung replica removed at 2.4–2.7 s, back at 43.2–45.8 s after the liveness restart |
| Limit | **`/health` does not touch the database**, so readiness stays green during a DB outage, and a new pod can be Ready before it can serve (Exp 1 baseline: outage 25.0–25.6 s although endpoints returned in 0.9–1.1 s; the cause is not established) |

## M5. Graceful degradation

| Item | Detail |
|---|---|
| Fault | No upstream answers in time; the exchange-rate provider is unreachable and no archived rate exists |
| Config / code | Gateway: `error_page 502 503 504 = @degraded` (`k8s/40-gateway.yaml:73`) returns JSON 503 with `Retry-After: 2` and `request_id` (`:79-83`). Rates: `ErrNoRate` instead of rate 1 or zero (`internal/service/rates.go:265-271`), mapped to `422 no_rate` (`internal/handler/helpers.go:266-270`) |
| Behaviour | A signalled failure replaces an HTML page, a hang or a wrong number. Base-currency records never touch the rate path; nothing is written on `no_rate` |
| Unit tests (run, passed) | `TestMoneySpec_Resolve_NoRate_IsAnErrorNeverRate1` (`internal/service/money_spec_test.go:106`), `TestWriteCurrencyError_NoRate_CarriesTheNoRateCode` (`internal/handler/currency_error_test.go:15`) |
| Integration (cited, not run) | `TestCreate_ForeignCurrencyWithNoRate_Is422AndWritesNothing` (`test/api/money_create_test.go:80`) |
| Demonstrated | Exp 2: 569 JSON 503 from the gateway. The `no_rate` path was shown in endterm E8 and was not repeated in Exp 1–8 |
| Limit | During a DB outage the API answers 500 for the membership check, and the gateway passes it through rather than turning it into 503 (`error_page` covers 502/503/504 only). Session liveness fails **open** on a DB error (`internal/middleware/session.go:128-132`) |

## M6. Rate limiting with `Retry-After`

| Item | Detail |
|---|---|
| Fault | Overload, a client in a retry loop, password brute force |
| Code | Global 1200/min per IP (`internal/router/router.go:115`), 10/min on `/auth` (`:182`), 600/min per user (`:209`), 60/min on create (`:397-398`); JSON refusal and helpers (`:778-788`) |
| Behaviour | 429 with JSON `code: rate_limited` and `Retry-After`, in place of letting the load reach the database pool |
| Unit test (run, passed) | `TestRateLimit_RefusalIsJSONWithRetryAfter` (`internal/router/ratelimit_test.go:17`) |
| Integration (cited, not run) | `TestRateLimit_TheAuthLimit_AnswersJSON429WithRetryAfter` (`test/api/platform_security_test.go:96`) |
| Demonstrated | Exp 5: 1080 × 429 on the single-replica baseline in the step phase, none on two replicas. Exp 7: lockout 429 with `Retry-After: 900`, first at attempt 11 |
| Limit | Counters live in each replica's memory, so with 2 replicas the effective limit doubles, and a restart resets them. This is why the improved config passes Exp 5: the gain is partly an artefact of the limiter. All host traffic shares one SNAT address, so per-IP limits are shared by test clients |

## Considered, not counted

| Mechanism | Why not counted |
|---|---|
| Job single-run via advisory lock (`internal/repository/postgres/postgres.go:182`, `internal/service/recurring.go:923`) | No test exercises the lock, and no experiment covers it; only the recurring job takes it |
| Circuit breaker | The API has none. `max_fails=1 fail_timeout=2s` is passive ejection and is treated as part of M1 |

## How to rerun the unit tests

```bash
export JWT_SECRET=<any synthetic value of 32+ characters>
go test -count=1 -v ./internal/service/ -run 'TestTransaction_CreateOnce_|TestMoneySpec_Resolve_NoRate|TestRegister_TxError'
go test -count=1 -v ./internal/handler/ -run 'TestCreateTransaction_RepeatedClientIDAnswers200|TestWriteCurrencyError_NoRate'
go test -count=1 -v ./internal/router/ -run 'TestRateLimit_RefusalIsJSONWithRetryAfter'
```
