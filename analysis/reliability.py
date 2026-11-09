#!/usr/bin/env python3
"""Reliability calculations for the FTR final project (work package B).

Reads measured inputs and prints a markdown table of MTTF, MTBF, MTTR,
availability, observed failure rate and request-success ratio per experiment
and configuration. Standard library only.

Usage
-----
  python analysis/reliability.py                                   # evidence/results/reliability-inputs.json
  python analysis/reliability.py path/to/reliability-inputs.json
  python analysis/reliability.py --endterm evidence/endterm-results  # derive inputs from the endterm raw runs
  python analysis/reliability.py ... --json-out analysis/reliability-computed.json

Expected input schema (reliability-inputs.json, schema_version 1)
-----------------------------------------------------------------
{
  "schema_version": 1,
  "generated_at": "2026-11-06T12:00:00Z",          # optional, informational
  "runs": [
    {
      "experiment": "E1",                          # experiment id, 1..8 or "E1".."E8"
      "scenario":   "instance failure",            # optional label
      "config":     "baseline" | "improved",       # any string; grouped as given
      "rep":        1,                             # repetition number
      "window_s":   60.0,                          # observation window length T_obs, seconds
      "failures":   1,                             # optional; default = len(downtime)
      "downtime":   [[3.1, 26.4]],                 # failure intervals [start_s, end_s], seconds
                                                   #   relative to the window start, non-overlapping
      "requests_ok":       470,                    # 2xx/3xx answers in the window
      "requests_failed":   12,                     # 5xx, timeouts, connection errors
      "requests_excluded": 0,                      # optional (e.g. 429 = limiter working, not a failure)
      "measured":   true,                          # false => value is illustrative and is labelled so
      "note":       "free text"                    # optional
    }
  ]
}
An equivalent nested form is accepted too:
  {"E1": {"baseline": {<run fields>} | [<run>, <run>, ...], "improved": ...}, ...}

The file that scripts/build_summary.py actually writes uses other field names, and is converted on load:
  observation_window_s -> window_s          downtime_intervals_s -> downtime (null -> no intervals)
  failures_observed    -> failures          requests_429         -> requests_excluded
  requests_total - requests_ok - requests_429 -> requests_failed       run -> rep
Its "aggregate" block is not used for the results; it is only compared with the sums computed here.

Definitions (see notes/reliability.md)
--------------------------------------
  T_obs  window length (s); D = sum of downtime interval lengths (s); U = T_obs - D (s)
  N_f    number of failure episodes in the window
  MTTR = D / N_f                     (s)       mean time to repair / restore
  MTTF = U / N_f                     (s)       mean up time before a failure
  MTBF = MTTF + MTTR = T_obs / N_f   (s)
  A    = MTTF / (MTTF + MTTR) = U / T_obs      (time-based availability, dimensionless)
  lambda = N_f / T_obs               (1/s, printed per hour)
  R_req  = ok / (ok + failed)        (request-success ratio; excluded requests not counted)
With N_f = 0 the window is right-censored: MTTF > T_obs, MTTR undefined, A = 1.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(HERE, "..", "evidence", "results", "reliability-inputs.json")

RUN_KEYS = {"window_s", "downtime", "requests_ok", "requests_failed", "failures"}


# --------------------------------------------------------------------------- loading
def from_build_summary(r: dict) -> dict:
    """Convert one run of scripts/build_summary.py to the schema above."""
    excluded = r["requests_429"]
    return {
        "experiment": r["experiment"], "config": r["config"], "rep": r["run"],
        "window_s": r["observation_window_s"],
        "failures": r["failures_observed"],
        "downtime": r["downtime_intervals_s"] or [],
        "requests_ok": r["requests_ok"],
        "requests_failed": r["requests_total"] - r["requests_ok"] - excluded,
        "requests_excluded": excluded,
        "measured": True,
        "recovery_s": r.get("recovery_s"),
    }


def check_aggregate(runs: list[dict], aggregate_block: dict) -> list[str]:
    """Differences between the input file's own totals and the sums over its runs."""
    problems = []
    for key, res in aggregate(runs).items():
        name = f"exp{key[0]}-{key[1]}"
        given = aggregate_block.get(name)
        if given is None:
            problems.append(f"{name}: no aggregate entry")
            continue
        for ours, theirs in ((res["T_obs_s"], given["observation_window_s"]), (res["N_f"], given["failures_observed"]),
                             (res["downtime_s"], given["downtime_total_s"]), (res["requests_ok"], given["requests_ok"])):
            if abs(ours - theirs) > 0.015:
                problems.append(f"{name}: computed {ours}, file says {theirs}")
    return problems


