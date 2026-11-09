# Reliability engineering (work package B)

Calculator: `analysis/reliability.py` (standard library only). Input: `evidence/results/reliability-inputs.json`.
Every number in this file carries one of three labels: **measured-final**, **measured-endterm**, **illustrative**.

## 1. Formulas and units

Symbols, per observation window of one experiment and configuration:

| Symbol | Meaning | Unit |
|---|---|---|
| `T_obs` | observation window length | s |
| `N_f` | failure episodes observed in the window | count |
| `D` | total downtime: sum of the lengths of the failure intervals | s |
| `U = T_obs − D` | total up time | s |
| `ok`, `failed` | requests answered correctly / failed in the window | count |

| Quantity | Formula | Unit | Meaning |
|---|---|---|---|
| MTTR | `D / N_f` | s | mean time to restore client-visible service |
| MTTF | `U / N_f` | s | mean up time before a failure |
| MTBF | `MTTF + MTTR = T_obs / N_f` | s | mean time between the starts of successive failures |
| Availability | `A = MTTF / (MTTF + MTTR) = U / T_obs` | dimensionless, % | share of the window in which service was up |
| Observed failure rate | `λ = N_f / T_obs` | 1/s (printed as 1/h) | failure episodes per unit time, under the conditions of the window |
| Request-success ratio | `R_req = ok / (ok + failed)` | dimensionless, % | share of requests served correctly |

Repetitions are pooled: `T_obs`, `N_f`, `D`, `ok` and `failed` are summed over the repetitions, then the formulas
are applied. This weights each repetition by its length; means of per-run ratios are not used.

With `N_f = 0` the window is **right-censored**: MTTF is reported as `> T_obs`, MTTR and MTBF as undefined, `A = 1`.
Zero failures in a short window does not show that λ is zero. The rule of three gives a rough 95 % upper bound of
`λ < 3 / T_obs`, valid only for a constant-rate process.

## 2. Observation windows and failure definitions

| Item | Definition |
|---|---|
| Window | From the first client request to the end of the last one in the run (`observation_window_s`). It encloses one injected fault and includes the time before it |
| Failed request | 5xx, timeout or connection error at the client |
| Excluded request | 429 from the limiter. It is reported separately and enters neither `ok` nor `failed` (`requests_excluded`) |
| Failure interval | From the start of the first failed request to the end of the last failed request of an episode. This is client-visible downtime, not component downtime. In the final inputs the interval is stored relative to the injection, which does not change its length |
| Failure episode | A run of failed requests. In the final inputs, failed requests within 2.0 s are merged (`build_summary.py`); in endterm-derived data the merge gap defaults to 1 s |
| Detection time (reported apart) | Fault onset to alert `firing` |
| Recovery time (reported apart) | Each experiment's own measure, listed in `reliability-inputs.json` under `recovery_s`: Exp 1 injection to replacement ready and no failures; Exp 2 restore command to last failure; Exp 3 removal to last slow request; Exp 4 kill to successful retry; Exp 8 undo to bad pod gone |

## 3. Assumptions and limits of these numbers

1. **MTTF, MTBF and λ describe the injection, not the system.** The experimenter chooses when the fault happens, so
   the MTTF of a window is mostly the lead time before the injection, and λ is close to `1 / T_obs`. They are computed
   as the assignment requires, but they are **not** field reliability estimates and must not be extrapolated.
2. **The comparable quantities are MTTR, `D`, `R_req` and `A`**, and `A` only between runs with equal windows and
   equal fault duration.
3. Failure episodes are treated as independent; with 2–4 repetitions per scenario the results show behaviour, not tight
   distributions, and no confidence intervals are claimed.
