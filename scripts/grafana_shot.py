"""Screenshot the Grafana dashboard over the time range of a saved run.

  python scripts/grafana_shot.py <result name without .json> [more names]

Output: evidence/screenshots/grafana-<name>.png. Needs Playwright from ../../../web (see shoot.mjs).
"""
import json
import subprocess
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))
from ftlab import EVID, RESULTS, ROOT  # noqa: E402

for name in sys.argv[1:]:
    d = json.load(open(RESULTS / f"{name}.json", encoding="utf-8"))
    t0 = d["t0"]
    end = d.get("t_end") or t0 + 150
    url = f"http://127.0.0.1:30300/d/howallet-endterm/x?orgId=1&from={int((t0 - 30) * 1000)}&to={int((end + 15) * 1000)}&kiosk"
    out = EVID / "screenshots" / f"grafana-{name}.png"
    subprocess.run(["node", str(ROOT / "scripts" / "shoot.mjs"), "url", url, str(out), "1600", "1100", "7000"],
                   cwd=str(ROOT), check=True, capture_output=True)
    print("saved", out.relative_to(ROOT), "size", out.stat().st_size)
