"""One command per experiment of the matrix: python scripts/run_exp.py <1..8> [rep ...]

Runs all repetitions of that experiment (or only the listed ones) and appends the readable record to
evidence/expN.txt. Raw results: evidence/results/expN-*.json. Needs evidence/lab-users.json (scripts/setup_users.py).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, __import__("os").path.dirname(__file__))
import exps  # noqa: E402
import faults  # noqa: E402
import final  # noqa: E402
from ftlab import EVID, kubectl  # noqa: E402


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)

    def flush(self):
        for st in self.streams:
            st.flush()


# (callable, args) per repetition
PLAN = {
    1: [(faults.e1_once, (c, r, m)) for c in ("improved", "baseline")
        for r, m in ((1, "graceful"), (2, "graceful"), (3, "crash"))],
    2: [(faults.e2_once, (r, m)) for r, m in ((1, "stop"), (2, "stop"), (3, "freeze"), (4, "freeze"))],
    3: [(final.exp3, (c, r)) for c in ("improved", "baseline") for r in (1, 2, 3)],
    4: [(final.exp4, (c, r)) for c in ("improved", "baseline") for r in (1, 2, 3)],
    5: [(exps.e3, (c, r)) for c in ("improved", "baseline") for r in (1, 2)],
    6: [(exps.e4quiet, (1,)), (exps.e4quiet, (2,))] +
       [(exps.e4replica, (r,)) for r in (1, 2, 3)] +
       [(exps.e4errors, (r,)) for r in (1, 2)] +
       [(exps.e4latency, (r,)) for r in (1, 2)],
    7: [(exps.e5, (1,)), (exps.e5, (2,)), (exps.e6, (1,)), (exps.e6, (2,))],
    8: [(final.exp8, (c, r)) for c in ("improved", "baseline") for r in (1, 2)],
}


def postgres_oom_at():
    """finishedAt of the last Postgres termination if the kernel OOM-killed it, else None."""
    reason = kubectl("get", "pod", "postgres-0", "-o",
                     "jsonpath={.status.containerStatuses[0].lastState.terminated.reason}")
    if reason != "OOMKilled":
        return None
    return kubectl("get", "pod", "postgres-0", "-o",
                   "jsonpath={.status.containerStatuses[0].lastState.terminated.finishedAt}")


def main():
    n = int(sys.argv[1])
    only = {int(x) for x in sys.argv[2:]}
    log = open(EVID / f"exp{n}.txt", "a", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log)
    print(f"\n##### run_exp {n} started {time.strftime('%Y-%m-%d %H:%M:%S')} #####")
    for f, args in PLAN[n]:
        rep = next((a for a in args if isinstance(a, int)), None)
        if only and rep not in only:
            continue
        print(f"\n--- {f.__name__}{args} {time.strftime('%H:%M:%S')}")
        oom_before = postgres_oom_at()
        f(*args)
        oom_after = postgres_oom_at()
        if oom_after and oom_after != oom_before:
            # an unplanned database failure confounds the run: flag it and stop instead of collecting more
            msg = f"ANOMALY: postgres OOMKilled at {oom_after} during {f.__name__}{args}: mark this run INVALID and rerun"
            print(msg)
            with open(EVID / "anomalies.txt", "a", encoding="utf-8") as fh:
                fh.write(msg + "\n")
            sys.exit(3)
        if n == 7:
            time.sleep(70)   # /auth is limited to 10/min; login lockout tests must not share a minute
    print(f"##### run_exp {n} finished {time.strftime('%Y-%m-%d %H:%M:%S')} #####")


if __name__ == "__main__":
    main()