4. Client-visible downtime depends on the probe. Resolution is about one request interval, and the length of a
   failed request (including the client's timeout) counts towards `D`, so slow failures lengthen it.
5. `N_f` is sensitive to the merge gap in derived data: endterm E2 improved gives `N_f = 7` with a 1 s gap and
   `N_f = 25` with no gap, while `D` moves only from 115.9 s to 109.0 s. Within one injection, compare `D` and not
   MTTR alone.
6. `R_req` depends on how many requests a configuration lets the client send and finish. It can reward hanging over
   failing fast (see §6).
7. Single node, one fault at a time; node, zone and host failures are not represented.

## 4. Input schema: `evidence/results/reliability-inputs.json`

The file is written by `scripts/build_summary.py`, and its fields are described in its `_description` block. On load the
calculator converts each run to its internal fields, documented at the top of `analysis/reliability.py`:

| File field | Calculator field |
|---|---|
| `observation_window_s` | `window_s` (`T_obs`) |
| `downtime_intervals_s` (null means no per-request records) | `downtime` (`D`) |
| `failures_observed` | `failures` (`N_f`) |
| `requests_ok` | `ok` |
| `requests_total − requests_ok − requests_429` | `failed` |
| `requests_429` | excluded |

The first run of the calculator stopped on this mismatch (`run 1/baseline lacks ['window_s']`). The fix is the conversion
in `load_inputs`; no input value was changed.

```bash
python analysis/reliability.py                                  # final inputs, default path
python analysis/reliability.py --per-run                        # plus one row per repetition
python analysis/reliability.py --json-out analysis/reliability-computed.json
python analysis/reliability.py --endterm evidence/endterm-results   # derive from endterm raw runs
```

## 5. Final results (measured-final)

Output of `python analysis/reliability.py` on `evidence/results/reliability-inputs.json`, values copied by script from
`analysis/reliability-computed.json`. Pooled over the valid repetitions. An experiment without request traffic gets
`n/a`. The calculator's sums agree with the `aggregate` block of the input file; it stops with an error if they do not.

| Exp | Scenario | Config | `T_obs` (s) | `N_f` | MTTR (s) | MTTF (s) | MTBF (s) | A | λ (1/h) | `R_req` |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Instance failure | baseline | 119.5 | 3 | 17.6 | 22.3 | 39.8 | 55.95 % | 90 | 68.51 % |
| 1 | | improved | 66.7 | 0 | undefined | > 66.7 (censored) | undefined | 100.00 % | 0 | 100.00 % |
| 2 | Database unavailable | baseline | 207.0 | 4 | 25.7 | 26.1 | 51.8 | 50.42 % | 70 | 75.28 % |
| 2 | | improved | 207.7 | 4 | 28.6 | 23.3 | 51.9 | 44.95 % | 69 | 50.56 % |
| 3 | Network delay / timeout | baseline | 268.0 | 3 | 33.8 | 55.5 | 89.3 | 62.13 % | 40 | 86.27 % |
| 3 | | improved | 247.5 | 0 | undefined | > 247.5 (censored) | undefined | 100.00 % | 0 | 100.00 % |
| 4 | Interrupted transaction | baseline | 121.5 | 3 | 18.3 | 22.3 | 40.5 | 54.94 % | 89 | 74.85 % |
| 4 | | improved | 115.6 | 0 | undefined | > 115.6 (censored) | undefined | 100.00 % | 0 | 100.00 % |
| 5 | Load spike | baseline | 240.4 | 6 | 1.0 | 39.0 | 40.1 | 97.42 % | 90 | 98.69 % |
| 5 | | improved | 240.4 | 0 | undefined | > 240.4 (censored) | undefined | 100.00 % | 0 | 100.00 % |
| 6 | Monitoring / alert | improved | 720.2 | 5 | 16.2 | 127.8 | 144.1 | 88.72 % | 25 | 91.39 % |
| 7 | Unauthorized operation | improved | n/a | n/a | n/a | n/a | n/a | n/a | n/a | security outcomes reported separately |
| 8 | Recovery / rollback | baseline | 146.8 | 0 | undefined | > 146.8 (censored) | undefined | 100.00 % | 0 | 100.00 % |
| 8 | | improved | 156.0 | 0 | undefined | > 156.0 (censored) | undefined | 100.00 % | 0 | 100.00 % |

Interpretation of the final results: the numbers below the table are the ones to compare.

- **MTTF, MTBF and λ reflect injection timing, not system reliability** (§3.1). For example, λ of 90 per hour for
  Exp 1 baseline says only that one fault was injected about every 40 s of window. None of them may be quoted as a field value.
- **Exp 1:** MTTR 17.6 s pools two different behaviours: graceful delete 25.6 s and 25.0 s, forced delete 2.0 s. Improved has no failure
  episode, so its MTTF is only a lower bound of 66.7 s of observation; by the rule of three the rough 95 % bound is λ < 162 per hour, which is
  a statement about the short window.
- **Exp 2 favours the baseline on `A` and `R_req`** (75.28 % vs 50.56 %). This is not an availability gain for the baseline: its requests wait up to the 12 s client
  timeout, so some finish after the restore. The improved gain is bounded response time, and it comes at the cost of fewer late successes.
- **Exp 3 and Exp 4:** the improved row has no failed request, while the baseline loses 74 and 123. Read Exp 3 with its latency: slow successful
  requests are not downtime here, and improved p95 was still about 5 s.
- **Exp 5:** the baseline row is caused by the per-process rate limiter (429 excluded here, 41 connection errors counted), not by capacity.
- **Exp 6** pools hung replica, database outage, lock bursts and quiet windows; it has no baseline and no single meaning. It is shown because the
  assignment asks for the quantities, and the scenario rows in `results/summary.md` are the useful ones.
- **Exp 8:** no failure in either configuration, so this experiment shows no gain from the improved one.

**Worked example, Exp 1 baseline rep 1** (`exp1-baseline-rep1-graceful`, measured-final): `T_obs` = 46.13 s, `N_f` = 1,
`D` = 25.63 s, `ok` = 221, `failed` = 149.

```
U       = 46.13 − 25.63          = 20.50 s
MTTR    = 25.63 / 1              = 25.63 s
MTTF    = 20.50 / 1              = 20.50 s    (time before and after the outage, not a lifetime; see §3.1)
MTBF    = 20.50 + 25.63          = 46.13 s
A       = 20.50 / 46.13          = 44.44 %
λ       = 1 / 46.13 s            = 78 per hour
R_req   = 221 / (221 + 149)        = 59.73 %
```

## 6. Worked calculation and pipeline check (measured-endterm)

Output of `reliability.py --endterm evidence/endterm-results`, default merge gap 1 s. These are the earlier runs,
**not** the final results; "endterm" is the configuration that became "improved". E5, E6 and E8 have no request
series, so they produce no row.

| Exp | Config | Reps | T_obs (s) | N_f | D (s) | MTTR (s) | MTTF (s) | MTBF (s) | A | λ (1/h) | ok / failed | R_req |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E1 replica loss | baseline | 3 | 78.6 | 3 | 48.2 | 16.1 | 10.2 | 26.2 | 38.76 % | 137 | 252 / 284 | 47.01 % |
| E1 | endterm | 3 | 65.8 | 0 | 0.0 | – | > 65.8 (censored) | – | 100.00 % | 0 | 529 / 0 | 100.00 % |
| E2 DB outage | baseline | 4 | 232.5 | 4 | 105.4 | 26.3 | 31.8 | 58.1 | 54.68 % | 62 | 1131 / 268 | 80.84 % |
| E2 | endterm | 4 | 233.2 | 7 | 115.9 | 16.6 | 16.8 | 33.3 | 50.31 % | 108 | 761 / 642 | 54.24 % |
| E3 load | baseline | 2 | 240.6 | 2 | 2.4 | 1.2 | 119.1 | 120.3 | 98.99 % | 30 | 3635 / 14 | 99.62 % |
| E3 | endterm | 2 | 240.4 | 0 | 0.0 | – | > 240.4 (censored) | – | 100.00 % | 0 | 4204 / 0 | 100.00 % |
| E4 error-rate | endterm | 2 | 132.0 | 2 | 54.5 | 27.3 | 38.8 | 66.0 | 58.71 % | 55 | 475 / 319 | 59.82 % |
| E4 latency | endterm | 2 | 159.3 | 0 | 0.0 | – | > 159.3 (censored) | – | 100.00 % | 0 | 958 / 0 | 100.00 % |
| E4 replica-down | endterm | 3 | 207.5 | 3 | 26.7 | 8.9 | 60.3 | 69.2 | 87.14 % | 52 | 1213 / 35 | 97.20 % |
| E7 interrupted writes | baseline | 2 | 101.2 | 2 | 3.1 | 1.5 | 49.1 | 50.6 | 96.95 % | 71 | 400 / 14 | 96.62 % |
| E7 | endterm | 2 | 97.6 | 0 | 0.0 | – | > 97.6 (censored) | – | 100.00 % | 0 | 400 / 0 | 100.00 % |

**Correction.** The endterm's 23 s baseline figure was understated: the probe stopped while requests were still timing out
(`evidence/test-plan.md`, amendment of 17:10). Exp 1 measures 25.0–25.6 s. The endterm rows are kept only as a check of the pipeline.

**Worked example, E1 baseline rep 1** (`e1-baseline-rep1-graceful.json`): `T_obs` = 28.1 s, `N_f` = 1, `D` = 23.0 s,
`ok` = 41, `failed` = 137.

```
U       = 28.1 − 23.0            = 5.1 s
MTTR    = 23.0 / 1               = 23.0 s
MTTF    = 5.1 / 1                = 5.1 s      (lead time before the injection, see §3.1)
MTBF    = 5.1 + 23.0             = 28.1 s
A       = 5.1 / 28.1             = 18.1 %     (script: 18.22 %, from unrounded values)
λ       = 1 / 28.1 s             = 0.0356 1/s = 128 1/h
R_req   = 41 / (41 + 137)        = 23.03 %
```

Cross-check: the derived `D` of 23.0 s equals `user_visible_outage_s` that the endterm run recorded independently,
and `R_req` equals its `availability_pct` of 23.03 %.

**What the endterm numbers show:**

- E1 and E7: the improved configuration has no client-visible failure episode in any repetition. Only a lower bound on
  MTTF can be stated, and it is about a minute or two of observation.
- E1 baseline pools two very different failure modes: 23 s after a graceful delete and 2.0 s after a forced kill. The
  pooled MTTR of 16.1 s describes neither, so report them separately.
- **E2 is a case where the headline metrics favour the baseline** (A 54.68 % vs 50.31 %, `R_req` 80.84 % vs 54.24 %).
  This does not show the baseline tolerates a database outage better. The baseline's requests hang up to the 12 s
  client timeout (p95 12.0 s), and in rep 1 it logged 52 successes during the outage, with an average latency of 8.3 s
  over all requests of that phase. A likely reason, not separately proven, is that requests queued and finished once the database returned.
  The improved path answers within the gateway bound (p95 5.0 s), so more requests fail in the same period. The gain
  bought here is bounded latency and a machine-readable error, not availability. No configuration can serve these
  reads with its only database down.

## 7. Theoretical estimates (illustrative, not measured)

**Every value in this section is illustrative.** The per-component availability of 0.999 is assumed in order to show
the shape of the calculation; it was not measured and must not be cited as a result.

Series and parallel composition, assuming independent failures:

```
A_series   = A1 × A2 × ...
A_parallel = 1 − (1 − A)^n
```

| Structure | Calculation (illustrative, each instance A = 0.999) | A |
|---|---|---|
| Gateway ×2 | 1 − 0.001² | 0.999999 |
| API ×2 | 1 − 0.001² | 0.999999 |
| PostgreSQL ×1 | – | 0.999 |
| Path, improved | 0.999999 × 0.999999 × 0.999 | ≈ 0.998998 |
| Path, baseline (API ×1, PostgreSQL ×1, no gateway) | 0.999 × 0.999 | ≈ 0.998001 |

Redundancy roughly halves the illustrative unavailability (0.2 % to 0.1 %), and what remains is almost entirely the
single database. That is the same conclusion as the SPOF analysis in `requirements.md` §8. The measured windows do not
verify these figures: they cover deliberate faults, not a natural failure process.

**Why measured results differ from theory:** the formula assumes independent failures and instant failover. The
measurements show dependent behaviour instead: both API replicas fail together when the shared database is lost (E2),
a replacement pod can be Ready before it can serve (E1 baseline), and the gateway's passive ejection delays recovery
after a database restore (Exp 2: 4.0–12.3 s vs 3.2–8.0 s; the cause is likely but not separately tested).
