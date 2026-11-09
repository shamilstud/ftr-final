"""Build evidence/results/summary.md and reliability-inputs.json from evidence/results/exp*.json only.
Nothing is typed in by hand. Runs named *INVALID* or with injection_verified=false are listed but excluded.
Criteria are the ones in evidence/test-plan.md.
"""
import glob
import json
import os
from pathlib import Path

R = Path(__file__).resolve().parent.parent / "evidence" / "results"
GAP_S = 2.0   # failed requests closer than this belong to one downtime interval


def load(pat):
    out = []
    for f in sorted(glob.glob(str(R / pat))):
        n = os.path.basename(f)[:-5]
        if "INVALID" in n:
            continue
        d = json.load(open(f, encoding="utf-8"))
        if d.get("injection_verified") is False:
            continue
        out.append((n, d))
    return out


def invalid():
    rows = [os.path.basename(f)[:-5] for f in sorted(glob.glob(str(R / "exp*INVALID*.json")))]
    for f in sorted(glob.glob(str(R / "exp*.json"))):
        if "INVALID" not in f and json.load(open(f, encoding="utf-8")).get("injection_verified") is False:
            rows.append(os.path.basename(f)[:-5] + " (injection_verified=false)")
    return rows


def rng(vals, unit=""):
    vals = [v for v in vals if v is not None]
    if not vals:
        return "-"
    lo, hi = min(vals), max(vals)
    return f"{lo}{unit}" if lo == hi else f"{lo}-{hi}{unit}"


def exp3_recovery(r):
    """Removal of the delay to the end of the last slow or failed request, counting requests still in flight
    at the removal (the run's own field only looked at requests started after it)."""
    t_rem = r["t0"] + r["restore_s"]
    ends = [x["t"] + x["ms"] / 1000 for x in r["records"]
            if (x["ms"] > r["slow_threshold_ms"] or x["s"] != 200) and x["t"] + x["ms"] / 1000 >= t_rem]
    return round(max(ends) - t_rem, 2) if ends else 0.0


def lat(stats):
    return rng([s["lat_p50_ms"] for s in stats]), rng([s["lat_p95_ms"] for s in stats]), rng([s["lat_max_ms"] for s in stats])


def okn(stats):
    ok, tot = sum(s["success"] for s in stats), sum(s["requests"] for s in stats)
    exc = sum(s.get("excluded", 0) for s in stats)
    pct = round(100 * ok / (tot - exc), 3) if tot - exc else None
    return f"{ok}/{tot - exc} ({pct} %)" + (f", {exc} 429" if exc else "")


def verdict(flags):
    return "PASS" if flags and all(flags) else "FAIL"


def fired(alerts):
    return sorted(k for k, v in alerts.items() if v.get("firing_s") is not None)


def retries(runs):
    g = [r["gateway"] for _, r in runs if r.get("gateway")]
    if not g:
        return "0 (no gateway)"
    return (f"{sum(x['requests_retried'] for x in g)} retried, {sum(x['failover_success'] for x in g)} failovers OK, "
            f"{sum(x['gateway_degraded_503'] for x in g)} degraded 503, max {max(x['max_tries_per_request'] for x in g)} tries")


