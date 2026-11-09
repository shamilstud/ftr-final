"""Append correlated gateway + API log lines to evidence/log-samples.txt.

  python scripts/log_samples.py "<section title>" <request-id prefix> <how many> [retried|failed|any] [api|baseline]

Logs are read from the live pods, so run it before the pods restart. Tokens never appear in these logs: the gateway
format has no headers, and the API logs method, path, status and ids only.
"""
import subprocess
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))
from ftlab import EVID, NS, gateway_lines  # noqa: E402


def logs(sel):
    return subprocess.run(["kubectl", "-n", NS, "logs", "-l", f"app={sel}", "--tail=-1", "--prefix", "--max-log-requests=4"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.splitlines()


def main():
    title, prefix, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    kind = sys.argv[4] if len(sys.argv) > 4 else "any"
    sel = sys.argv[5] if len(sys.argv) > 5 else "api"
    gw = gateway_lines(prefix, 3 * 3600)
    if kind == "retried":
        gw = [g for g in gw if "," in g.get("upstream_addr", "")]
    elif kind == "failed":
        gw = [g for g in gw if g["status"] >= 400]
    api = logs(sel)
    gw_raw = logs("gateway")
    out = [f"=== {title} (request id prefix {prefix}, {len(gw)} matching gateway entries, showing up to {n}) ==="]
    for g in gw[:n]:
        rid = g["request_id"]
        out.append(f"[{rid}]")
        out += ["  gateway: " + l.split(" ", 1)[-1][:400] for l in gw_raw if f'"{rid}"' in l]
        lines = [l.split(" ", 1)[-1][:500] for l in api if f'"{rid}"' in l]
        out += ["  api:     " + l for l in lines] or ["  api:     (no line: request never reached a live API process, or the pod has restarted)"]
    with open(EVID / "log-samples.txt", "a", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n\n")
    print("\n".join(out)[:3000])


if __name__ == "__main__":
    main()
