"""New final-project experiments.

  python scripts/final.py exp3 <improved|baseline> <rep>   slow api -> postgres link (tc netem)
  python scripts/final.py exp4 <improved|baseline> <rep>   SIGKILL in the middle of a create transaction
  python scripts/final.py exp8 <improved|baseline> <rep>   broken release, rollout undo
Raw results: evidence/results/exp3-*.json, exp4-*.json, exp8-*.json.
"""
import json
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, __import__("os").path.dirname(__file__))
from ftlab import (BASE, AlertWatcher, EndpointWatcher, GW, NS, Probe, ensure_account, gateway_lines, gw_stats, http,  # noqa: E402
                   known_state, kubectl, load_users, log, now, psql, rel, save, sh, summarize, timeline)
from faults import Writer, accounts_probe, compact, first_after, integrity  # noqa: E402
from exps import NODE, container_pid  # noqa: E402

DELAY_MS = 2000   # calibrated by a pilot: GET /api/accounts makes 4-5 DB round trips, so about 8-10 s
CFG = {"improved": (GW, "api", 2, "app=api"), "baseline": (BASE, "api-baseline", 1, "app=api-baseline")}


def duplicates_in_db():
    return int(psql("SELECT count(*) FROM (SELECT household_id, client_id FROM transactions "
                    "WHERE client_id IS NOT NULL GROUP BY 1,2 HAVING count(*) > 1) d")[0][0])


# ------------------------------------------------------------------ exp3

def exp3(cfg, rep, hold=40):
    base, svc, _, sel = CFG[cfg]
    name = f"exp3-{cfg}-rep{rep}"
    pods_before = known_state(name)
    victim = kubectl("get", "pods", "-l", sel, "-o", "jsonpath={.items[0].metadata.name}")
    pid = container_pid(victim)
    tc = f"docker exec {NODE} nsenter -t {pid} -n tc "
    add = [tc + "qdisc add dev eth0 root handle 1: prio",
           tc + f"qdisc add dev eth0 parent 1:3 handle 30: netem delay {DELAY_MS}ms",
           tc + "filter add dev eth0 parent 1:0 protocol ip u32 match ip dport 5432 0xffff flowid 1:3"]
    user = "l1" if cfg == "improved" else "l2"
    acc = ensure_account(user, f"exp3-{cfg}-{rep}-{int(now())}", base=base)
    qdisc = ""
    # client timeout 60 s: the client must not be the thing that bounds the wait
    with EndpointWatcher(svc) as ep, AlertWatcher() as al, \
            Probe(accounts_probe(base, "main", name, timeout=60), rate=2, workers=128) as pr, \
            Writer(base, user, acc, name) as wr:
        time.sleep(10)
        t0 = now()
        try:
            for c in add:
                out = sh(c)
                if out:
                    log("  tc:", out)
            log(f"{name}: netem {DELAY_MS} ms on egress to :5432 of {victim} (pid {pid})")
            time.sleep(hold)
        finally:
            qdisc = sh(tc + "-s qdisc show dev eth0")
            t_restore = now()
            log(f"{name}: removed at t+{t_restore - t0:.1f}s", sh(tc + "qdisc del dev eth0 root"))
        t_probe_stop = t_restore + 30
        while now() < t_probe_stop:
            time.sleep(1)
        t_end = now()
    recs = sorted(pr.records, key=lambda r: r["t"])
    sent = 0
    lines = qdisc.splitlines()
    for i, l in enumerate(lines):
        if "netem" in l and i + 1 < len(lines):
            sent = int(lines[i + 1].split()[3])   # " Sent 14547 bytes 91 pkt ..."
    during = [r for r in recs if t0 <= r["t"] < t_restore]
    after = [r for r in recs if r["t"] >= t_restore + 15]
    gw = gw_stats(gateway_lines(name, now() - t0 + 20)) if cfg == "improved" else None
    # recovered = first request started after the removal that was as fast as the pre-fault p95 x 3 (min 200 ms),
    # with every later request also fast
    pre = [r["lat_ms"] for r in recs if r["t"] < t0]
    limit = max(200.0, 3 * sorted(pre)[int(0.95 * (len(pre) - 1))]) if pre else 200.0
    slow_after = [r for r in recs if r["t"] >= t_restore and (r["lat_ms"] > limit or r["status"] != 200)]
    recovered = max((r["t"] + r["lat_ms"] / 1000 for r in slow_after), default=t_restore)
    first_slow = next((r["t"] for r in during if r["lat_ms"] > limit or r["status"] != 200), None)
    res = {"experiment": "Exp3 network delay on the api->postgres link", "config": cfg, "rep": rep, "victim": victim,
           "inject": add, "delay_ms": DELAY_MS, "t0": t0, "t_end": t_end, "restore_s": rel(t_restore, t0),
           "injection_verified": sent > 0, "netem_packets_delayed": sent, "qdisc_at_removal": qdisc,
           "client_timeout_s": 60,
           "stats_before": summarize([r for r in recs if r["t"] < t0]),
           "stats_during": summarize(during),
           "stats_after_settled": summarize(after),
           "slow_threshold_ms": limit,
           "detection": {"first_slow_or_failed_request_s": rel(first_slow, t0)},
           "recovery": {"last_slow_request_end_after_removal_s": rel(recovered, t_restore)},
           "endpoint_changes": [(round(t - t0, 2), n) for t, n in ep.rows],
           "gateway": gw, "alerts": al.summary(t0), "integrity": integrity(acc, wr.items),
           "client_retries_writer": sum(len(i["statuses"]) - 1 for i in wr.items),
           "duplicates_db": duplicates_in_db(),
           "writes": [{"client_id": i["client_id"], "amount": i["amount"], "statuses": i["statuses"],
                       "final": i["final"]} for i in wr.items]}
    print(f"\n=== {name} ===\npods before:\n{pods_before}")
    print(f"{qdisc}\n")
    print("\n".join(timeline(recs, t0, t_end, ep.rows, al.rows, step=5.0)))
    for k in ("injection_verified", "netem_packets_delayed", "stats_before", "stats_during", "stats_after_settled",
              "slow_threshold_ms", "detection", "recovery", "endpoint_changes", "gateway", "alerts", "integrity",
              "client_retries_writer", "duplicates_db"):
        print(f"{k}: {res[k]}")
    res["records"] = compact(recs)
    save(name, res)