NOTES = [
    "- **Exp1.** Baseline graceful delete: outage 25.0-25.6 s although endpoints returned in 0.9-1.1 s; forced delete: 2.0 s. The cause of "
    "the long graceful outage is not established. The endterm's 23 s figure was cut short by its window. Improved: no failed request in 3 runs; "
    "one request failed over in the gateway log (`502, 200`).",
    "- **Exp2.** The baseline's higher success during the outage (37 % vs 8 %) is not better availability: its requests wait up to the 12 s "
    "client timeout, so some finish after the restore. The gateway gives up within 10 s. After one timeout, `max_fails=1` marks both replicas "
    "down and later requests get a fast 503 (`502` from `no live upstreams`). That is the trade-off: faster, honest failure against fewer late "
    "successes. The API's own 500s pass through the gateway unchanged.",
    "- **Exp3.** Baseline hypothesis failed: predicted 8-10 s per request, measured p50 10.0-18.0 s, max 34 s, 30 % failed. Failures are "
    "connections closed by the server without a response. Hypothesis: the API's 15 s `WriteTimeout`. Confounder: the client's write "
    "retries (1001, mostly 429 from the 60 creates/min route limit) add load on the baseline. The improved run's retries all succeeded, "
    "`504, 200`. Readiness did not remove the slow replica, as predicted, because `/health` does not touch the database. Improved recovery "
    "of 9.84 s in one run comes from one 235 ms request after the removal, just over the 200 ms threshold; the other two runs give 3.78 s and 1.42 s. "
    "ApiHighErrorRate fired in the improved runs, though clients saw no failure: the API records a request the gateway timed out as a 500.",
    "- **Exp4.** Postgres logged `unexpected EOF on client connection with an open transaction` for the interrupted backends, and each retry was "
    "answered 201, so the first attempt really was rolled back. The baseline's retry time grew 3.6 -> 22.2 -> 34.1 s with restart count: "
    "kills about 44 s apart put the container into kubelet's restart back-off, so 3.6 s is the fair figure for a first crash. The "
    "idempotency and the transaction are in the application, so this does not separate the configs on data integrity, only on recovery time.",
    "- **Exp5.** The baseline row fails because of the rate limiter, not capacity: API CPU stayed near 61 millicores. 1080 of 3600 requests got 429 "
    "once the 1200/min per-process limit ran out, 36-39 s into the step. The improved config avoids it only because each replica counts "
    "separately (900/min each), so its limit is effectively doubled, not shared. The 41 baseline connection errors appear only while the limiter "
    "is active; the cause is not established. Plan hypothesis that the baseline also holds this rate: failed.",
    "- **Exp6.** Requests already in flight on the hung replica got a 503 after 5 s with a single try (`504`): 34 of 1076 in total, all within about 5 s "
    "of the fault, and no failover. Exp3's slow replica did fail over. Hypothesis: the difference is that readiness removed the hung replica from DNS "
    "while those requests were in flight, leaving nginx no other peer for them to try. A database outage fires ApiHighLatencyP95 as well as "
    "ApiHighErrorRate, which is correct: requests really were slow. Exp6 replica and error-rate gateway statistics were read from the gateway logs "
    "after the runs. In error-rate rep 2, the secondary alert's clear time was not recorded (fixed later; its primary alert was).",
    "- **Exp7.** The 2 failed cases are one case in both repetitions: `B GET /api/households/{A}` expected 404, got 405, because the route has no GET. "
    "Nothing was disclosed, but the case tests routing, not tenant isolation; the expected value was my error and was left as written. The other cases "
    "show that tenant isolation is enforced. The two requests missing from the API log are the 3 MB bodies, which the gateway rejected with 413 first and logged.",
    "- **Exp8.** As predicted, the baseline survived too: a default RollingUpdate with 1 replica keeps the old pod, so this result shows no gain from "
    "the improved config. Its bad pod takes longer to go (6.1-6.5 s vs 0.8-1.9 s) because of the preStop `sleep 5`. No alert noticed the stuck "
    "rollout in any run: a monitoring gap. The broken release's first run at a 256Mi Postgres limit was a real outage (3.61 % availability, Postgres "
    "OOM-killed) that disappeared at 1Gi. That supports memory pressure as the cause, on one run pair; it is excluded from this table and "
    "kept as evidence that the database is a single point of failure.",
    "- **All.** One-node kind cluster on a laptop: replicas share one host, the load generator runs on it, and pod-level faults cannot show node failure. "
    "Repetitions are few (2-4), so ranges describe behaviour and are not statistical estimates.",
]

rows = []   # the consolidated table
details = []
HEAD = "| Exp | Config | Valid reps | Fault injected | Detection (s) | Recovery (s) | OK / total | p50 ms | p95 ms | max ms | Retries / failovers | Data integrity | Result |"
SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|---|"


def add(exp, cfg, reps, fault, det, rec, ok, ls, retr, integ, res):
    rows.append(f"| {exp} | {cfg} | {reps} | {fault} | {det} | {rec} | {ok} | {ls[0]} | {ls[1]} | {ls[2]} | {retr} | {integ} | {res} |")