def load_inputs(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and isinstance(data.get("runs"), list):
        runs = data["runs"]
        if runs and "observation_window_s" in runs[0]:
            runs = [from_build_summary(r) for r in runs]
            problems = check_aggregate(runs, data.get("aggregate", {}))
            if problems:
                raise ValueError("input file disagrees with itself: " + "; ".join(problems))
    elif isinstance(data, list):
        runs = data
    elif isinstance(data, dict):  # nested {experiment: {config: run | [runs]}}
        runs = []
        for exp, by_cfg in data.items():
            if not isinstance(by_cfg, dict) or exp in ("schema_version", "generated_at", "source"):
                continue
            for cfg, val in by_cfg.items():
                for i, run in enumerate(val if isinstance(val, list) else [val], 1):
                    runs.append({"experiment": exp, "config": cfg, "rep": run.get("rep", i), **run})
    else:
        raise ValueError("unrecognised input structure")
    for r in runs:
        missing = {"experiment", "config", "window_s"} - r.keys()
        if missing:
            raise ValueError(f"run {r.get('experiment')}/{r.get('config')} lacks {sorted(missing)}")
        r.setdefault("downtime", [])
        r.setdefault("requests_ok", 0)
        r.setdefault("requests_failed", 0)
        r.setdefault("requests_excluded", 0)
        r.setdefault("measured", True)
        r.setdefault("failures", len(r["downtime"]))
    return runs


def _is_ok(s) -> bool:
    return isinstance(s, int) and 200 <= s < 400


def _is_excluded(s) -> bool:
    return s == 429  # rate limiter answering as designed; not a failure (endterm convention)


def episodes_from_records(recs: list[dict], merge_gap_s: float) -> tuple[float, list[list[float]], int, int, int]:
    """Turn client request records into (window_s, downtime intervals, ok, failed, excluded).

    A record is {"t": start (s), "s": HTTP status or 0, "ms": latency}. A failure episode is a run of failed
    requests (by start time) not interrupted by a success; episodes closer than merge_gap_s are merged. The
    episode lasts from the first failed request's start to the last failed request's end (client-visible).
    """
    recs = sorted((r for r in recs if r.get("t") is not None), key=lambda r: r["t"])
    if not recs:
        return 0.0, [], 0, 0, 0
    t0 = recs[0]["t"]
    t_end = max(r["t"] + (r.get("ms") or 0) / 1000.0 for r in recs)
    ok = failed = excluded = 0
    eps: list[list[float]] = []
    cur = None
    for r in recs:
        s = r.get("s")
        start = r["t"] - t0
        end = start + (r.get("ms") or 0) / 1000.0
        if _is_excluded(s):
            excluded += 1
            continue
        if _is_ok(s):
            ok += 1
            if cur is not None:
                eps.append(cur)
                cur = None
            continue
        failed += 1
        if cur is None:
            cur = [start, end]
        else:
            cur[1] = max(cur[1], end)
    if cur is not None:
        eps.append(cur)
    merged: list[list[float]] = []
    for e in eps:
        if merged and e[0] - merged[-1][1] < merge_gap_s:
            merged[-1][1] = max(merged[-1][1], e[1])
        else:
            merged.append(list(e))
    return t_end - t0, [[round(a, 3), round(b, 3)] for a, b in merged], ok, failed, excluded


def load_endterm(dirpath: str, merge_gap_s: float) -> list[dict]:
    """Derive runs from the endterm raw results (evidence/endterm-results/*.json)."""
    runs = []
    for f in sorted(glob.glob(os.path.join(dirpath, "*.json"))):
        name = os.path.basename(f)
        if "INVALID" in name:  # fault was not injected; excluded in the endterm too
            continue
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        m = re.match(r"(e\d)", name)
        exp = m.group(1).upper() if m else name
        sub = re.sub(r"^e\d-|\.json$", "", name)
        kind = re.sub(r"-?rep\d+.*$", "", sub)
        if kind and kind not in ("baseline", "endterm"):  # E4 has three sub-scenarios
            exp = f"{exp}/{kind}"
        rep = d.get("rep")
        scenario = d.get("experiment", "")
        groups: list[tuple[str, list]] = []
        if isinstance(d.get("configs"), dict):  # E2: both configs probed at once
            for cfg, v in d["configs"].items():
                groups.append((cfg, v.get("records", [])))
        elif isinstance(d.get("items"), dict):  # E7: per-writer attempts
            recs = [a for its in d["items"].values() for it in its for a in it.get("attempts", [])]
            groups.append((d.get("config", "endterm"), recs))
        elif isinstance(d.get("records"), list):
            groups.append((d.get("config", "endterm"), d["records"]))
        for cfg, recs in groups:
            if not recs:
                continue
            window, down, ok, bad, exc = episodes_from_records(recs, merge_gap_s)
            runs.append({
                "experiment": exp, "scenario": scenario, "config": cfg,
                "rep": rep if rep is not None else sub, "window_s": round(window, 3),
                "failures": len(down), "downtime": down,
                "requests_ok": ok, "requests_failed": bad, "requests_excluded": exc,
                "measured": True, "note": f"derived from {name}",
            })
    return runs


# --------------------------------------------------------------------------- computing
def compute(window_s: float, failures: int, downtime: list, ok: int, failed: int) -> dict:
    d = sum(max(0.0, float(b) - float(a)) for a, b in downtime)
    d = min(d, window_s)
    u = window_s - d
    out = {"T_obs_s": window_s, "N_f": failures, "downtime_s": d, "uptime_s": u,
           "requests_ok": ok, "requests_failed": failed}
    if failures > 0:
        out["MTTR_s"] = d / failures
        out["MTTF_s"] = u / failures
        out["MTBF_s"] = window_s / failures
        out["censored"] = False
    else:
        out["MTTR_s"] = None
        out["MTTF_s"] = None  # > T_obs (right-censored)
        out["MTBF_s"] = None
        out["censored"] = True
    out["A_time"] = (u / window_s) if window_s > 0 else None
    out["lambda_per_h"] = (failures / window_s * 3600.0) if window_s > 0 else None
    n = ok + failed
    out["R_req"] = (ok / n) if n else None
    return out


def aggregate(runs: list[dict]) -> "OrderedDict[tuple, dict]":
    groups: "OrderedDict[tuple, list]" = OrderedDict()
    for r in sorted(runs, key=lambda r: (str(r["experiment"]), str(r["config"]))):  # stable within a config
        groups.setdefault((str(r["experiment"]), str(r["config"])), []).append(r)
    res = OrderedDict()
    for key, rs in groups.items():
        w = sum(float(r["window_s"]) for r in rs)
        nf = sum(int(r["failures"]) for r in rs)
        down = [iv for r in rs for iv in r["downtime"]]
        ok = sum(int(r["requests_ok"]) for r in rs)
        bad = sum(int(r["requests_failed"]) for r in rs)
        c = compute(w, nf, down, ok, bad)
        c["reps"] = len(rs)
        c["measured"] = all(r.get("measured", True) for r in rs)
        c["scenario"] = next((r.get("scenario") for r in rs if r.get("scenario")), "")
        c["per_run"] = [dict(rep=r.get("rep"), **compute(float(r["window_s"]), int(r["failures"]),
                                                          r["downtime"], int(r["requests_ok"]),
                                                          int(r["requests_failed"]))) for r in rs]
        res[key] = c
    return res


# --------------------------------------------------------------------------- printing
def fmt(x, nd=1, pct=False):
    if x is None:
        return "–"
    return f"{x * 100:.{nd}f} %" if pct else f"{x:.{nd}f}"


def table(res, per_run: bool) -> str:
    head = ("| Exp | Config | Reps | T_obs (s) | N_f | Downtime D (s) | MTTR (s) | MTTF (s) | MTBF (s) "
            "| A = MTTF/(MTTF+MTTR) | λ (1/h) | ok / failed | Success ratio | Data |")
    lines = [head, "|" + "---|" * 14]
    for (exp, cfg), c in res.items():
        mttf = fmt(c["MTTF_s"]) if not c["censored"] else f"> {c['T_obs_s']:.1f} (censored)"
        lines.append(
            f"| {exp} | {cfg} | {c['reps']} | {fmt(c['T_obs_s'])} | {c['N_f']} | {fmt(c['downtime_s'])} "
            f"| {fmt(c['MTTR_s'])} | {mttf} | {fmt(c['MTBF_s'])} | {fmt(c['A_time'], 2, True)} "
            f"| {fmt(c['lambda_per_h'], 0)} | {c['requests_ok']} / {c['requests_failed']} "
            f"| {fmt(c['R_req'], 2, True)} | {'measured' if c['measured'] else '**illustrative**'} |")
        if per_run:
            for p in c["per_run"]:
                lines.append(
                    f"| | ↳ rep {p['rep']} | | {fmt(p['T_obs_s'])} | {p['N_f']} | {fmt(p['downtime_s'])} "
                    f"| {fmt(p['MTTR_s'])} | {fmt(p['MTTF_s']) if not p['censored'] else '> T_obs'} "
                    f"| {fmt(p['MTBF_s'])} | {fmt(p['A_time'], 2, True)} | {fmt(p['lambda_per_h'], 0)} "
                    f"| {p['requests_ok']} / {p['requests_failed']} | {fmt(p['R_req'], 2, True)} | |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input", nargs="?", default=None, help="reliability-inputs.json")
    ap.add_argument("--endterm", metavar="DIR", help="derive inputs from endterm raw results instead")
    ap.add_argument("--merge-gap", type=float, default=1.0,
                    help="merge failure episodes separated by less than this many seconds (endterm mode)")
    ap.add_argument("--per-run", action="store_true", help="also print one row per repetition")
    ap.add_argument("--json-out", help="write computed values to this JSON file")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1251/cp1252
        sys.stdout.reconfigure(encoding="utf-8")

    if a.endterm:
        runs = load_endterm(a.endterm, a.merge_gap)
        src = f"endterm raw results in {a.endterm} (derived: episodes from client records, merge gap {a.merge_gap} s)"
    else:
        path = a.input or DEFAULT_INPUT
        if not os.path.exists(path):
            print(f"input not found: {path}", file=sys.stderr)
            return 2
        runs = load_inputs(path)
        src = os.path.relpath(path, os.path.join(HERE, ".."))
    if not runs:
        print("no runs found", file=sys.stderr)
        return 1
    res = aggregate(runs)
    print(f"<!-- source: {src} -->")
    print(table(res, a.per_run))
    print()
    print("A is time-based (client-visible downtime). λ is conditional on the injected fault – the window was "
          "chosen around one injection, so it is not a field failure rate. Censored MTTF = no failure observed.")
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump({"source": src, "results": [{"experiment": k[0], "config": k[1], **v}
                                                  for k, v in res.items()]}, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