# ------------------------------------------------------------------ exp4

def post_txn(base, u, acc, amount, client_id, rid, timeout):
    return http("POST", base + "/api/transactions", token=u["token"], hh=u["household_id"], timeout=timeout,
                headers={"X-Request-Id": rid},
                body={"type": "expense", "amount": str(amount), "account_id": acc, "description": rid,
                      "transacted_at": "2026-11-06T12:00:00Z", "client_id": client_id})


def exp4(cfg, rep):
    base, svc, _, sel = CFG[cfg]
    name = f"exp4-{cfg}-rep{rep}"
    pods_before = known_state(name)
    users = load_users()
    acc = ensure_account("l3", f"exp4-{cfg}-{rep}-{int(now())}", base=base)
    acc_bg = ensure_account("l5", f"exp4-bg-{cfg}-{rep}-{int(now())}", base=base)
    u = load_users()["l3"]
    amounts = [101, 102]
    for i, am in enumerate(amounts):
        r = post_txn(base, u, acc, am, str(uuid.uuid4()), f"{name}-seed{i}", 10)
        assert r["status"] == 201, r
    target = 103
    client_id = str(uuid.uuid4())
    holder = subprocess.Popen(["kubectl", "-n", NS, "exec", "-i", "postgres-0", "--", "sh", "-c",
                               'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q'],
                              stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    attempts = []
    t_success = [None]
    stop_client = threading.Event()

    def client():
        k = 0
        while not stop_client.is_set():
            k += 1
            # attempt 1 waits on the lock; retries use the same client_id
            r = post_txn(base, u, acc, target, client_id, f"{name}-try{k}", 30 if k == 1 else 12)
            attempts.append({"try": k, "t": r["t"], "end": r["t"] + r["lat_ms"] / 1000, "status": r["status"],
                             "err": r["err"], "ms": r["lat_ms"], "body": r["body"][:200]})
            if r["status"] in (200, 201):
                t_success[0] = r["t"] + r["lat_ms"] / 1000
                return
            if now() - attempts[0]["t"] > 120:
                return
            time.sleep(0.5)

    released = False

    def release():
        nonlocal released
        if not released:
            released = True
            holder.stdin.write("COMMIT;\n\\q\n")
            holder.stdin.close()

    th = threading.Thread(target=client, daemon=True)
    waiter = None
    evidence = {}
    try:
        # NO KEY UPDATE: the INSERT's foreign-key check (KEY SHARE) passes, then the app's own balance lock
        # (LockAccountForUpdate, run right before the UPDATE) blocks. So the transaction stops between its two
        # steps: row inserted, balance not moved, nothing committed. pg_locks below proves the INSERT happened.
        holder.stdin.write(f"BEGIN;\nSELECT 1 FROM accounts WHERE id='{acc}' FOR NO KEY UPDATE;\n")
        holder.stdin.flush()
        for _ in range(40):
            if psql("SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction' "
                    "AND query LIKE '%FOR NO KEY UPDATE%'")[0][0] == "1":
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("lock holder did not start")
        with EndpointWatcher(svc) as ep, AlertWatcher() as al, \
                Probe(accounts_probe(base, "main", name, timeout=12), rate=4) as pr, \
                Writer(base, "l5", acc_bg, name) as wr:
            time.sleep(6)
            t_post = now()
            th.start()
            for _ in range(50):
                rows = psql("SELECT pid, client_addr, wait_event_type, wait_event, state, backend_xid, "
                            "pg_blocking_pids(pid), left(query,60) FROM pg_stat_activity "
                            "WHERE wait_event_type='Lock' AND query LIKE 'SELECT id FROM accounts WHERE id = $1 FOR NO KEY UPDATE%'")
                if rows:
                    waiter = rows[0]
                    break
                time.sleep(0.05)
            if not waiter:
                raise RuntimeError("the create never reached the balance lock")
            locks = psql("SELECT locktype, coalesce(relation::regclass::text,''), mode, granted FROM pg_locks "
                         f"WHERE pid={waiter[0]} AND locktype IN ('relation','transactionid','tuple')")
            visible = psql(f"SELECT count(*) FROM transactions WHERE client_id='{client_id}'")[0][0]
            ip = waiter[1].split("/")[0]
            victim = next(p.split()[0] for p in kubectl("get", "pods", "-l", sel, "-o",
                          "jsonpath={range .items[*]}{.metadata.name} {.status.podIP}{'\\n'}{end}").splitlines()
                          if p.split()[1] == ip)
            pid = container_pid(victim)
            restarts0 = kubectl("get", "pod", victim, "-o", "jsonpath={.status.containerStatuses[0].restartCount}")
            kill = f"docker exec {NODE} kill -KILL {pid}"
            t_kill = now()
            evidence["kill_output"] = sh(kill)
            log(f"{name}: backend {waiter[0]} of {victim} blocked on the balance lock; SIGKILL at "
                f"t+{t_kill - t_post:.2f}s after the POST")
            time.sleep(1.0)
            first_ended_before_kill = bool(attempts) and attempts[0]["end"] < t_kill
            release()
            t_release = now()
            th.join(timeout=150)
            stop_client.set()
            dead_backend_left = psql(f"SELECT count(*) FROM pg_stat_activity WHERE pid={waiter[0]}")[0][0]
            # 15 s more so the probe sees the system settle
            deadline = t_kill + 30
            while now() < deadline:
                time.sleep(0.5)
            t_end = now()
    finally:
        release()
        holder.wait(timeout=30)
    recs = sorted(pr.records, key=lambda r: r["t"])
    after = [r for r in recs if r["t"] >= t_kill]
    bad = [r for r in after if r["status"] != 200]
    pg_log = [l[:200] for l in kubectl("logs", "postgres-0", "--since=150s").splitlines()
              if any(x in l for x in ("unexpected EOF", "could not receive", "connection reset", "could not send"))]
    restarts1 = kubectl("get", "pod", victim, "-o", "jsonpath={.status.containerStatuses[0].restartCount}")
    dropped = first_after(ep.rows, t_kill, lambda n: n < CFG[cfg][2])
    back = first_after(ep.rows, dropped or t_kill, lambda n: n >= CFG[cfg][2])
    cnt, dist, total = psql(f"SELECT count(*), count(DISTINCT client_id), coalesce(sum(amount),0) "
                            f"FROM transactions WHERE account_id='{acc}'")[0]
    bal = psql(f"SELECT balance FROM accounts WHERE id='{acc}'")[0][0]
    same = psql(f"SELECT count(*) FROM transactions WHERE client_id='{client_id}'")[0][0]
    exp_sum = sum(amounts) + target
    final_status = attempts[-1]["status"] if attempts else None
    check = {"rows_on_account": int(cnt), "expected_rows": 3, "distinct_client_id": int(dist),
             "rows_with_interrupted_client_id": int(same), "sum_amount": float(total), "expected_sum": exp_sum,
             "balance": float(bal), "expected_balance": -exp_sum,
             "pass": int(cnt) == 3 and int(dist) == 3 and int(same) == 1 and float(total) == exp_sum
             and float(bal) == -exp_sum}
    insert_done = any(l[1] == "transactions" and l[2] == "RowExclusiveLock" and l[3] == "t" for l in locks)
    valid = bool(waiter) and insert_done and int(visible) == 0 and not first_ended_before_kill and restarts1 != restarts0
    res = {"experiment": "Exp4 interrupted multi-step transaction", "config": cfg, "rep": rep, "t0": t_kill,
           "t_end": t_end, "victim": victim, "inject": kill, "injection_verified": valid,
           "blocked_backend": dict(zip(("pid", "client_addr", "wait_type", "wait_event", "state", "xid",
                                        "blocked_by", "query"), waiter)),
           "backend_locks_at_kill": locks, "insert_done_at_kill": insert_done, "committed_rows_visible_at_kill": int(visible),
           "kill_after_post_s": round(t_kill - t_post, 2), "container_restarts": f"{restarts0} -> {restarts1}",
           "lock_released_s": rel(t_release, t_kill), "dead_backend_still_listed_after_success": int(dead_backend_left),
           "postgres_log": pg_log,
           "attempts": [{**a, "t": rel(a["t"], t_kill), "end": rel(a["end"], t_kill)} for a in attempts],
           "client_attempts": len(attempts), "final_status": final_status,
           "recovery": {"time_to_successful_retry_s": rel(t_success[0], t_kill), "endpoint_drop_s": rel(dropped, t_kill),
                        "endpoints_back_s": rel(back, t_kill),
                        "last_failed_probe_end_s": rel(max((r["t"] + r["lat_ms"] / 1000 for r in bad), default=None), t_kill)},
           "detection": {"interrupted_client_error_s": rel(attempts[0]["end"], t_kill) if attempts else None,
                         "first_failed_probe_s": rel(min((r["t"] for r in bad), default=None), t_kill)},
           "probe_after_kill": summarize(after), "probe_total": summarize(recs),
           "integrity_target": check, "integrity_background": integrity(acc_bg, wr.items),
           "duplicates_db": duplicates_in_db(), "alerts": al.summary(t_kill),
           "gateway": gw_stats(gateway_lines(name, now() - t_kill + 20)) if cfg == "improved" else None,
           "writes_background": [{"client_id": i["client_id"], "amount": i["amount"], "statuses": i["statuses"],
                                  "final": i["final"]} for i in wr.items]}
    print(f"\n=== {name} ===\npods before:\n{pods_before}")
    print("interrupted create: account", acc, "client_id", client_id)
    print("\n".join(timeline(recs, t_kill, t_end, ep.rows, al.rows, step=2.0)))
    for k in ("injection_verified", "blocked_backend", "backend_locks_at_kill", "insert_done_at_kill", "committed_rows_visible_at_kill",
              "kill_after_post_s", "container_restarts", "lock_released_s", "dead_backend_still_listed_after_success",
              "postgres_log", "attempts", "final_status", "recovery", "detection", "probe_after_kill",
              "integrity_target", "integrity_background", "duplicates_db", "alerts", "gateway"):
        print(f"{k}: {res[k]}")
    res["records"] = compact(recs)
    save(name, res)


# ------------------------------------------------------------------ exp8

def deploy_state(dep):
    d = json.loads(kubectl("get", "deploy", dep, "-o", "json"))
    t = d["spec"]["template"]["spec"]["containers"][0]
    rs = json.loads(kubectl("get", "rs", "-l", "app=" + dep, "-o", "json"))["items"]
    active = sorted(r["metadata"]["labels"]["pod-template-hash"] for r in rs if r["spec"]["replicas"] > 0)
    return {"generation": d["metadata"]["generation"], "revision": d["metadata"]["annotations"].get("deployment.kubernetes.io/revision"),
            "image": t["image"], "env": t.get("env"), "active_template_hashes": active,
            "ready": d["status"].get("readyReplicas"), "updated": d["status"].get("updatedReplicas"),
            "unavailable": d["status"].get("unavailableReplicas", 0)}


def exp8(cfg, rep, hold=45):
    base, svc, desired, sel = CFG[cfg]
    dep = svc
    name = f"exp8-{cfg}-rep{rep}"
    pods_before = known_state(name)
    user = "l1" if cfg == "improved" else "l2"
    acc = ensure_account(user, f"exp8-{cfg}-{rep}-{int(now())}", base=base)
    before = deploy_state(dep)
    bad_cmd = f"kubectl -n {NS} set env deploy/{dep} API_PORT=9999"
    undo_cmd = f"kubectl -n {NS} rollout undo deploy/{dep}"
    bad_state, bad_pods, unhealthy_at, bad_log, bad_restarts = None, "", None, "", None
    undone = False
    with EndpointWatcher(svc) as ep, AlertWatcher() as al, \
            Probe(accounts_probe(base, "main", name, timeout=12), rate=5) as pr, Writer(base, user, acc, name) as wr:
        time.sleep(10)
        t0 = now()
        try:
            log(f"{name}: deploy broken release `{bad_cmd}`", sh(bad_cmd))
            while now() - t0 < hold:
                ev = kubectl("get", "events", "--field-selector", "reason=Unhealthy", "--sort-by=.lastTimestamp",
                             "-o", "jsonpath={range .items[*]}{.involvedObject.name} {.lastTimestamp} {.message}{'\\n'}{end}")
                now_state = deploy_state(dep)
                newh = [h for h in now_state["active_template_hashes"] if h not in before["active_template_hashes"]]
                if newh and unhealthy_at is None and any(h in l and t_iso(l.split()[1]) >= t0 - 2 for h in newh
                                                        for l in ev.splitlines() if len(l.split()) > 1):
                    unhealthy_at = now()
                    log(f"  readiness failure of the new pod seen at t+{unhealthy_at - t0:.1f}s")
                time.sleep(1)
            bad_state = deploy_state(dep)
            newh = [h for h in bad_state["active_template_hashes"] if h not in before["active_template_hashes"]]
            bad_pods = kubectl("get", "pods", "-l", sel, "-o", "wide")
            if newh:
                np = kubectl("get", "pods", "-l", f"pod-template-hash={newh[0]}", "-o",
                             "jsonpath={.items[0].metadata.name}")
                bad_log = kubectl("logs", np, "--tail=5")
                bad_restarts = kubectl("get", "pod", np, "-o", "jsonpath={.status.containerStatuses[0].restartCount}")
            t_undo = now()
            log(f"{name}: `{undo_cmd}` at t+{t_undo - t0:.1f}s", sh(undo_cmd))
            undone = True
            log(sh(f"kubectl -n {NS} rollout status deploy/{dep} --timeout=120s").splitlines()[-1])
            t_rollout_done = now()
            while now() - t_rollout_done < 60:   # bad pod really gone
                if len(kubectl("get", "pods", "-l", sel, "--no-headers").splitlines()) == desired:
                    break
                time.sleep(0.5)
            t_clean = now()
        finally:
            if not undone:
                log("cleanup undo:", sh(undo_cmd))
        time.sleep(15)
        t_end = now()
    after_state = deploy_state(dep)
    recs = sorted(pr.records, key=lambda r: r["t"])
    fault = [r for r in recs if t0 <= r["t"] < t_clean]
    bad = [r for r in fault if r["status"] != 200]
    all_after = [r for r in recs if r["t"] >= t0]
    bad_all = [r for r in all_after if r["status"] != 200]
    integ = integrity(acc, wr.items)
    dup = duplicates_in_db()
    restored = (after_state["active_template_hashes"] == before["active_template_hashes"] and
                after_state["env"] == before["env"] and after_state["image"] == before["image"] and
                after_state["ready"] == desired)
    verified = bad_state is not None and any(h not in before["active_template_hashes"]
                                             for h in bad_state["active_template_hashes"]) and bad_state["unavailable"] > 0
    criteria = {"availability_ge_99": (summarize(fault)["availability_pct"] or 0) >= 99,
                "bad_release_never_ready": bad_state is not None and bad_state["ready"] == desired and bad_state["unavailable"] > 0,
                "config_restored": restored, "writes_intact": integ["pass"], "no_duplicates": dup == 0,
                "recovery_le_60s": t_clean - t_undo <= 60}
    res = {"experiment": "Exp8 broken release and rollback", "config": cfg, "rep": rep, "t0": t0, "t_end": t_end,
           "inject": bad_cmd, "undo": undo_cmd, "injection_verified": verified,
           "state_before": before, "state_broken": bad_state, "state_after": after_state,
           "bad_release_pod_log": bad_log, "bad_pod_restarts": bad_restarts,
           "detection": {"new_pod_readiness_failure_seen_s": rel(unhealthy_at, t0)},
           "recovery": {"undo_at_s": rel(t_undo, t0), "rollout_status_done_after_undo_s": round(t_rollout_done - t_undo, 2),
                        "bad_pod_gone_after_undo_s": round(t_clean - t_undo, 2)},
           "endpoint_counts": [(round(t - t0, 2), n) for t, n in ep.rows],
           "stats_fault_window": summarize(fault), "stats_whole": summarize(all_after),
           "failed_samples": [{"t": rel(r["t"], t0), "status": r["status"], "err": r["err"], "body": r["body"][:120]}
                              for r in bad[:10]],
           "downtime_s": round(max(r["t"] + r["lat_ms"] / 1000 for r in bad_all) - min(r["t"] for r in bad_all), 2) if bad_all else 0.0,
           "integrity": integ, "duplicates_db": dup, "alerts": al.summary(t0), "acceptance": criteria,
           "pass": all(criteria.values()),
           "gateway": gw_stats(gateway_lines(name, now() - t0 + 20)) if cfg == "improved" else None,
           "writes": [{"client_id": i["client_id"], "amount": i["amount"], "statuses": i["statuses"],
                       "final": i["final"]} for i in wr.items]}
    print(f"\n=== {name} ===\npods before:\n{pods_before}\npods with the broken release:\n{bad_pods}")
    print("\n".join(timeline(recs, t0, t_end, ep.rows, al.rows, step=5.0)))
    for k in ("injection_verified", "state_before", "state_broken", "state_after", "bad_release_pod_log", "bad_pod_restarts",
              "detection", "recovery", "stats_fault_window", "failed_samples", "downtime_s", "integrity",
              "duplicates_db", "alerts", "gateway", "acceptance", "pass"):
        print(f"{k}: {res[k]}")
    res["records"] = compact(recs)
    save(name, res)


def t_iso(s):
    import calendar
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return 0


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    globals()[cmd](*[int(a) if a.isdigit() else a for a in args])