# ------------------------------------------------------------ exp1
for cfg in ("improved", "baseline"):
    runs = load(f"exp1-{cfg}-*.json")
    if not runs:
        continue
    st = [r["stats_after_injection"] for _, r in runs]
    flags = [r["stats_after_injection"]["availability_pct"] >= (99.9 if r["mode"] == "graceful" else 99) for _, r in runs]
    add(1, cfg, len(runs), "pod delete: 2 graceful, 1 forced",
        rng([r["detection"]["endpoint_drop_s"] for _, r in runs]) + " endpoint drop",
        rng([r["recovery"]["recovery_s"] for _, r in runs]) + f"; outage {rng([r['recovery']['user_visible_outage_s'] for _, r in runs])}",
        okn(st), lat(st), retries(runs), "n/a (read-only probe)",
        verdict(flags) if cfg == "improved" else "comparison, no target")

# ------------------------------------------------------------ exp2
runs2 = load("exp2-*.json")
for cfg in ("improved", "baseline"):
    if not runs2:
        break
    cs = [r["configs"][cfg] for _, r in runs2]
    st = [c["stats_during_outage"] for c in cs]
    integ = [c["integrity"] for c in cs]
    flags = [c["integrity"]["pass"] and (c["recovery"]["recovery_after_restore_cmd_s"] or 0) <= 30 for c in cs]
    if cfg == "improved":
        flags += [bool(fired(r["alerts"])) for _, r in runs2]
        flags += [all(k.split(" ", 1)[-1].startswith("{") for k in c["error_bodies"] if k[:3].isdigit()) for c in cs]
    add(2, cfg, len(runs2), "postgres: 2 stop, 2 SIGSTOP freeze, 20 s",
        rng([c["detection"]["first_failed_request_s"] for c in cs]) + " first failure" +
        ("; alerts firing " + rng([min((v["firing_s"] for v in r["alerts"].values() if v["firing_s"] is not None), default=None) for _, r in runs2]) if cfg == "improved" else ""),
        rng([c["recovery"]["recovery_after_restore_cmd_s"] for c in cs]) + " after restore",
        okn(st) + " during outage", lat(st), retries(runs2) if cfg == "improved" else "0 (no gateway)",
        f"{sum(i['acknowledged'] for i in integ)} acked, {sum(i['rows_in_db'] for i in integ)} rows, "
        f"{sum(i['rows_in_db'] - i['distinct_client_id'] for i in integ)} dup; balances {'OK' if all(i['pass'] for i in integ) else 'MISMATCH'}",
        verdict(flags))

# ------------------------------------------------------------ exp3
for cfg in ("improved", "baseline"):
    runs = load(f"exp3-{cfg}-*.json")
    if not runs:
        continue
    st = [r["stats_during"] for _, r in runs]
    integ = [r["integrity"] for _, r in runs]
    flags = [r["integrity"]["pass"] and exp3_recovery(r) <= 30 for _, r in runs]
    if cfg == "improved":
        flags += [r["stats_during"]["availability_pct"] >= 99 and r["stats_during"]["lat_max_ms"] <= 12000 and
                  r["gateway"]["max_tries_per_request"] <= 2 for _, r in runs]
    res = verdict(flags) if cfg == "improved" else ("comparison, no target; writes " + ("OK" if all(i["pass"] for i in integ) else "MISMATCH"))
    add(3, cfg, len(runs), f"netem {runs[0][1]['delay_ms']} ms api->postgres, 40 s",
        rng([r["detection"]["first_slow_or_failed_request_s"] for _, r in runs]) + " first slow",
        rng([exp3_recovery(r) for _, r in runs]) + " after removal",
        okn(st) + " during fault", lat(st),
        retries(runs) + f"; client write retries {sum(r['client_retries_writer'] for _, r in runs)}",
        f"{sum(i['acknowledged'] for i in integ)} acked, {sum(i['rows_in_db'] for i in integ)} rows, "
        f"{sum(i['rows_in_db'] - i['distinct_client_id'] for i in integ)} dup; balances {'OK' if all(i['pass'] for i in integ) else 'MISMATCH'}",
        res)

# ------------------------------------------------------------ exp4
for cfg in ("improved", "baseline"):
    runs = load(f"exp4-{cfg}-*.json")
    if not runs:
        continue
    st = [r["probe_after_kill"] for _, r in runs]
    limit = 10 if cfg == "improved" else 60
    flags = [r["integrity_target"]["pass"] and r["integrity_background"]["pass"] and r["duplicates_db"] == 0 and
             (r["recovery"]["time_to_successful_retry_s"] or 9e9) <= limit for _, r in runs]
    add(4, cfg, len(runs), "SIGKILL between insert and balance move",
        rng([r["detection"]["interrupted_client_error_s"] for _, r in runs]) + " client error",
        rng([r["recovery"]["time_to_successful_retry_s"] for _, r in runs]) + " to successful retry",
        okn(st) + " probe after kill", lat(st),
        retries(runs) + f"; client tries {rng([r['client_attempts'] for _, r in runs])}",
        f"interrupted id rows {rng([r['integrity_target']['rows_with_interrupted_client_id'] for _, r in runs])}; "
        f"balance == -sum {'all' if all(r['integrity_target']['pass'] for _, r in runs) else 'NOT all'}; "
        f"DB dup {rng([r['duplicates_db'] for _, r in runs])}", verdict(flags))

# ------------------------------------------------------------ exp5
for cfg in ("improved", "baseline"):
    runs = load(f"exp5-{cfg}-*.json")
    if not runs:
        continue
    st = [r["phases"]["step 30 req/s"] for _, r in runs]
    flags = [s["availability_pct"] >= 99 and s["lat_p95_ms"] < 500 for s in st] + [not fired(r["alerts"]) for _, r in runs]
    add(5, cfg, len(runs), "load step 5->30->5 req/s", "n/a", "n/a", okn(st) + " in step phase", lat(st), retries(runs),
        "n/a (read-only)", verdict(flags))

# ------------------------------------------------------------ exp6
quiet = load("exp6-quiet-*.json")
for label, pat, al in (("hung replica (SIGSTOP)", "exp6-replica-*.json", "ApiReplicaDown"),
                       ("database stopped 25 s", "exp6-error-*.json", "ApiHighErrorRate"),
                       ("lock bursts about 40 s", "exp6-latency-*.json", "ApiHighLatencyP95")):
    runs = load(pat)
    if not runs:
        continue
    f = [r["alerts"].get(al, {}).get("firing_s") for _, r in runs]
    c = [r["alerts"].get(al, {}).get("cleared_s") for _, r in runs]
    st = [r.get("probe_fault_window") or r["probe_after_injection"] for _, r in runs]
    flags = [x is not None and x <= 45 for x in f] + [x is not None for x in c]
    others = sorted({k for _, r in runs for k in fired(r["alerts"]) if k != al})
    add(6, "improved", len(runs), label, f"{al} firing {rng(f)}", f"alert cleared {rng(c)}", okn(st), lat(st), retries(runs),
        "also fired: " + (", ".join(others) or "none"), verdict(flags))
if quiet:
    seen = [r["alerts_seen"] for _, r in quiet]
    st = [r["probe"] for _, r in quiet]
    add(6, "improved", len(quiet), f"none: quiet {quiet[0][1]['duration_s']} s", "n/a", "n/a", okn(st), lat(st), "-",
        "alerts seen: " + str(sum(len(s) for s in seen)), verdict([not s for s in seen]))
add_note = "Baseline has no alert rules of its own, so experiment 6 has no baseline row."

# ------------------------------------------------------------ exp7
acc = load("exp7-access-*.json")
inp = load("exp7-input-*.json")
for name, runs in (("access control", acc), ("input and limits", inp)):
    if not runs:
        continue
    cases = [c for _, r in runs for c in r["cases"]]
    good = sum(1 for c in cases if c["pass"])
    if name == "access control":
        flags = [r["pass"] for _, r in runs]
        integ = "victim snapshot unchanged: " + ("yes" if all(r["data_unchanged"] for _, r in runs) else "NO")
        logged = f"{sum(1 for c in cases if c['gateway_logged'] and c['api_logged'])}/{len(cases)} logged in gateway+API"
    else:
        spoof = [x for _, r in runs for x in r["x_real_ip"] if x["entry"] == "gateway"]
        flags = [c["pass"] for c in cases] + [x["logged_remote"] != "203.0.113.77" for x in spoof]
        integ = "accounts before/after: " + ", ".join(str(r["accounts_before_after"]) for _, r in runs)
        logged = f"{sum(1 for c in cases if c['gateway_logged'])}/{len(cases)} gateway, {sum(1 for c in cases if c['api_logged'])}/{len(cases)} API; first 429 at attempt {rng([r['first_429_at_attempt'] for _, r in runs])}"
    add(7, "gateway", len(runs), name, logged, "n/a", f"{good}/{len(cases)} cases as expected", ("-", "-", "-"), "-", integ, verdict(flags))

# ------------------------------------------------------------ exp8
for cfg in ("improved", "baseline"):
    runs = load(f"exp8-{cfg}-*.json")
    if not runs:
        continue
    st = [r["stats_fault_window"] for _, r in runs]
    integ = [r["integrity"] for _, r in runs]
    add(8, cfg, len(runs), "broken release API_PORT=9999, then rollout undo",
        rng([r["detection"]["new_pod_readiness_failure_seen_s"] for _, r in runs]) + " readiness failure; alerts: " +
        (", ".join(sorted({k for _, r in runs for k in fired(r["alerts"])})) or "none"),
        rng([r["recovery"]["bad_pod_gone_after_undo_s"] for _, r in runs]) + " after undo",
        okn(st), lat(st), retries(runs),
        f"{sum(i['acknowledged'] for i in integ)} acked, {sum(i['rows_in_db'] for i in integ)} rows; config restored: "
        f"{'yes' if all(r['acceptance']['config_restored'] for _, r in runs) else 'NO'}",
        verdict([r["pass"] for _, r in runs]))

rows.sort(key=lambda x: int(x.split("|")[1]))
inv = invalid()
L = ["# Consolidated results", "",
     "Generated by `scripts/build_summary.py` from `evidence/results/exp*.json`; every value is measured. Ranges are min-max",
     "across valid repetitions. Criteria are in `evidence/test-plan.md`. \"comparison, no target\" means the plan set no target",
     "for that row. Counts add up across repetitions.", "", HEAD, SEP] + rows + ["", add_note, ""]
L += ["## Reading notes", "",
      "Written by hand after checking the raw results of the 2026-11-06 runs. Causes marked as hypotheses were not tested.", ""] + NOTES + [""]
L += ["## Excluded runs", ""] + ([f"- `{x}`" for x in inv] or ["None."]) + [
    "",
    "Also set aside, not invalid: `results/pre-pg-memory-fix/` (exp3 and exp4 rep 1 at the old Postgres limit) and "
    "`results/superseded/` (exp1 with the early stop, exp6 latency without a clear time). See the amendments in `test-plan.md`.", ""]
(R / "summary.md").write_text("\n".join(L), encoding="utf-8")


# ------------------------------------------------------------ reliability inputs
def intervals(recs, t0):
    bad = sorted((r["t"], r["t"] + r["ms"] / 1000) for r in recs if r["s"] not in (200, 201))
    out = []
    for a, b in bad:
        if out and a - out[-1][1] <= GAP_S:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[round(a - t0, 2), round(b - t0, 2)] for a, b in out]


def entry(exp, cfg, run, recs, t0, faults=1, extra=None):
    ok = sum(1 for r in recs if r["s"] in (200, 201))
    n429 = sum(1 for r in recs if r["s"] == 429)
    win = (max(r["t"] + r["ms"] / 1000 for r in recs) - min(r["t"] for r in recs)) if recs else 0
    iv = intervals([r for r in recs if r["s"] != 429], t0)
    e = {"experiment": exp, "config": cfg, "run": run, "injected_faults": faults, "observation_window_s": round(win, 2),
         "requests_total": len(recs), "requests_ok": ok, "requests_429": n429,
         "successful_request_ratio": round(ok / (len(recs) - n429), 5) if len(recs) - n429 else None,
         "downtime_intervals_s": iv, "downtime_total_s": round(sum(b - a for a, b in iv), 2),
         "failures_observed": len(iv)}
    if extra:
        e.update(extra)
    return e


REL = []
for n, r in load("exp1-*.json"):
    REL.append(entry(1, r["config"], n, r["records"], r["t0"], extra={"recovery_s": r["recovery"]["recovery_s"]}))
for n, r in load("exp2-*.json"):
    for c, v in r["configs"].items():
        REL.append(entry(2, c, n, v["records"], r["t0"], extra={"recovery_s": v["recovery"]["recovery_after_restore_cmd_s"]}))
for n, r in load("exp3-*.json"):
    REL.append(entry(3, r["config"], n, r["records"], r["t0"],
                     extra={"recovery_s": exp3_recovery(r),
                            "fault_duration_s": r["restore_s"]}))
for n, r in load("exp4-*.json"):
    REL.append(entry(4, r["config"], n, r["records"], r["t0"],
                     extra={"recovery_s": r["recovery"]["time_to_successful_retry_s"]}))
for n, r in load("exp5-*.json"):
    REL.append(entry(5, r["config"], n, r["records"], r["t0"], faults=0))
for n, r in load("exp6-*.json"):
    if "records" in r:
        REL.append(entry(6, "improved", n, r["records"], r["t0"]))
    else:
        p = r["probe"]
        REL.append({"experiment": 6, "config": "improved", "run": n, "injected_faults": 0,
                    "observation_window_s": r["duration_s"], "requests_total": p["requests"], "requests_ok": p["success"],
                    "requests_429": 0, "successful_request_ratio": round(p["success"] / p["requests"], 5),
                    "downtime_intervals_s": None, "downtime_total_s": None, "failures_observed": p["failed"]})
for n, r in load("exp8-*.json"):
    REL.append(entry(8, r["config"], n, r["records"], r["t0"], extra={"recovery_s": r["recovery"]["bad_pod_gone_after_undo_s"]}))

agg = {}
for e in REL:
    k = f"exp{e['experiment']}-{e['config']}"
    a = agg.setdefault(k, {"runs": 0, "injected_faults": 0, "failures_observed": 0, "observation_window_s": 0.0,
                           "downtime_total_s": 0.0, "requests_total": 0, "requests_ok": 0, "requests_429": 0})
    a["runs"] += 1
    for f in ("injected_faults", "failures_observed", "observation_window_s", "requests_total", "requests_ok", "requests_429"):
        a[f] = round(a[f] + e[f], 2)
    a["downtime_total_s"] = round(a["downtime_total_s"] + (e["downtime_total_s"] or 0), 2)
for a in agg.values():
    d = a["requests_total"] - a["requests_429"]
    a["successful_request_ratio"] = round(a["requests_ok"] / d, 5) if d else None

doc = {"_description": {
    "scope": "Measured in the kind lab on one date. Faults were injected on purpose, so these windows measure behaviour under fault: "
             "they do not estimate field MTTF or field availability.",
    "experiment": "Number in the assignment's matrix. Experiment 7 has no availability window and is left out.",
    "config": "baseline or improved.",
    "run": "Result file name without .json.",
    "injected_faults": "Faults injected in this run (0 for load and quiet windows).",
    "observation_window_s": "First request sent to last response received by the probe, seconds. Includes the time before the fault.",
    "requests_total": "Probe requests in the window. Experiment 2 uses its GET probe; writes are in the result files.",
    "requests_ok": "Probe requests answered 200 or 201.",
    "requests_429": "Answers from the rate limiter; excluded from the ratio.",
    "successful_request_ratio": "requests_ok / (requests_total - requests_429).",
    "downtime_intervals_s": f"[start, end] relative to the injection, seconds. Failed requests within {GAP_S} s are merged. Null where the result has no per-request records.",
    "downtime_total_s": "Sum of the intervals. Only failed requests count: slow but successful requests are not downtime, so read experiment 3 with its latency. Resolution is limited by the probe rate.",
    "failures_observed": "Number of downtime intervals (user-visible failures).",
    "recovery_s": "Experiment's own recovery measure. Exp1 injection to replacement ready and no failures; exp2 restore command to last failure; exp3 removal to last slow request; exp4 kill to successful retry; exp8 undo to bad pod gone.",
    "fault_duration_s": "Exp3 only: how long the delay was in place.",
    "aggregate": "Sums over valid runs per experiment and config. MTTR = downtime_total_s / failures_observed; failure rate = failures_observed / observation_window_s.",
}, "aggregate": agg, "runs": REL}
(R / "reliability-inputs.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
print("\n".join(L))
