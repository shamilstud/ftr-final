"""Build the final technical report (DOCX, then PDF through Word).

    python report/build_report.py            # DOCX + PDF
    python report/build_report.py --no-pdf   # DOCX only

Adapted from the endterm build. Per-run tables are read from evidence/results/*.json, the consolidated table from
evidence/results/summary.md, the reliability table from analysis/reliability-computed.json, and many descriptive tables
from notes/*.md at build time. Numbers typed into the prose are checked against the raw JSON by verify_facts(), which
stops the build if one no longer holds. The look follows the A3/A4 reports: the A4 .docx is the style template.
"""
import copy  # noqa: F401
import json
import re
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EVID = ROOT / "evidence"
RES = EVID / "results"
SHOTS = EVID / "screenshots"
FIG = HERE / "fig"
TEMPLATE = ROOT.parent / "ftr-a4" / "report" / "Assignment_4_FTR_Yakupov_255193.docx"
OUT_DOCX = HERE / "Final_FTR_Yakupov_255193.docx"
OUT_PDF = HERE / "Final_FTR_Yakupov_255193.pdf"
FIG.mkdir(exist_ok=True)


# ----------------------------------------------------------------------------- data

def J(name):
    return json.load(open(RES / f"{name}.json", encoding="utf-8"))


_rel = json.load(open(ROOT / "analysis" / "reliability-computed.json", encoding="utf-8"))
REL = {(r["experiment"], r["config"]): r for r in _rel["results"]}
SCEN = {"1": "Instance failure", "2": "Database unavailable", "3": "Network delay", "4": "Interrupted transaction",
        "5": "Load step", "6": "Monitoring, pooled", "7": "Unauthorized operation", "8": "Rollback"}


def parse_summary():
    text = (RES / "summary.md").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.startswith("|")]
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    return rows[0], rows[2:]


SUM_HEAD, SUM_ROWS = parse_summary()
SUMMARY_BY = {}
for _r in SUM_ROWS:
    SUMMARY_BY.setdefault((_r[0], _r[1]), {"det": _r[4], "rec": _r[5]})

_exp1_out = [J(f"exp1-baseline-rep{r}-graceful")["recovery"]["user_visible_outage_s"] for r in (1, 2)]
OUT1_MIN, OUT1_MAX = min(_exp1_out), max(_exp1_out)


def _res(c, r):
    return J(f"exp5-{c}-rep{r}")["phases"]["step 30 req/s"]["resources"]


_pg = [_res("improved", r)["postgres-0"]["mem_working_set_mib"] for r in (1, 2)]
PG_WS_MIN, PG_WS_MAX = min(_pg), max(_pg)
_imp_api = [v["cpu_millicores"] for r in (1, 2) for k, v in _res("improved", r).items()
            if k.startswith("api-") and "baseline" not in k]
IMP_API_MIN, IMP_API_MAX = min(_imp_api), max(_imp_api)
_imp_gw = [v["cpu_millicores"] for r in (1, 2) for k, v in _res("improved", r).items() if k.startswith("gateway")]
IMP_GW_MIN, IMP_GW_MAX = min(_imp_gw), max(_imp_gw)
_base_api = [v["cpu_millicores"] for r in (1, 2) for k, v in _res("baseline", r).items() if k.startswith("api-baseline")]
BASE_CPU = f"{min(_base_api):.1f}–{max(_base_api):.1f}"
_first429 = []
for _r2 in (1, 2):
    _d = J(f"exp5-baseline-rep{_r2}")
    _first429.append(next(x["t"] for x in _d["records"] if x["s"] == 429) - _d["t0"] - 30)
FIRST429_MIN, FIRST429_MAX = min(_first429), max(_first429)


def verify_facts():
    """Numbers typed in the prose, recomputed from raw results. A mismatch stops the build."""
    def near(a, b, tol=0.051):
        assert abs(a - b) <= tol, (a, b)

    e1i = [J(f"exp1-improved-rep{r}-{m}")["stats_after_injection"] for r, m in ((1, "graceful"), (2, "graceful"), (3, "crash"))]
    assert sum(s["success"] for s in e1i) == 418 and sum(s["requests"] for s in e1i) == 418
    assert OUT1_MIN == 25.0 and OUT1_MAX == 25.6
    assert J("exp1-baseline-rep3-crash")["recovery"]["user_visible_outage_s"] == 2.0
    e2 = [J("exp2-" + n) for n in ("rep1-stop", "rep2-stop", "rep3-freeze", "rep4-freeze")]
    oi = [d["configs"]["improved"]["stats_during_outage"] for d in e2]
    ob = [d["configs"]["baseline"]["stats_during_outage"] for d in e2]
    assert (sum(s["success"] for s in oi), sum(s["requests"] for s in oi)) == (40, 522)
    assert (sum(s["success"] for s in ob), sum(s["requests"] for s in ob)) == (182, 489)
    assert sum(d["gateway"]["requests_retried"] for d in e2) == 188
    assert sum(d["gateway"]["failover_success"] for d in e2) == 12
    assert sum(d["gateway"]["gateway_degraded_503"] for d in e2) == 569
    assert sum(d["configs"]["improved"]["integrity"]["acknowledged"] for d in e2) == 146
    assert sum(d["configs"]["baseline"]["integrity"]["acknowledged"] for d in e2) == 143
    rec = [d["configs"]["improved"]["recovery"]["recovery_after_restore_cmd_s"] for d in e2]
    assert (min(rec), max(rec)) == (4.0, 12.3)
    rec = [d["configs"]["baseline"]["recovery"]["recovery_after_restore_cmd_s"] for d in e2]
    assert (min(rec), max(rec)) == (3.2, 8.0)
    near(max(s["lat_max_ms"] for s in oi), 10015.8, 0.01)
    e3i = [J(f"exp3-improved-rep{r}") for r in (1, 2, 3)]
    e3b = [J(f"exp3-baseline-rep{r}") for r in (1, 2, 3)]
    assert sum(d["stats_during"]["success"] for d in e3i) == 247 == sum(d["stats_during"]["requests"] for d in e3i)
    assert sum(d["stats_during"]["success"] for d in e3b) == 173 and sum(d["stats_during"]["requests"] for d in e3b) == 247
    assert sum(d["client_retries_writer"] for d in e3b) == 1001 and sum(d["client_retries_writer"] for d in e3i) == 18
    assert sum(d["gateway"]["failover_success"] for d in e3i) == 31 == sum(d["gateway"]["requests_retried"] for d in e3i)
    assert all(d["alerts"]["ApiHighErrorRate"]["firing_s"] is not None for d in e3i)
    assert max(d["stats_during"]["lat_max_ms"] for d in e3b) == 34030.4
    near(max(d["stats_during"]["lat_max_ms"] for d in e3i), 5083.2, 0.01)
    e4b = [J(f"exp4-baseline-rep{r}") for r in (1, 2, 3)]
    e4i = [J(f"exp4-improved-rep{r}") for r in (1, 2, 3)]
    assert [d["recovery"]["time_to_successful_retry_s"] for d in e4b] == [3.6, 22.2, 34.1]
    assert sum(d["probe_after_kill"]["success"] for d in e4i) == 376 == sum(d["probe_after_kill"]["requests"] for d in e4i)
    assert all(d["insert_done_at_kill"] and d["committed_rows_visible_at_kill"] == 0 and d["final_status"] == 201
               and d["integrity_target"]["pass"] for d in e4b + e4i)
    e5b = [J(f"exp5-baseline-rep{r}")["phases"]["step 30 req/s"]["codes"] for r in (1, 2)]
    assert sum(c["429"] for c in e5b) == 1080 and sum(c["conn_error"] for c in e5b) == 41
    e6 = [J(f"exp6-replica-down-rep{r}")["probe_after_injection"] for r in (1, 2, 3)]
    assert sum(s["failed"] for s in e6) == 34 and sum(s["requests"] for s in e6) == 1076
    for r in (1, 2):
        a = J(f"exp7-access-control-rep{r}")
        bad = [c for c in a["cases"] if not c["pass"]]
        assert len(bad) == 1 and bad[0]["case"] == "B GET /api/households/{A}" and bad[0]["status"] == 405 and a["data_unchanged"]
        assert a["all_requests_logged"]
    assert J("exp7-input-limits-rep1")["first_429_at_attempt"] == 11 == J("exp7-input-limits-rep2")["first_429_at_attempt"]
    for c in ("baseline", "improved"):
        for r in (1, 2):
            assert J(f"exp8-{c}-rep{r}")["alerts"] == {}
    s = J("exp8-improved-rep1-INVALID-confounded-postgres-oom")["stats_fault_window"]
    near(100 * s["success"] / s["requests"], 3.61, 0.01)
    print("verify_facts: all prose numbers hold")


# ----------------------------------------------------------------------------- notes as tables

def _section(path, heading):
    text = (ROOT / path).read_text(encoding="utf-8")
    a = text.index("\n" + heading) if ("\n" + heading) in text else text.index(heading)
    rest = text[a + 1:]
    m = re.search(r"\n## ", rest[3:])
    return rest[: m.start() + 3] if m else rest


def _md_tables(sec):
    tables, cur = [], []
    for ln in sec.splitlines():
        if ln.startswith("|"):
            cur.append([c.strip() for c in ln.strip().strip("|").split("|")])
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    return [[t[0]] + t[2:] for t in tables]


def md_table(title, path, heading, widths, size=7.5, which=0, keep=None, rows=None, fill=None):
    t = _md_tables(_section(path, heading))[which]
    head, body = t[0], t[1:]
    if rows:
        body = body[:rows]
    if keep:
        head = [head[i] for i in keep]
        body = [[r[i] for i in keep] for r in body]
    if len(widths) != len(head):
        raise ValueError(f"{title}: {len(head)} columns, {len(widths)} widths: {head}")
    fills = {(i, j): fill for i in range(len(body)) for j in range(len(head))} if fill else None
    return table(title, head, body, widths, size=size, fills=fills)


def md_list(path, heading):
    items = re.findall(r"^\d+\.\s+(.*(?:\n   .*)*)", _section(path, heading), flags=re.M)
    bullets([re.sub(r"\s*\n\s*", " ", i) for i in items])


def parse_refs():
    text = (ROOT / "notes" / "references.md").read_text(encoding="utf-8")
    items = re.findall(r"^\d+\.\s+(.*(?:\n {2,}\S.*)*)", text, flags=re.M)
    return [re.sub(r"\s*\n\s*", " ", i) for i in items]


# ----------------------------------------------------------------------------- figures

def crop_shot(src, box, out):
    Image.open(SHOTS / src).convert("RGB").crop(box).save(FIG / out)
    return FIG / out


OK_C, FAIL_C, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e1e0d9"   # validated reference slots


def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": "#c3c2b7", "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
                         "axes.titlesize": 9, "axes.titlecolor": INK, "legend.frameon": False})
    return plt


def fig_exp1():
    plt = _mpl()
    fig, axes = plt.subplots(2, 1, figsize=(6.6, 3.4), sharex=True, sharey=True)
    for ax, (name, label) in zip(axes, (("exp1-baseline-rep1-graceful", "Baseline: 1 replica, no gateway"),
                                        ("exp1-improved-rep1-graceful", "Improved: 2 replicas behind the gateway"))):
        d = J(name)
        ok, bad = {}, {}
        for r in d["records"]:
            sec = int((r["t"] - d["t0"]) // 1)
            (ok if r["s"] == 200 else bad).setdefault(sec, 0)
            (ok if r["s"] == 200 else bad)[sec] += 1
        xs = sorted(set(ok) | set(bad))
        o = [ok.get(x, 0) for x in xs]
        b = [bad.get(x, 0) for x in xs]
        ax.bar(xs, o, width=0.8, color=OK_C, label="Succeeded")
        ax.bar(xs, b, width=0.8, bottom=o, color=FAIL_C, label="Failed")
        ax.axvline(0, color=INK, lw=0.8)
        ax.text(0.3, 10.1, "fault", color=INK, fontsize=7, va="bottom")
        ax.set_title(label, loc="left")
        ax.set_ylabel("requests started per s")
        ax.set_ylim(0, 12)
        ax.grid(axis="y", color=GRID, lw=0.5)
        ax.set_axisbelow(True)
    axes[0].legend(loc="upper right", ncol=2)
    axes[1].set_xlabel("seconds after the injection")
    axes[1].set_xlim(-6, 42)
    fig.tight_layout()
    fig.savefig(FIG / "exp1-timeline.png", dpi=200, facecolor="#ffffff")
    plt.close(fig)
    return FIG / "exp1-timeline.png"


def fig_scatter(kind, mode, ymax):
    plt = _mpl()
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.7), sharex=True, sharey=True)
    if mode == "stats":
        d = J(kind)
        pairs = [("Baseline", d["configs"]["baseline"]["records"], d["t0"], d["restore_s"]),
                 ("Improved", d["configs"]["improved"]["records"], d["t0"], d["restore_s"])]
    else:
        b, i = J("exp3-baseline-rep1"), J("exp3-improved-rep1")
        pairs = [("Baseline", b["records"], b["t0"], b["restore_s"]), ("Improved", i["records"], i["t0"], i["restore_s"])]
    for ax, (label, recs, t0, restore) in zip(axes, pairs):
        ax.axvspan(0, restore, color="#f0efec", zorder=0)
        ok = [(r["t"] - t0, r["ms"] / 1000) for r in recs if r["s"] in (200, 201)]
        bad = [(r["t"] - t0, r["ms"] / 1000) for r in recs if r["s"] not in (200, 201)]
        ax.scatter(*zip(*ok), s=9, color=OK_C, label="Succeeded", marker="o", linewidths=0)
        if bad:
            ax.scatter(*zip(*bad), s=14, color=FAIL_C, label="Failed", marker="x", linewidths=0.9)
        ax.set_title(label, loc="left")
        ax.set_xlabel("seconds after the injection (shaded: fault)")
        ax.grid(axis="y", color=GRID, lw=0.5)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("response time, s")
    axes[0].set_ylim(0, ymax)
    axes[0].legend(loc="upper right")
    fig.tight_layout()
    name = f"{kind}-latency.png"
    fig.savefig(FIG / name, dpi=200, facecolor="#ffffff")
    plt.close(fig)
    return FIG / name


def integrity_rows():
    out = []
    for c in ("baseline", "improved"):
        e2 = [J("exp2-" + n)["configs"][c]["integrity"] for n in ("rep1-stop", "rep2-stop", "rep3-freeze", "rep4-freeze")]
        out.append(["2", c, 4, sum(i["acknowledged"] for i in e2), sum(i["rows_in_db"] for i in e2),
                    sum(i["rows_in_db"] - i["distinct_client_id"] for i in e2), "yes" if all(i["pass"] for i in e2) else "no"])
    for c in ("baseline", "improved"):
        rs = [J(f"exp3-{c}-rep{r}") for r in (1, 2, 3)]
        out.append(["3", c, 3, sum(d["integrity"]["acknowledged"] for d in rs), sum(d["integrity"]["rows_in_db"] for d in rs),
                    sum(d["duplicates_db"] for d in rs), "yes" if all(d["integrity"]["pass"] for d in rs) else "no"])
    for c in ("baseline", "improved"):
        rs = [J(f"exp4-{c}-rep{r}") for r in (1, 2, 3)]
        out.append(["4", c, 3, sum(d["integrity_background"]["acknowledged"] + d["integrity_target"]["expected_rows"] for d in rs),
                    sum(d["integrity_background"]["rows_in_db"] + d["integrity_target"]["rows_on_account"] for d in rs),
                    sum(d["duplicates_db"] for d in rs),
                    "yes" if all(d["integrity_target"]["pass"] and d["integrity_background"]["pass"] for d in rs) else "no"])
    for c in ("baseline", "improved"):
        rs = [J(f"exp8-{c}-rep{r}") for r in (1, 2)]
        out.append(["8", c, 2, sum(d["integrity"]["acknowledged"] for d in rs), sum(d["integrity"]["rows_in_db"] for d in rs),
                    sum(d["duplicates_db"] for d in rs), "yes" if all(d["integrity"]["pass"] for d in rs) else "no"])
    return out


def consolidated_landscape():
    """Landscape section holding the consolidated table, then back to portrait."""
    sec = doc.add_section(WD_SECTION.NEW_PAGE)
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = sec.page_height, sec.page_width
    PASS, FAIL = "E2F0D9", "F8D7DA"
    fills = {}
    for i, r in enumerate(SUM_ROWS):
        if r[-1].startswith("PASS"):
            fills[(i, len(r) - 1)] = PASS
        elif r[-1].startswith("FAIL"):
            fills[(i, len(r) - 1)] = FAIL
    L("cons"); table("Consolidated results, baseline versus improved (`evidence/results/summary.md`, every value measured).",
                     SUM_HEAD, SUM_ROWS, [0.28, 0.55, 0.3, 1.0, 0.85, 0.75, 0.95, 0.5, 0.6, 0.65, 1.15, 1.05, 0.6],
                     size=6.5, fills=fills)
    P("\"comparison, no target\" means the plan set no target for that row. Baseline has no alert rules of its own, so "
      "Exp 6 has no baseline row. Exp 7 FAIL: see Section 10.9. Exp 5 baseline FAIL: see Section 10.7.", size=8, italic=True)
    sec2 = doc.add_section(WD_SECTION.NEW_PAGE)
    sec2.orientation = WD_ORIENT.PORTRAIT
    sec2.page_width, sec2.page_height = sec2.page_height, sec2.page_width

# ----------------------------------------------------------------------------- docx helpers

doc = None
counters = {"fig": 0, "tab": 0}
labels = {}


_pending = []


def L(label):
    _pending.append(label)


def T(label):
    return f"Table {labels.get(label, '?')}"


def F(label):
    return f"Figure {labels.get(label, '?')}"


def open_template():
    d = Document(str(TEMPLATE))
    body = d.element.body
    for el in list(body):
        if el.tag != qn("w:sectPr"):
            body.remove(el)
    # page number in the footer, after the template's text
    for p in d.sections[0].footer.paragraphs:
        if p.text.strip():
            r = p.add_run("  |  page ")
            r.font.size = Pt(8)
            fld = OxmlElement("w:fldSimple")
            fld.set(qn("w:instr"), "PAGE")
            rr = OxmlElement("w:r")
            rpr = OxmlElement("w:rPr")
            sz = OxmlElement("w:sz")
            sz.set(qn("w:val"), "16")
            rpr.append(sz)
            rr.append(rpr)
            t = OxmlElement("w:t")
            t.text = "1"
            rr.append(t)
            fld.append(rr)
            p._p.append(fld)
            break
    d.core_properties.title = "Final Project – Fault-Tolerant University Records Service"
    d.core_properties.author = "Shamil Yakupov (255193)"
    return d


def runs(p, text, size=None, bold=None, italic=None, color=None):
    """Inline markup: **bold**, `code`."""
    import re
    for part in re.split(r"(\*\*.+?\*\*|`.+?`|(?<![\w*])\*[^*\s][^*]*?\*(?![\w*]))", text):
        if not part:
            continue
        if part.startswith("**"):
            r = p.add_run(part[2:-2].replace("`", ""))
            r.bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            r = p.add_run(part[1:-1])
            r.italic = True
        elif part.startswith("`"):
            r = p.add_run(part[1:-1])
            r.font.name = "Consolas"
            r._element.rPr.rFonts.set(qn("w:hAnsi"), "Consolas")
            if size:
                r.font.size = Pt(size - 0.5)
            else:
                r.font.size = Pt(9)
        else:
            r = p.add_run(part)
        if size and not part.startswith("`"):
            r.font.size = Pt(size)
        if bold is not None and not part.startswith("**"):
            r.bold = bold
        if italic:
            r.italic = True
        if color:
            r.font.color.rgb = RGBColor.from_string(color)
    return p


def P(text, size=None, align=None, bold=None, italic=None, space_after=4, keep=False):
    p = doc.add_paragraph()
    runs(p, text, size=size, bold=bold, italic=italic)
    if align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == "justify":
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.space_after = Pt(space_after)
    if keep:
        p.paragraph_format.keep_with_next = True
    return p


def H1(t):
    h = doc.add_heading(t, level=1)
    h.paragraph_format.keep_with_next = True
    return h


def H2(t):
    h = doc.add_heading(t, level=2)
    h.paragraph_format.keep_with_next = True
    return h


def bullets(items, style="List Bullet"):
    for it in items:
        p = doc.add_paragraph(style=style)
        runs(p, it)
        p.paragraph_format.space_after = Pt(1)


def caption(text):
    p = doc.add_paragraph(style="Caption")
    runs(p, text)
    return p


def shade(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)


def cell_margins(table, top=30, bottom=30, left=60, right=60):
    tblPr = table._tbl.tblPr
    m = OxmlElement("w:tblCellMar")
    for k, v in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        e = OxmlElement(f"w:{k}")
        e.set(qn("w:w"), str(v))
        e.set(qn("w:type"), "dxa")
        m.append(e)
    tblPr.append(m)


def table(title=None, headers=None, rows=None, widths=None, size=8, fills=None, label=None):
    """Caption above, header row shaded like the A3/A4 reports, repeated on page breaks."""
    counters["tab"] += 1
    if label or _pending:
        labels[label or _pending.pop()] = counters["tab"]
    cp = doc.add_paragraph(style="Caption")
    runs(cp, f"Table {counters['tab']}. {title}")
    cp.paragraph_format.keep_with_next = True
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = doc.styles["Table Grid"]
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    cell_margins(t)
    for i, h in enumerate(headers):
        c = t.rows[0].cells[i]
        c.text = ""
        runs(c.paragraphs[0], h, size=size, bold=True)
        shade(c, "D9EAF7")
    for c in t.rows[0].cells:
        for hp in c.paragraphs:
            hp.paragraph_format.keep_with_next = True
    trPr = t.rows[0]._tr.get_or_add_trPr()
    th = OxmlElement("w:tblHeader")
    th.set(qn("w:val"), "true")
    trPr.append(th)
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            runs(cells[i].paragraphs[0], str(v), size=size)
            if fills and fills.get((ri, i)):
                shade(cells[i], fills[(ri, i)])
    for row in t.rows:
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for i, w in enumerate(widths):
            row.cells[i].width = Inches(w)
            for p in row.cells[i].paragraphs:
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.space_before = Pt(0)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def figure(path=None, text=None, width=6.6, label=None):
    counters["fig"] += 1
    if label or _pending:
        labels[label or _pending.pop()] = counters["fig"]
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_after = Pt(1)
    p.add_run().add_picture(str(path), width=Inches(width))
    c = caption(f"Figure {counters['fig']}. {text}")
    c.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return counters["fig"]


def code(text, size=7.5):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    pPr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "F2F4F7")
    pPr.append(shd)
    lines = text.strip("\n").split("\n")
    for i, line in enumerate(lines):
        r = p.add_run(line)
        r.font.name = "Consolas"
        r._element.rPr.rFonts.set(qn("w:hAnsi"), "Consolas")
        r.font.size = Pt(size)
        if i < len(lines) - 1:
            r.add_break()
    return p


def page_break():
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)




def set_doc(_d):
    pass


# ----------------------------------------------------------------------------- content

CONFIG_NOTE = ("Measured values below come from `evidence/results/` and `analysis/reliability-computed.json`. Values that are "
               "assumed or illustrative are labelled so, and are shown apart from the measurements.")


def title_block():
    P("**Final Project – Integrated Dependability Engineering Capstone**", size=20, align="center", space_after=2)
    P("**Fault-Tolerant University Records Service: Gateway, Observability, Security and Fault Injection, "
      "Baseline versus Improved**", size=12, align="center", space_after=6)
    P("Astana IT University | Fault Tolerance and Dependable Computing | Academic Year 2026–2027", italic=True,
      align="center", space_after=2)
    P("Student: Shamil Yakupov, ID 255193 | Instructor: Azamat Serek | Individual work", align="center", space_after=2)
    P("Experiments run on 6 November 2026 | Report date: 6 November 2026", align="center", space_after=8)
    P("Context note: all data in this project are synthetic. The system under test is a local Kubernetes lab; "
      "no real users, production systems or real university infrastructure were involved. The application is "
      "the hoWallet Go API, used (as in the midterm and endterm) only as a stand-in for a synthetic university service.",
      size=9, italic=True)
    P("Contribution: this is individual work with no other team members. Shamil Yakupov is responsible for all of it: "
      "requirements, design, configuration, scripts, experiments, analysis and this report.", size=9, italic=True)


def s1_abstract():
    H1("1. Abstract and Keywords")
    P("This project evaluates a dependable records service on a one-node Kubernetes lab, by comparing an improved "
      "configuration (nginx gateway with bounded retries, two API replicas, probes, drain on shutdown, three Prometheus "
      "alerts) with a retained baseline (one replica, no gateway). Both share one PostgreSQL and the same application "
      "code. Eight experiments from the assignment matrix were run with 2–4 repetitions per scenario under a test plan "
      f"written beforehand. Losing one API instance cost the baseline a {OUT1_MIN}–{OUT1_MAX} s outage after a graceful "
      "delete and 2.0 s after a forced one, and cost the improved configuration no failed request in 418. Under a 2 s "
      "delay on one replica's database link, the improved configuration kept all 247 requests successful, each bounded "
      "near 5 s, while the baseline lost 30 % with a maximum of 34 s. No acknowledged write was lost or duplicated in any "
      "run of either configuration; that result belongs to the application, so it does not separate the two. The "
      "comparison is not uniformly favourable: during a database outage the baseline answered more requests "
      "successfully (37 % vs 8 %); the gain there is bounded response time, not availability. The load result is partly "
      "an artefact of a per-process rate limiter, one access-control case is recorded as failed because its expected "
      "value was wrong, the rollback experiment shows no gain from the improved configuration, and PostgreSQL remains a "
      "single point of failure. Ranges describe behaviour on one machine and are not statistical estimates.",
      align="justify")
    P("**Keywords:** dependability, fault injection, availability, MTTR, bounded retries, idempotency, transaction "
      "rollback, Kubernetes, nginx, Prometheus, observability, access control.")


def s2_intro():
    H1("2. Introduction, Motivation and System Context")
    P("A records service for a university has a simple promise: a student's payment or registration entry is recorded "
      "exactly once, can be read back, and is visible only to the people allowed to see it. A short outage near a "
      "deadline is a nuisance; a fee charged twice or a payment lost is worse. The work therefore asks a narrow question: "
      "which failures does the improved configuration actually tolerate, at what cost, and what can the evidence not "
      "show? The assignment's dependability vocabulary and means follow Avizienis et al. [1].", align="justify")
    P("**System.** Option A of the assignment: the project continues the university information system from the "
      "midterm and endterm. The hoWallet Go API, written by the author as a personal-finance tracker, keeps the role of "
      "the university **Registration/Records** service, and PostgreSQL its data store. All accounts, households and "
      "transactions are synthetic lab records. The domain words in logs and tables come from the code: a *household* is "
      "the tenant (one student's record space), an *account* is a ledger, and a *transaction* is a record write (for "
      "example a fee payment). The system has more than three independently identifiable services: gateway, API, "
      "database, Prometheus, Grafana and a migration job, plus API modules (authentication, transactions, background "
      "jobs). The critical multi-step workflow is posting a record, which inserts a row and moves a ledger balance in "
      "one database transaction.", align="justify")
    P("**Relation to the endterm.** The manifests and scripts continue the endterm lab. Three things changed. The "
      "configuration label `endterm` is now `improved`. The gateway log also records `$upstream_status`, so retries can "
      "be counted; no timeout or retry value changed. The PostgreSQL memory limit went from 256 Mi to 1 Gi after an OOM "
      "kill (Section 9.5). Three experiments are new (Exp 3 network delay, Exp 4 interrupted transaction, Exp 8 "
      "rollback), and all eight were run again, so endterm figures are used only where stated.", align="justify")
    P("**Correction to the endterm.** The endterm reported a 23 s baseline outage after a replica loss. That figure was "
      "understated: its probe stopped while requests were still timing out, so it was a lower bound. Exp 1 now waits for "
      f"14.5 s of clean answers and measures **{OUT1_MIN}–{OUT1_MAX} s**. Section 10.3 gives the detail.",
      align="justify")


def s3_req():
    H1("3. Requirements, Assumptions and Dependability Objectives")
    H2("3.1 Users, workflow, data and consequences of failure")
    md_table("Users, workflow, data and consequences of failure (`notes/requirements.md`).", "notes/requirements.md",
             "## 1.", [1.3, 5.7], size=8)
    H2("3.2 Critical functions")
    md_table("Critical functions.", "notes/requirements.md", "## 2.", [0.6, 2.5, 3.0, 0.9], size=7,
             keep=[0, 1, 3, 4])
    H2("3.3 Service-level objectives")
    P("A request **fails** if it gets a 5xx, a timeout or a connection error. A 429 is the rate limiter working as "
      f"designed and is counted separately. The structure of the objectives follows [2]. {T('slo')} in Section 10.11 gives the measured outcome per objective.")
    md_table("Service-level objectives with the improved configuration as the target.", "notes/requirements.md", "## 3.",
             [0.5, 1.35, 1.75, 1.7, 0.75, 0.95], size=7)
    H2("3.4 Assumptions")
    md_list("notes/requirements.md", "## 4.")


def s4_fault():
    H1("4. Fault Model, Threat Model and Failure Analysis")
    H2("4.1 Terminology and fault model")
    md_table("Fault, error and failure as used here, after Avizienis et al. [1].", "notes/requirements.md", "## 5.",
             [1.0, 2.8, 3.2], size=7, rows=3)
    md_table("Fault model.", "notes/requirements.md", "## 6.", [2.0, 2.7, 1.4, 0.9], size=7.5)
    H2("4.2 Failure scenarios and impact")
    P("Severity: **H** = data integrity or confidentiality at risk, **M** = critical function unavailable, **L** = "
      "degraded but correct.")
    md_table("Failure scenarios and failure-impact analysis.", "notes/requirements.md", "## 7.",
             [0.3, 0.68, 0.92, 0.9, 1.0, 0.6, 0.95, 1.35], size=6.5)
    H2("4.3 Threat model")
    P("The STRIDE analysis [16] with code references is in `notes/threat-model.md`. Assets: records and balances, "
      "credentials, the JWT signing secret, tenant membership and the audit trail. Actors: A1 anonymous attacker, A2 "
      "authenticated user of another tenant, A3 compromised pod, A4 abusive or buggy client.", align="justify")
    table("Threats per entry point, controls and residual risk (condensed from `notes/threat-model.md`).",
          ["Entry point / actor", "Threat (STRIDE)", "Control", "Residual risk"],
          [["/auth/* (A1)", "S: brute force, credential stuffing",
            "10 req/min per IP on /auth; lockout after 5 failures per (e-mail, IP) for 15 min; bcrypt",
            "counters live in each replica's memory (Exp 7: first 429 at attempt 11, not 6)"],
           ["/auth/* (A1)", "S/T: forged JWT", "HMAC only, issuer and expiry required; same 401 for all failures",
            "whoever holds JWT_SECRET can mint tokens (A3)"],
           ["Household API (A2)", "I/T: reading or changing another tenant through X-Household-ID or foreign ids",
            "membership check → 404; queries scoped by household_id", "tested in Exp 7"],
           ["Household API (A4)", "T: duplicate writes on retry", "client_id idempotency key + unique index",
            "only POST /api/transactions has it"],
           ["Household API (A4)", "D: flooding, huge bodies",
            "600/min per user, 1200/min per IP, 60/min creates; 2 MB at the gateway, 1 MB decode",
            "per-replica limits; no statement_timeout"],
           ["Gateway (A1)", "S: spoofed X-Real-IP to dodge limits", "gateway overwrites X-Real-IP, clears True-Client-IP",
            "the baseline, with no gateway, honours the header (Exp 7)"],
           ["Any pod (A3)", "E/I: lateral movement", "NetworkPolicies, non-root, read-only root filesystem",
            "API database user owns all tables; logs in the same database; not tested in Exp 7"],
           ["All (A1–A4)", "R: denying actions", "JSON request log with request_id and user_id; audit_logs; app_logs",
            "no dedicated failed-login event; audit queue drops under load"]],
          [1.15, 1.75, 2.3, 1.8], size=7.5)
    md_table("Misuse scenarios tested in Exp 7.", "notes/threat-model.md", "## 0.", [0.4, 2.2, 0.45, 1.05, 1.6, 1.3], size=7.5)


def s5_rel():
    H1("5. Reliability Engineering Calculations")
    P("All values in Sections 5.1–5.3 are **measured-final**: computed by `python analysis/reliability.py` from "
      "`evidence/results/reliability-inputs.json` and read from `analysis/reliability-computed.json` when this report is "
      "built. Section 5.4 is **illustrative** and is not a measurement.", align="justify")
    H2("5.1 Formulas, units and windows")
    md_table("Symbols.", "notes/reliability.md", "## 1.", [1.3, 4.2, 1.5], size=7.5, which=0)
    md_table("Quantities.", "notes/reliability.md", "## 1.", [1.15, 2.2, 1.0, 2.65], size=7.5, which=1)
    P("Repetitions are pooled: `T_obs`, `N_f`, `D`, `ok` and `failed` are summed, then the formulas are applied. With "
      "`N_f = 0` the window is right-censored: MTTF is reported as `> T_obs`, MTTR and MTBF as undefined, `A = 1`. Zero "
      "failures in a short window does not show that λ is zero; the rule of three gives a rough 95 % upper bound of "
      "`λ < 3 / T_obs` for a constant-rate process [4].", align="justify")
    bullets([
        "**Window:** from the first client request to the end of the last one in the run, including the time before "
        "the fault. Each window encloses one injected fault.",
        "**Failure interval:** from the start of the first failed request to the end of the last failed request of an "
        "episode (client-visible downtime). Failed requests within 2.0 s are merged. Resolution is limited by the probe "
        "rate, and the length of a failed request, including the client's timeout, counts towards `D`.",
        "**Observed failure rate basis:** `λ = N_f / T_obs`, under the conditions of the window.",
        "**MTTF, MTBF and λ here mostly reflect injection timing, not system reliability.** The experimenter chooses when "
        "the fault happens, so MTTF is largely the lead time before the injection and λ is close to 1 / `T_obs`. They "
        "are computed because the assignment asks for them, and must not be extrapolated to a field value "
        "(`notes/reliability.md` §3). The comparable quantities are MTTR, `D`, `R_req`, and `A` between runs with equal "
        "windows and fault duration.",
    ])
    H2("5.2 Measured results")
    rows = []
    for k, r in REL.items():
        cens = r["censored"]
        rows.append([k[0], SCEN.get(k[0], ""), k[1], r["reps"], f"{r['T_obs_s']:.1f}", r["N_f"], f"{r['downtime_s']:.1f}",
                     "undefined" if cens else f"{r['MTTR_s']:.1f}",
                     f"> {r['T_obs_s']:.1f}, censored" if cens else f"{r['MTTF_s']:.1f}",
                     "undefined" if cens else f"{r['MTBF_s']:.1f}", f"{r['A_time']*100:.2f} %",
                     f"{r['lambda_per_h']:.0f}", f"{r['requests_ok']} / {r['requests_failed']}",
                     f"{r['R_req']*100:.2f} %"])
    rows.append(["7", SCEN["7"], "–", "–", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a",
                 "reported in 10.9"])
    L("rel"); table("Measured-final reliability quantities, pooled per experiment and configuration. λ is per hour of "
                    "window and conditional on injected faults.",
                    ["Exp", "Scenario", "Config", "Reps", "T_obs (s)", "N_f", "D (s)", "MTTR (s)", "MTTF (s)", "MTBF (s)",
                     "A", "λ (1/h)", "ok / failed", "R_req"],
                    rows, [0.28, 0.72, 0.56, 0.3, 0.44, 0.26, 0.4, 0.5, 0.65, 0.5, 0.52, 0.34, 0.6, 0.5], size=6.5)
    pr = {x["rep"]: x for x in REL[("1", "baseline")]["per_run"]}
    g1, g2, f = pr["exp1-baseline-rep1-graceful"], pr["exp1-baseline-rep2-graceful"], pr["exp1-baseline-rep3-crash"]
    H2("5.3 Worked calculation and interpretation")
    P(f"Exp 1 baseline, repetition 1 (`exp1-baseline-rep1-graceful`): `T_obs` = {g1['T_obs_s']} s, `N_f` = {g1['N_f']}, "
      f"`D` = {g1['downtime_s']:.2f} s, `ok` = {g1['requests_ok']}, `failed` = {g1['requests_failed']}.", keep=True)
    code(f"""U       = {g1['T_obs_s']} - {g1['downtime_s']:.2f}        = {g1['uptime_s']:.2f} s
MTTR    = {g1['downtime_s']:.2f} / 1            = {g1['MTTR_s']:.2f} s
MTTF    = {g1['uptime_s']:.2f} / 1            = {g1['MTTF_s']:.2f} s   (time around the outage, not a lifetime)
MTBF    = {g1['MTTF_s']:.2f} + {g1['MTTR_s']:.2f}        = {g1['MTBF_s']:.2f} s
A       = {g1['uptime_s']:.2f} / {g1['T_obs_s']}        = {g1['A_time']*100:.2f} %
lambda  = 1 / {g1['T_obs_s']} s          = {g1['lambda_per_h']:.0f} per hour  (one injected fault in this window)
R_req   = {g1['requests_ok']} / ({g1['requests_ok']} + {g1['requests_failed']})      = {g1['R_req']*100:.2f} %""", size=8)
    b1, i1 = REL[("1", "baseline")], REL[("1", "improved")]
    b2, i2 = REL[("2", "baseline")], REL[("2", "improved")]
    bullets([
        f"**Exp 1.** The pooled MTTR of {b1['MTTR_s']:.1f} s describes neither behaviour: graceful delete {g1['MTTR_s']:.1f} s "
        f"and {g2['MTTR_s']:.1f} s, forced delete {f['MTTR_s']:.1f} s. Improved has no failure episode, so only a lower bound "
        f"of {i1['T_obs_s']:.1f} s on MTTF can be stated (rule of three: λ < {3/i1['T_obs_s']*3600:.0f} per hour, about this "
        "short window).",
        f"**Exp 2 favours the baseline** on `A` ({b2['A_time']*100:.2f} % vs {i2['A_time']*100:.2f} %) and `R_req` "
        f"({b2['R_req']*100:.2f} % vs {i2['R_req']*100:.2f} %). Section 10.4 explains why this is not an availability gain "
        "for the baseline and not one for the improved configuration either.",
        "**Exp 3.** Slow successful requests are not downtime in this calculation. Improved has `D` = 0, yet its p95 is "
        "about 5 s, so read this row with its latency.",
        "**Exp 5.** 429 is excluded from `R_req`, so the baseline's 98.69 % does not show that 1086 requests were refused "
        "in the whole window.",
        "**Exp 6** pools four different scenarios and has no baseline; the scenario rows of Table " +
        str(labels.get("cons", "?")) + " are the useful ones.",
    ])
    H2("5.4 Theoretical estimate (illustrative, not measured)")
    P("**Every value in this table is illustrative.** The per-instance availability of 0.999 is an assumption chosen to "
      "show the shape of the calculation. It was not measured and must not be cited as a result. Assuming independent "
      "failures, `A_series = A1 × A2 × …` and `A_parallel = 1 − (1 − A)^n`.", align="justify", keep=True)
    t = md_table("ILLUSTRATIVE series and parallel availability, not measured.", "notes/reliability.md", "## 7.",
                 [2.7, 3.2, 1.1], size=8, fill="FFF4CE")
    P("Redundancy halves the illustrative unavailability from 0.2 % to 0.1 %, and what remains is almost entirely the "
      "single database, which agrees with the SPOF analysis of Section 6.5. Section 10.12 explains why measured results "
      "differ from this calculation.", align="justify")


def s6_arch():
    H1("6. System Architecture and Resilience Design")
    H2("6.1 Service boundaries and data flows")
    L("arch"); figure(FIG / "architecture.png", "Lab architecture: improved path (gateway → API × 2 → PostgreSQL) with "
                      "monitoring, and the baseline path at the bottom sharing the same database. Source: "
                      "`scripts/architecture.html`.", 6.9)
    P("**AI-generated figure.** The diagram was generated with the help of AI (Claude Code, as an HTML/SVG page "
      "rendered to PNG). It was carried over from the endterm, its labels were updated for this report, and it was "
      "checked by the author against the manifests in `k8s/`. The generation prompt is given in Appendix 14.6.",
      size=8, align="center")
    md_table("Services (`notes/architecture.md`).", "notes/architecture.md", "## 2.",
             [1.0, 1.15, 1.4, 1.2, 0.95, 1.3], size=7)
    md_table("Dependencies and failure containment.", "notes/architecture.md", "## 4.", [1.35, 1.45, 0.7, 3.5], size=7.5)
    P("**Critical workflow, post a record.** (1) The client sends `POST /api/transactions` with a bearer token, "
      "`X-Household-ID` and `client_id`. (2) The gateway sets `X-Request-Id`, overwrites `X-Real-IP` and proxies to one "
      "pod; it does not replay the POST once sent [10]. (3) API middleware runs: request id, logger, rate limit, JWT, live "
      "session, per-user limit, verified e-mail, membership. (4) `CreateOnce` looks up `client_id`; on a miss it runs one "
      "database transaction with the insert and the balance update [13]. (5) Audit, budget notification and cashback run "
      "after the commit, not atomically with it. (6) The API answers 201, or 200 for a replay.", align="justify")
    H2("6.2 Redundancy and backup strategy")
    md_table("Redundancy and backup.", "notes/architecture.md", "## 6.", [1.15, 2.4, 1.6, 1.85], size=7.5)
    P("Backup is by the earlier drill only: `pg_dump` and `pg_restore` [14] were exercised in the `ftr-a3` lab (restore in "
      "19.2 s on a very small database). No restore was run in the final project, and SLO-9 is therefore not tested.",
      align="justify")
    H2("6.3 Baseline versus improved")
    P("Both configurations run side by side, use one image and share one PostgreSQL. The application mechanisms "
      "(rollback, idempotency, rate limits, validation) exist in both, so the comparison measures infrastructure "
      "mechanisms, not the application's.", align="justify")
    L("bvi"); md_table("Exactly which mechanisms differ.", "notes/architecture.md", "## 8.", [1.6, 2.6, 2.8], size=7.5)
    H2("6.4 Deployment, restart and resource assumptions")
    md_table("Resource and load assumptions.", "notes/architecture.md", "## 9.", [1.5, 5.5], size=8)
    H2("6.5 Single points of failure")
    md_table("Single points of failure.", "notes/requirements.md", "## 8.", [1.6, 0.8, 2.3, 2.3], size=7.5)


def s7_impl():
    H1("7. Infrastructure and Software Fault-Tolerance Implementation")
    H2("7.1 Infrastructure-level mechanisms")
    table("Infrastructure mechanisms demonstrated.",
          ["Mechanism", "Configuration", "Demonstrated by"],
          [["Instance redundancy with load balancing", "API × 2 and gateway × 2 (`k8s/30-api.yaml`, `k8s/40-gateway.yaml`); "
            "per-pod upstream through the headless Service [11]", "Exp 1: 418/418 with one pod deleted; 25.0–25.6 s or 2.0 s "
            "outage without it"],
           ["Self-healing: restart and replacement", "ReplicaSet replaces a deleted pod; liveness 10 s × 3 restarts a hung "
            "one [5]", "Exp 1 recovery 1.3–1.7 s; Exp 6 hung replica back at 43.2–45.8 s"],
           ["Rolling update and rollback", "`maxUnavailable 0`, `maxSurge 1`, `kubectl rollout undo` [6]",
            "Exp 8: old pods kept serving. **A default RollingUpdate with one replica did the same**, so this adds nothing "
            "measured over the default"],
           ["Drain on shutdown", "preStop `sleep 5`, grace period 20 s [7]", "Exp 1 graceful runs: no failed request. Cost: "
            "bad pod takes 6.1–6.5 s to go in Exp 8, against 0.8–1.9 s"],
           ["Database restart in place", "StatefulSet, PVC; readiness `pg_isready`", "Exp 2: recovers without intervention. "
            "No failover: PostgreSQL stays a SPOF"]],
          [1.8, 2.7, 2.5], size=7.5)
    H2("7.2 Software-level mechanisms")
    P("Six mechanisms already in the code or lab configuration are counted; none was added for this project. Go "
      "references are to the hoWallet repository at commit `06aea177`; the test output is in `analysis/ft-unit-tests.txt` "
      "(20 unit tests: **20 passed, 0 failed**).", align="justify")
    md_table("Software fault-tolerance mechanisms, references and tests (`notes/software-ft.md`).",
             "notes/software-ft.md", "## Summary", [0.3, 1.35, 1.5, 2.0, 1.2, 0.65], size=7)
    bullets([
        "**M1 gateway timeouts and bounded retries.** Connect 1 s, read 5 s, at most 2 tries, idempotent methods only [10]. "
        "This is the outer layer in Nygard's sense [3]; there is no circuit breaker inside the API. `max_fails=1 "
        "fail_timeout=2s` is passive ejection, and is treated as part of M1. **M1 has no Go test: it is nginx configuration, "
        "and Exp 1, 2 and 3 are its evidence.**",
        "**M2 idempotent create via `client_id`.** The lookup comes first, so a retry returns the first row with no side "
        "effects; a concurrent loser hits the unique index, rolls back and reads the winner's row. 16 unit tests ran and "
        "passed. Integration tests against PostgreSQL were cited, not run. Limit: only `POST /api/transactions` has a key.",
        "**M3 database transaction rollback.** `RunInTx` commits only if the function returns no error. **Its unit test "
        "`TestRegister_TxError` proves only that the error propagates; the integration tests were cited, not run. Exp 4 is "
        "the evidence.** Limit: audit, budget notification and cashback run after the commit.",
        "**M4 health and readiness checks.** **No Go test of the probe behaviour: Exp 1 and Exp 6 are the evidence.** "
        "Limit: `/health` does not touch the database, so readiness stays green during a database outage or a slow "
        "database link (Exp 2, Exp 3).",
        "**M5 graceful degradation.** JSON 503 with `Retry-After` instead of an HTML page or a hang. Limit: during a "
        "database outage the API's own JSON 500 passes through the gateway unchanged. The `no_rate` path was shown in the "
        "endterm and was not repeated here.",
        "**M6 rate limiting with `Retry-After`** (`httprate` [18]). Limit: counters live in each replica's memory (Exp 5, Exp 7).",
    ])
    code("""upstream api_upstream {
  zone api_upstream 64k;
  server api-headless.university.svc.cluster.local:8080 resolve max_fails=1 fail_timeout=2s;   # line 35
}
location / {
  proxy_set_header X-Real-IP $remote_addr;   proxy_set_header True-Client-IP "";
  proxy_set_header X-Request-Id $req_id;     # adopted or minted correlation id
  proxy_connect_timeout 1s;  proxy_read_timeout 5s;                                            # lines 59-60
  proxy_next_upstream error timeout http_502 http_503 http_504;  proxy_next_upstream_tries 2;  # lines 66-67
  error_page 502 503 504 = @degraded;   error_page 413 = @too_large;                           # JSON bodies
}""")
    caption("Listing 1. Gateway excerpt (`k8s/40-gateway.yaml`).")
    H2("7.3 Dependency-failure behaviour")
    P("If an API pod dies, the gateway retries GET on the other replica and answers JSON 503 if neither answers. If "
      "PostgreSQL stops, the membership check fails closed with a JSON 500, and requests that wait past 5 s get a JSON 503 "
      "from the gateway. Session liveness fails open on a database error, a deliberate choice to avoid mass sign-out, "
      "and a stated limitation. If the exchange-rate provider is down, base-currency writes are unaffected.",
      align="justify")


def s8_obs():
    H1("8. Monitoring, Observability and Security Implementation")
    H2("8.1 Metrics and dashboard")
    table("Metrics (metrics patch on `feat/metrics`, enabled by `METRICS_ENABLED=true`; hoWallet `main` has no metrics endpoint).",
          ["Metric", "Type / labels", "Used for"],
          [["http_requests_total", "counter {method, route, status}", "request rate, error-rate alert"],
           ["http_request_duration_seconds", "histogram {method, route}", "p50, p95, p99; latency alert"],
           ["http_requests_in_flight", "gauge", "saturation"],
           ["db_pool_acquired_conns, db_pool_total_conns", "gauges from pgxpool", "pool starvation"],
           ["up (Prometheus)", "per scraped pod", "ApiReplicaDown alert"]],
          [2.7, 2.2, 2.1], size=8)
    P("Prometheus scrapes every 2 s and evaluates rules every 2 s, discovering API pods per replica through DNS on the "
      "headless Service. The Grafana dashboard `howallet-endterm` (dashboard name kept from the endterm) shows only "
      "`job=\"api\"`, so baseline numbers come from the result files.", align="justify")
    figure(crop_shot("grafana-exp6-replica-down-rep1.png", (0, 0, 1600, 925), "g-replica.png"),
           "Grafana during the Exp 6 hung-replica series: one instance's `up` falls to 0, the firing table shows "
           "ApiReplicaDown, the error rate panel stays at 0 %.", 5.8)
    figure(crop_shot("grafana-exp2-rep1-stop.png", (0, 0, 1600, 925), "g-db.png"),
           "Grafana during Exp 2 rep 1 (database stopped): p95 and p99 reach the 5–10 s bound, 500 and 499 responses appear, "
           "ApiHighLatencyP95 fires. Replicas stay up, since `/health` does not touch the database.", 5.8)
    H2("8.2 Structured logs and request correlation")
    P("Both tiers log one JSON object per request. nginx adopts an incoming `X-Request-Id` or mints one and forwards it; "
      "the API adopts it, writes it to its log line with `user_id` and `household_id`, and persists the same lines to the "
      "`app_logs` table. Samples below are from `evidence/log-samples.txt`, shortened; identifiers are synthetic.",
      align="justify")
    code("""# Exp 3 improved: slow replica cut at 5 s, retried on the healthy one; client sees 200
gateway {"request_id":"exp3-improved-rep3-25","method":"GET","uri":"/api/accounts","status":200,
         "upstream_addr":"10.244.0.7:8080, 10.244.0.8:8080","request_time":5.005,"upstream_status":"504, 200"}
api     {"level":"ERROR","msg":"request","status":500,"duration":5000730056,"request_id":"exp3-improved-rep3-25"}
api     {"level":"INFO","msg":"request","status":200,"duration":2626313,"request_id":"exp3-improved-rep3-25"}
# Exp 4 improved: interrupted POST is not replayed; the client's retry with the same client_id succeeds
gateway {"request_id":"exp4-improved-rep3-try1","method":"POST","status":503,"upstream_status":"502"}
gateway {"request_id":"exp4-improved-rep3-try2","method":"POST","status":201,"upstream_status":"201"}
# Exp 7: denied request, one id in both logs
gateway {"request_id":"exp7a-r2-05","method":"GET","uri":"/api/accounts","status":404,"upstream_status":"404"}
api     {"level":"INFO","msg":"request","status":404,"request_id":"exp7a-r2-05","user_id":"9fe8558a-…"}""", size=7)
    caption("Listing 2. Correlated log lines, shortened from `evidence/log-samples.txt`.")
    H2("8.3 Alert rules and incident indicators")
    table("Alert rules (`k8s/50-prometheus.yaml`) [15] and observed firing delay from the injection (Exp 6).",
          ["Alert / severity", "Expression (summary)", "for", "Indicates", "Firing delay measured"],
          [["ApiHighErrorRate / critical", "5xx rate / all rate > 0.05 over 30 s, job=api, route≠/health", "10 s",
            "server-side failures", "14.2–16.2 s (database stopped)"],
           ["ApiHighLatencyP95 / warning", "histogram p95 over 30 s > 0.5 s", "10 s", "slow responses, locks, saturation",
            "16.2 s (lock bursts)"],
           ["ApiReplicaDown / critical", "(count(up{job=api} == 1) < 2) or absent(up{job=api} == 1)", "6 s",
            "fewer than 2 healthy replicas", "11.2–12.2 s (hung replica)"]],
          [1.5, 2.5, 0.35, 1.3, 1.35], size=7.5)
    figure(crop_shot("prom-alerts-exp6-replica-down-rep1-ApiReplicaDown-firing.png", (0, 60, 1600, 590), "p-replica.png"),
           "Prometheus alert page: ApiReplicaDown firing in Exp 6 rep 1 (screenshot taken by `AlertWatcher` during the run).",
           5.6)
    P("**How an incident is diagnosed.** An alert goes pending, then firing. The dashboard separates the cases: error "
      "rate up with replicas up points to a dependency; replicas below 2 points to a pod; latency up with no errors "
      "points to contention. The `request_id` from the client or the gateway log then finds the API line, and "
      "`upstream_status` shows the retries. **Gap measured in Exp 8: no alert watches rollouts**, so a stuck rollout is "
      "invisible to monitoring.", align="justify")
    H2("8.4 Security controls")
    md_table("Security controls (`notes/security-controls.md`).", "notes/security-controls.md", "## Summary",
             [0.3, 2.3, 0.85, 2.0, 0.45, 1.1], size=7)
    P("Controls map to OWASP API Security Top 10 2023 risks [17], mainly API1 (object level authorisation), API2 "
      "(authentication) and API4 (resource consumption). Secrets in the manifests are synthetic lab placeholders. Known "
      "weaknesses are stated: limits and lockout are per replica, session liveness fails open on a database error, there "
      "is no dedicated failed-login event, and the API's database user owns every table. NetworkPolicies [8] and pod "
      "hardening were deployed but not tested in Exp 7.", align="justify")


def s9_method():
    H1("9. Verification, Validation and Experimental Methodology")
    H2("9.1 Environment and configurations")
    P("One-node kind cluster [9] `ftr-endterm` (name kept from the endterm) on Docker Desktop, Windows 11 laptop, WSL2, "
      "namespace `university`. All traffic comes from one Python client on the same machine, through `127.0.0.1` "
      f"NodePorts. Valid runs are from 6 November 2026, about 16:08–17:08 local time. The hypotheses, workloads, durations "
      f"and criteria in `evidence/test-plan.md` were written before the runs. The configurations differ as in Table "
      f"{labels.get('bvi', '?')}.", align="justify")
    H2("9.2 Tooling and repetitions")
    bullets([
        "`scripts/run_exp.py <1..8>` runs all repetitions of one experiment and appends the record to `evidence/expN.txt`; "
        "raw data lands in `evidence/results/expN-*.json`. `bash run_all.sh` runs all eight.",
        "`scripts/ftlab.py`: an open-loop probe (a hanging request does not slow it), an endpoint watcher and an alert "
        "watcher polling Prometheus every second. Each run first waits for all workloads ready and no alert active.",
        "`scripts/faults.py` (Exp 1, 2), `scripts/final.py` (Exp 3, 4, 8), `scripts/exps.py` (Exp 5, 6, 7), "
        "`scripts/build_summary.py` (consolidated table and reliability inputs). `tc netem` [12] injects the delay.",
    ])
    H2("9.3 Test plan")
    table("Experiment matrix as run (`evidence/test-plan.md`). B = baseline, I = improved.",
          ["Exp", "Question", "Fault", "Workload", "Reps", "Hypothesis written before the run", "Success criteria"],
          [["1", "Does another instance keep the workflow available?", "Delete one API pod: graceful, graceful, forced",
            "GET /api/accounts, open loop, 8 req/s", "3 per config",
            "I: at most a few failed requests per pod loss. B: outage until the replacement is ready",
            "I availability ≥ 99 % after injection, ≥ 99.9 % for graceful. B: no target"],
           ["2", "Fail safely, recover, no corruption?", "Postgres scaled to 0 (2 runs) or SIGSTOP (2 runs), 20 s",
            "GET at 6 req/s per config, timeout 12 s, plus one create per 1.5 s retried with same client_id",
            "4, both configs at once", "Both fail and recover with no lost or duplicate write. I within about 10 s, B waits "
            "to the 12 s client timeout. An alert fires (I)",
            "One row per acknowledged write, balance = −Σ, recovery ≤ 30 s, I errors JSON, alert fires"],
           ["3", "Are timeouts and retries bounded and effective?", "`tc netem delay 2000ms` to port 5432 in one API pod, 40 s",
            "GET at 2 req/s, client timeout 60 s, plus one create per 1.5 s", "3 per config",
            "I: cut at 5 s and retried once, ≥ 99 %, max ≤ 12 s; readiness will not remove the replica. **B: every request "
            "about 8–10 s**",
            "I: delayed packets seen, ≥ 99 %, max ≤ 12 s, ≤ 2 tries, writes intact, latency normal ≤ 30 s after removal. B: no "
            "target"],
           ["4", "Recover without losing or duplicating a record?",
            "SIGKILL the API process while its create waits between insert and balance update (row lock held by a test session)",
            "2 creates, the interrupted create retried every 0.5 s; GET at 4 req/s; background creates", "3 per config",
            "Rollback leaves nothing, retry gives one row in both. I retry in about 1–2 s; B only after the restart",
            "1 row for the id, 3 rows on the account, balance = −Σ, no duplicate anywhere, retry ≤ 60 s (I ≤ 10 s)"],
           ["5", "How do throughput, latency and errors change?", "None: load 5 → 30 → 5 req/s, 30 + 60 + 30 s",
            "GET, 5 users, timeout 6 s; 429 counted apart", "2 per config",
            "I serves all with p95 < 500 ms. **B also holds this rate**, so the difference may be small",
            "≥ 99 % excluding 429, p95 < 500 ms in the step, no alert"],
           ["6", "Detected and diagnosed from telemetry?",
            "Hung replica (SIGSTOP); database stopped 25 s; 22 lock bursts over about 40 s; quiet window",
            "GET at 6 req/s (quiet 5 req/s); Prometheus polled every 1 s", "3, 2, 2, 2 (I only)",
            "Each fault fires its alert within about 45 s; none in the quiet window; other alerts may also fire",
            "Expected alert firing ≤ 45 s, clears after recovery, zero in quiet"],
           ["7", "Access control and validation enforced and logged?",
            "None: 11 access-control cases, input cases, 12 wrong passwords",
            "Sequential requests; 5 req/s probe during input cases", "2 per set",
            "401, 404, 400/413, 429; data unchanged; each request has both log lines; X-Real-IP spoof not honoured through "
            "the gateway",
            "Each case returns its expected status; md5 snapshot unchanged; all logged"],
           ["8", "Return to an acceptable state?", "`set env API_PORT=9999`, 45 s, then `rollout undo`",
            "GET at 5 req/s, timeout 12 s, plus one create per 1.5 s", "2 per config",
            "I: no failed request. **B: probably the same.** Monitoring will not notice the stuck rollout",
            "≥ 99 %, bad pod never ready, template restored, recovery ≤ 60 s, writes intact"]],
          [0.25, 1.0, 1.25, 1.2, 0.45, 1.55, 1.3], size=6.5)
    P("**Recorded per run:** fault, start and end, detection time, recovery time, successful and failed requests, latency "
      "p50, p95 and max, retries and failovers, data-integrity result. Each run records whether the fault was really in "
      "place. Client retries reuse the same `client_id`. Integrity is checked in SQL: rows against acknowledged writes, "
      "distinct `client_id`, `balance = −Σ amounts`, and no duplicate `(household_id, client_id)` anywhere.",
      align="justify")
    H2("9.4 Automated tests")
    table("Tests relevant to this project.",
          ["Level", "Count", "Status in this project"],
          [["Unit tests of the fault-tolerance mechanisms", "20", "**Run** on 6 Nov 2026 at 06aea177: 20 passed "
            "(`analysis/ft-unit-tests.txt`)"],
           ["All Go unit tests", "2095 top-level", "Run on 4 Nov 2026 at 06aea177: 0 failed (`notes/test-inventory.md`)"],
           ["Integration tests on real PostgreSQL", "278 (static count)", "**Cited, not run**: Docker was busy with the lab"],
           ["API tests over a real app and database", "538 (static count)", "**Cited, not run**"]],
          [3.0, 1.1, 2.9], size=8)
    H2("9.5 Invalid runs and amendments")
    P("Negative findings about the lab are kept in the record. `evidence/anomalies.txt` and the two dated amendments in "
      "`evidence/test-plan.md` are the sources.", align="justify")
    table("Runs set aside, why, and what was done.",
          ["Run", "Why", "Action"],
          [["`exp4-*-rep1-INVALID-kill-not-effective` (2)", "SIGKILL had no effect: restart count unchanged, first POST "
            "answered 201", "Kept, excluded, rerun"],
           ["`exp8-improved-rep1-INVALID-confounded-postgres-oom`", "Postgres OOM-killed at its 256 Mi limit (exit 137, peak "
            "268 MB, 68 connections); availability 3.61 % in the fault window. It measured the memory limit, not the rollout",
            "Kept, excluded. Dated environment change to 1 Gi, for both configs. All later runs share one environment"],
           ["`results/pre-pg-memory-fix/` (Exp 3, Exp 4 rep 1)", "Valid measurements of a different environment (256 Mi)",
            "Set aside, not used; rerun"],
           ["`results/superseded/` (Exp 1 × 6, Exp 6 latency × 2)", "Script defects: Exp 1 stopped too early; the alert "
            "watcher could stop just before a clear", "Set aside; stop conditions fixed before the repeats. No hypothesis or "
            "criterion changed"]],
          [2.3, 2.8, 1.9], size=7.5)
    P("**Finding: PostgreSQL at 256 Mi.** After the limit went to 1 Gi, the same broken release ran at 100 % availability. "
      f"That supports memory pressure as the cause but does not prove it: it rests on one run pair. Two further readings "
      f"limit the claim. At 1 Gi, the Exp 5 improved runs show a Postgres working set of {PG_WS_MIN:.1f}–{PG_WS_MAX:.1f} MiB, "
      "close to the old limit, and working set includes reclaimable cache. A `memory.peak` of 210 MB recorded at 17:01 "
      "covers only the time since Postgres last restarted (Exp 6 stopped it), so it says nothing about the Exp 8 runs. The "
      "endterm also saw a Postgres OOM kill, under normal lab load.", align="justify")


def s10_results():
    H1("10. Results and Baseline-versus-Improved Comparison")
    P("Every value here is measured, on 6 November 2026, unless labelled otherwise. Ranges are min–max across valid "
      "repetitions. With 2–4 repetitions they describe behaviour and are not statistical estimates.", align="justify")
    H2("10.1 Consolidated results table")
    P(f"Table {labels.get('cons', '?')} on the next page is `evidence/results/summary.md`, reproduced by script when this "
      "report is built. Experiment 6 has no baseline row because the baseline has no alert rules of its own.",
      align="justify")
    consolidated_landscape()
    H2("10.2 Required metrics by experiment")
    rows = []
    for k, r in REL.items():
        sm = SUMMARY_BY.get(k, {})
        rows.append([k[0], k[1], f"{r['R_req']*100:.2f} %", f"{r['A_time']*100:.2f} %", f"{r['lambda_per_h']:.0f} per hour, "
                     f"{r['N_f']} in {r['T_obs_s']:.1f} s", "undefined" if r["censored"] else f"{r['MTTR_s']:.1f} s",
                     sm.get("det", "–"), sm.get("rec", "–")])
    table("Availability, failure rate, recovery and detection. `R_req` and `A` are over the whole window; "
          "the consolidated table gives the fault window.",
          ["Exp", "Config", "R_req", "A", "Observed failure rate, basis", "MTTR", "Detection (s)", "Recovery (s)"],
          rows, [0.3, 0.6, 0.55, 0.55, 1.45, 0.6, 1.6, 1.35], size=7)

    H2("10.3 Exp 1: application instance failure")
    rows = []
    for c in ("baseline", "improved"):
        for rep, m in ((1, "graceful"), (2, "graceful"), (3, "crash")):
            d = J(f"exp1-{c}-rep{rep}-{m}")
            s = d["stats_after_injection"]
            rows.append([c, f"{rep} {'forced' if m == 'crash' else m}", f"{s['success']}/{s['requests']}", s["lat_p50_ms"],
                         s["lat_p95_ms"], s["lat_max_ms"], d["detection"]["endpoint_drop_s"],
                         d["recovery"]["endpoints_back_s"], d["recovery"]["recovery_s"],
                         d["recovery"]["user_visible_outage_s"]])
    table("Exp 1 per run, after injection, probe 8 req/s. Latency in ms, times in s.",
          ["Config", "Rep", "OK / total", "p50", "p95", "max", "Endpoint drop", "Endpoints back", "Recovery", "Outage"],
          rows, [0.7, 0.95, 0.8, 0.55, 0.6, 0.6, 0.75, 0.8, 0.65, 0.6], size=7.5)
    figure(fig_exp1(), "Exp 1 rep 1 (graceful delete): requests per second by outcome, from the raw records. The baseline "
           "fails for about 25 s although its endpoint returned at 0.9 s; the improved configuration shows no failed request.",
           6.2)
    P(f"**Hypothesis held for improved; baseline worse than the plan expected.** The improved configuration failed no "
      "request in 3 runs; one request failed over in the gateway log (`502, 200`). The baseline's graceful delete caused "
      f"a {OUT1_MIN}–{OUT1_MAX} s outage although endpoints returned in 0.9–1.1 s; the forced delete caused 2.0 s. **Why a "
      "graceful delete takes longer than a forced one is not established.** The plan expected an outage until the "
      "replacement was ready; the replacement was ready far sooner than the outage ended, and readiness on `/health` does "
      "not show that the pod can serve database requests. The endterm's explanation, pool starvation during start-up, was "
      "not tested here.", align="justify")
    P("**Correction.** The endterm's 23 s was a lower bound, because that probe stopped while requests were still timing "
      f"out. The run now records `clients_settled_before_stop`, true in all six runs, and measures {OUT1_MIN}–{OUT1_MAX} s.",
      align="justify")

    H2("10.4 Exp 2: database unavailable")
    rows = []
    for n in ("rep1-stop", "rep2-stop", "rep3-freeze", "rep4-freeze"):
        d = J("exp2-" + n)
        for c in ("baseline", "improved"):
            v = d["configs"][c]
            s = v["stats_during_outage"]
            ig = v["integrity"]
            al = ", ".join(f"{a[3:] if a.startswith('Api') else a} {x['firing_s']}" for a, x in d["alerts"].items()) if c == "improved" else "no rule"
            rows.append([n, c, f"{s['success']}/{s['requests']}", s["lat_p50_ms"], s["lat_p95_ms"], s["lat_max_ms"],
                         ", ".join(f"{k}:{x}" for k, x in s["codes"].items()), v["recovery"]["recovery_after_restore_cmd_s"],
                         f"{ig['acknowledged']}/{ig['rows_in_db']}", al])
    table("Exp 2 per run, during the outage. Latency in ms; recovery in s after the restore command; writes = acknowledged / "
          "rows; alert firing in s.",
          ["Run", "Config", "OK / total", "p50", "p95", "max", "Codes", "Recovery", "Writes", "Alerts firing"],
          rows, [0.72, 0.58, 0.55, 0.55, 0.55, 0.55, 1.25, 0.45, 0.45, 1.3], size=6.5)
    figure(fig_scatter("exp2-rep1-stop", "stats", 13.5), "Exp 2 rep 1 (stop): response time of every GET, from the raw records. "
           "The improved configuration's failures end within 5–10 s or at once; the baseline's wait up to the 12 s client timeout.",
           6.2)
    P("**The success ratio favours the baseline: 37 % vs 8 % during the outage, 75.28 % vs 50.56 % over the whole window. "
      "This must not be read as an availability gain for improved, and it is not a real gain for the baseline.** No "
      "configuration can serve these reads with its only database down. The baseline's requests wait up to the 12 s client "
      "timeout, so some finish after the restore and count as successes. The gateway gives up within 10 s, and after one "
      "timeout `max_fails=1` marks both replicas down, so later requests get a fast 503 (`502` from `no live upstreams`). "
      "**The improved gain is bounded response time: maximum 10.0 s and a machine-readable reply with `Retry-After`, against "
      "12.0 s at the client's own limit.** The price is fewer late successes and slower recovery after the restore "
      "(4.0–12.3 s vs 3.2–8.0 s). 188 requests were retried, only 12 of those succeeded on the other replica, and 569 got "
      "JSON 503. Both configurations recovered without intervention within the 30 s criterion, and all 146 and 143 "
      "acknowledged writes have exactly one row. Both alerts fired at 15.2–21.2 s, and correctly: requests really were "
      "slow and failing.", align="justify")

    H2("10.5 Exp 3: network delay or timeout (new)")
    rows = []
    for c in ("baseline", "improved"):
        for r in (1, 2, 3):
            d = J(f"exp3-{c}-rep{r}")
            s = d["stats_during"]
            g = d["gateway"]
            rows.append([c, r, d["netem_packets_delayed"], f"{s['success']}/{s['requests']}", s["lat_p50_ms"], s["lat_p95_ms"],
                         s["lat_max_ms"], g["failover_success"] if g else "–", d["client_retries_writer"],
                         d["recovery"]["last_slow_request_end_after_removal_s"],
                         ", ".join(f"{a[3:]} {x['firing_s']}" for a, x in d["alerts"].items()) or "no rule"])
    table("Exp 3 per run, during the fault. Latency in ms. Gateway retries all succeeded (`504, 200`). Client write retries "
          "are the writer's. Last slow = end of the last request over 200 ms after removal, s.",
          ["Config", "Rep", "Delayed packets", "OK / total", "p50", "p95", "max", "Gateway retries", "Client write retries",
           "Last slow", "Alerts firing (s)"],
          rows, [0.58, 0.28, 0.55, 0.52, 0.55, 0.55, 0.55, 0.55, 0.55, 0.45, 1.45], size=6.5)
    figure(fig_scatter("exp3", "exp3", 36), "Exp 3 rep 1: response time of every GET. Improved: requests on the slow replica are "
           "cut at 5 s and retried, the rest stay near 10 ms. Baseline: nothing bounds the wait until connections are closed.",
           6.2)
    P("**Baseline hypothesis failed.** The plan predicted 8–10 s per request. Measured p50 was 10.0–18.0 s, maximum 34 s, and "
      "30 % failed (74 of 247), as connections closed by the server without a response. A likely reason is the API's 15 s "
      "`WriteTimeout`, but **this is a hypothesis, not tested**. A confounder is the client's 1001 write retries, mostly "
      "429 from the 60 creates/min route limit, which add load on the baseline (18 on improved). **Improved passed:** "
      "247/247, maximum 5.08 s against the 12 s bound, at most 2 tries, 31 retries all `504, 200`. Readiness did not remove "
      "the slow replica, as predicted, because `/health` does not touch the database. The 9.84 s recovery in one run comes "
      "from one 235 ms request after the removal, just over the 200 ms threshold; the others give 3.78 s and 1.42 s.",
      align="justify")
    P("**False-positive trade-off.** ApiHighErrorRate fired in all three improved runs, though **clients saw no failure**: "
      "the API records a request the gateway timed out as a 500 (Listing 2 shows one, with the client's 200 beside it). ApiHighLatencyP95 "
      "fired too, correctly. In two runs the error alert was pending, cleared and returned before firing.", align="justify")

    H2("10.6 Exp 4: interrupted multi-step transaction (new)")
    rows = []
    for c in ("baseline", "improved"):
        for r in (1, 2, 3):
            d = J(f"exp4-{c}-rep{r}")
            s = d["probe_after_kill"]
            it = d["integrity_target"]
            rows.append([c, r, d["insert_done_at_kill"], d["committed_rows_visible_at_kill"], d["container_restarts"],
                         d["client_attempts"], d["recovery"]["time_to_successful_retry_s"], f"{s['success']}/{s['requests']}",
                         it["rows_with_interrupted_client_id"], f"{it['rows_on_account']}/{it['expected_rows']}",
                         f"{it['balance']:.0f} / {it['expected_balance']}", d["duplicates_db"],
                         ", ".join(f"{a[3:]} {x['firing_s']}" for a, x in d["alerts"].items()) or ("none" if c == "improved" else "no rule")])
    table("Exp 4 per run. `Insert done` and `Committed` are read from `pg_locks` and the table at the moment of the kill.",
          ["Config", "Rep", "Insert done", "Committed", "Container restarts", "Client tries", "Retry OK (s)", "Probe OK / total",
           "Rows for id", "Rows on account", "Balance / expected", "DB dup", "Alerts firing (s)"],
          rows, [0.55, 0.26, 0.42, 0.55, 0.55, 0.38, 0.38, 0.55, 0.34, 0.5, 0.6, 0.28, 0.9], size=6.5)
    P("**Hypothesis held on integrity, and it does not separate the configurations.** Idempotency and the transaction live "
      "in the application, so both have them. Each process was killed with the insert done and nothing committed; Postgres "
      "logged `unexpected EOF on client connection with an open transaction`; each retry was answered 201, so the first "
      "attempt really was rolled back. **The difference is retry time and probes:** improved retries succeed in 1.2–1.7 s "
      "through the other replica, and its probe stays at 376/376. The gateway does not replay the POST; the client's retry "
      "does the recovery. **Baseline retry time grew 3.6 → 22.2 → 34.1 s with restart count.** Kills were about 44 s apart, "
      "which likely puts the container into kubelet's restart back-off [7], so **3.6 s is the fair figure for a first "
      "crash**. The back-off also shows on improved (endpoints back at 1.5, 19.0 and 31.3 s), where the other replica hides it; "
      "ApiReplicaDown fired in two of those runs, correctly.", align="justify")

    H2("10.7 Exp 5: high load")
    rows = []
    for c in ("baseline", "improved"):
        for r in (1, 2):
            d = J(f"exp5-{c}-rep{r}")
            s = d["phases"]["step 30 req/s"]
            res = s["resources"]
            api = [v["cpu_millicores"] for k, v in res.items() if (k.startswith("api-baseline") if c == "baseline" else (k.startswith("api-") and "baseline" not in k))]
            cd = s["codes"]
            rows.append([c, r, cd.get("200", 0), cd.get("429", 0), cd.get("conn_error", 0), s["achieved_success_rps"], s["lat_p50_ms"],
                         s["lat_p95_ms"], s["lat_max_ms"], ", ".join(f"{x:.1f}" for x in api), "none"])
    table("Exp 5, 60 s step at 30 req/s (1800 requests per run). Latency in ms. CPU in millicores per API pod, one sample per run.",
          ["Config", "Rep", "200", "429", "Conn. errors", "OK req/s", "p50", "p95", "max", "API CPU", "Alerts"],
          rows, [0.7, 0.3, 0.5, 0.45, 0.5, 0.55, 0.45, 0.6, 0.6, 0.9, 0.5], size=7.5)
    P("**The plan's hypothesis that the baseline also holds this rate failed, and the failure is the limiter, not "
      f"capacity.** API CPU stayed near {BASE_CPU} millicores. 1080 of 3600 requests got 429 once the 1200/min per-process limit "
      f"ran out, {FIRST429_MIN:.0f}–{FIRST429_MAX:.0f} s into the step, which is why the baseline row fails (98.37 % excluding "
      "429, p95 769.7–1094.8 ms). **The improved configuration avoids this only because each replica counts separately "
      "(900/min each): its limit is effectively doubled, not shared. The improvement is therefore partly an artefact of "
      "the limiter**, and this experiment does not show that two replicas add capacity that was needed. **The cause of the 41 "
      "connection errors is not established**; they appear only while the limiter is active. At low load the gateway costs "
      "little: warm-phase p50 is 9.9 ms against 9.1–9.3 ms.", align="justify")

    H2("10.8 Exp 6: monitoring and alert trigger")
    rows = []
    for n, exp in (("quiet-window-rep1", "none"), ("quiet-window-rep2", "none"), ("replica-down-rep1", "ApiReplicaDown"),
                   ("replica-down-rep2", "ApiReplicaDown"), ("replica-down-rep3", "ApiReplicaDown"),
                   ("error-rate-rep1", "ApiHighErrorRate"), ("error-rate-rep2", "ApiHighErrorRate"),
                   ("latency-rep1", "ApiHighLatencyP95"), ("latency-rep2", "ApiHighLatencyP95")):
        d = J("exp6-" + n)
        s = d.get("probe_after_injection") or d.get("probe_fault_window") or d.get("probe")
        al = d.get("alerts", {})
        rows.append([n, exp, f"{s['success']}/{s['requests']}", s["lat_p95_ms"], s["lat_max_ms"],
                     "; ".join(f"{a[3:]}: firing {x['firing_s']}, clear {x['cleared_s'] if x['cleared_s'] is not None else 'not recorded'}" for a, x in al.items())
                     or "none seen"])
    table("Exp 6 per run. Latency in ms; alert times in s after the injection.",
          ["Run", "Expected alert", "OK / total", "p95", "max", "Alerts"],
          rows, [1.2, 1.15, 0.65, 0.55, 0.55, 2.9], size=7)
    figure(crop_shot("grafana-exp6-latency-rep1.png", (0, 0, 1600, 925), "g-lat.png"),
           "Grafana during the Exp 6 lock bursts: latency rises with no failed request, ApiHighLatencyP95 fires.", 5.4)
    P("**Hypothesis held.** Each fault fired its alert within 45 s (11.2–16.2 s) and cleared after recovery; no alert fired in "
      "either quiet window. A database outage also fires ApiHighLatencyP95, which is correct: requests really were slow. "
      "In error-rate rep 2 that secondary alert's clear time was not recorded; the primary's was.", align="justify")
    P("**Hung replica: not all requests failed over.** 34 of 1076 requests got a 503 after 5 s, with a single try (`504`) and "
      "no failover, all within about 5 s of the fault. Exp 3's slow replica did fail over. **A hypothesis, not tested,** is "
      "that readiness removed the hung replica from DNS while those requests were in flight, leaving nginx no other peer "
      "for them. The replica left endpoints at 2.4–2.7 s and returned at 43.2–45.8 s after the liveness restart.",
      align="justify")

    H2("10.9 Exp 7: unauthorized or invalid operation")
    d1 = J("exp7-access-control-rep1")
    rows = [[c["case"], c["status"], c["expected"], "yes" if c["pass"] else "**no**"] for c in d1["cases"]]
    fills = {(i, 3): "F8D7DA" for i, c in enumerate(d1["cases"]) if not c["pass"]}
    table("Exp 7 access control, rep 1. Rep 2 gave the same statuses.", ["Case", "Status", "Expected", "As expected"],
          rows, [3.6, 0.7, 1.0, 0.9], size=7.5, fills=fills)
    P("**Recorded result: FAIL, 20 of 22 cases as expected.** The 2 failed cases are one case in both repetitions: `B GET "
      "/api/households/{A}` expected 404 and got **405**, because that route has no GET. Nothing was disclosed: the body is "
      "empty. The case tests routing, not tenant isolation. **The expected value was wrong, but it stays as written before "
      "the run, and the result is not re-scored.** The other cases show tenant isolation enforced: 401 for missing, garbage "
      "or forged tokens, 404 for all of user B's attempts on A's record space, and a 400 refusal for A's account id used "
      "from B's own household. The md5 snapshot of the victim's data is unchanged, and 22/22 requests are in both logs.",
      align="justify")
    d2 = J("exp7-input-limits-rep1")
    rows = [[c["case"], c["status"], c.get("retry_after") or "–"] for c in d2["cases"] if "wrong password" not in c["case"]]
    table("Exp 7 input and limit cases, rep 1 (rep 2 the same). 38/38 cases as expected over both reps.",
          ["Case", "Status", "Retry-After"], rows, [4.4, 0.8, 1.0], size=7.5)
    P(f"Twelve wrong passwords gave `{d2['login_status_sequence']}`: **first 429 at attempt 11, not 6**, because each replica "
      "counts its own 5 failures (a weakness the threat model predicted). The correct password afterwards still got 429 with "
      f"`Retry-After: 900`. Background probes stayed OK ({d2['probe']['success']}/{d2['probe']['requests']} and "
      f"{J('exp7-input-limits-rep2')['probe']['success']}/{J('exp7-input-limits-rep2')['probe']['requests']}). The 3 MB bodies "
      "stop at the gateway with JSON 413, which is why those 2 requests are missing from the API log (36/38 in the API log, "
      "38/38 at the gateway). **Finding:** on the baseline the API logged `remote=203.0.113.77`, an address chosen by the "
      "client, and therefore a fresh limiter and lockout key per request; through the gateway it logged `10.244.0.1`. Failed "
      "logins have no dedicated audit event, only the request line.", align="justify")

    H2("10.10 Exp 8: recovery and rollback (new)")
    rows = []
    for c in ("baseline", "improved"):
        for r in (1, 2):
            d = J(f"exp8-{c}-rep{r}")
            s = d["stats_fault_window"]
            rows.append([c, r, f"{s['success']}/{s['requests']}", s["lat_p50_ms"], s["lat_p95_ms"], s["lat_max_ms"],
                         d["detection"]["new_pod_readiness_failure_seen_s"], d["recovery"]["bad_pod_gone_after_undo_s"],
                         f"{d['integrity']['acknowledged']}/{d['integrity']['rows_in_db']}", d["duplicates_db"],
                         "yes" if d["acceptance"]["config_restored"] else "no", "none"])
    table("Exp 8 per run. Latency in ms; readiness failure in s after the release; bad pod gone in s after the undo.",
          ["Config", "Rep", "OK / total", "p50", "p95", "max", "Readiness failure", "Bad pod gone", "Writes", "DB dup",
           "Restored", "Alerts"],
          rows, [0.65, 0.3, 0.65, 0.45, 0.5, 0.55, 0.6, 0.55, 0.55, 0.4, 0.55, 0.5], size=7)
    P("**Both pass, and this experiment shows no gain from the improved configuration**, as the plan predicted: a default "
      "RollingUpdate with 1 replica rounds `maxUnavailable` down to 0 and `maxSurge` up to 1 [6], so it keeps the old pod. The "
      "improved bad pod takes longer to go (6.11–6.49 s vs 0.76–1.94 s) because of the preStop `sleep 5`. **No alert noticed "
      "the stuck rollout in any run: a monitoring gap.** Separately, the first run of the same broken release at the 256 Mi "
      "Postgres limit was a real outage (3.61 % availability), kept as evidence that the database is a single point of "
      "failure (Section 9.5).", align="justify")

    H2("10.11 Data consistency, retries and objectives")
    rows = integrity_rows()
    table("Data consistency across all write runs: no acknowledged write lost or duplicated in any valid run of either config.",
          ["Exp", "Config", "Runs", "Acknowledged", "Rows", "Duplicates in database", "Balance = −Σ"],
          rows, [0.4, 0.8, 0.5, 1.0, 0.7, 1.5, 1.0], size=8)
    L("slo"); table("Measured outcome per service-level objective (improved configuration).",
          ["SLO", "Target", "Measured", "Outcome"],
          [["SLO-1 steady availability", "≥ 99.5 %", "1200/1200 in quiet windows (Exp 6)", "Met"],
           ["SLO-2 during instance loss", "≥ 99 %", "418/418 (Exp 1)", "Met"],
           ["SLO-3 latency at 30 req/s", "p95 ≤ 500 ms", "25.7–28.5 ms (Exp 5)", "Met, with the limiter caveat"],
           ["SLO-4 bounded failure latency", "GET ≤ 10.5 s; POST ≤ 5.5 s", "GET max 10.016 s (Exp 2), 5.08 s (Exp 3). POST was "
            "not separated in the results", "GET met; POST not measured separately"],
           ["SLO-5 recovery", "≤ 10 s instance; ≤ 30 s database", "1.3–1.7 s (Exp 1); 4.0–12.3 s (Exp 2); 6.11–6.49 s (Exp 8)", "Met"],
           ["SLO-6 write integrity", "0 lost, 0 duplicates, balance check", "All runs (table above)", "Met; application mechanism, "
            "present in both configs"],
           ["SLO-7 detection", "≤ 30 s, no false positive in quiet", "11.2–16.2 s (Exp 6); 0 alerts in quiet",
            "Met; but a false positive under fault in Exp 3, and a rollout gap in Exp 8"],
           ["SLO-8 access control", "100 % denied, 0 rows changed, logged", "Isolation enforced, snapshot unchanged, 22/22 logged. "
            "Exp 7 recorded FAIL on one mis-specified case", "Recorded FAIL; see 10.9"],
           ["SLO-9 backup recoverability", "0 records lost since dump", "No restore run in this project", "**Not tested**"]],
          [1.6, 1.4, 2.5, 1.5], size=7.5)
    L("hyp"); table("Hypotheses against results.",
          ["Exp", "Hypothesis", "Result"],
          [["1", "Improved: at most a few failures. Baseline: outage until the replacement is ready",
            "Improved held. Baseline outage outlasted readiness by far; cause not established"],
           ["2", "Both fail and recover with no lost write; improved bounded, baseline waits; alert fires", "Held. Success "
            "ratio favours the baseline; explained in 10.4"],
           ["3", "Improved bounded and retried; baseline 8–10 s per request", "Improved held. **Baseline failed**: p50 10–18 s, "
            "30 % failed"],
           ["4", "One row after retry in both; improved retry 1–2 s; baseline after restart", "Held; integrity does not separate "
            "the configs"],
           ["5", "Improved p95 < 500 ms; baseline also holds the rate", "Improved held. **Baseline part failed**, by the limiter"],
           ["6", "Each fault fires its alert ≤ 45 s; none in quiet", "Held; 34 of 1076 got 503 with no failover"],
           ["7", "Expected statuses; data unchanged; logged; spoof not honoured through the gateway",
            "Held except the expected value of one case: **recorded FAIL**"],
           ["8", "No failed request; baseline probably the same; monitoring will not notice", "Held in all three parts: "
            "no gain from improved"]],
          [0.35, 3.3, 3.35], size=7.5)
    H2("10.12 Measured results against theory")
    P("Section 5.4 is illustrative; everything else is measured. The formula assumes independent failures and instant "
      "failover, and the measurements show otherwise. Both API replicas fail together when the shared database is lost "
      "(Exp 2), so redundancy gives nothing there. A replacement pod can be Ready before it can serve (Exp 1 baseline). "
      "Passive ejection likely delays recovery after a database restore (4.0–12.3 s vs 3.2–8.0 s). A request in flight on "
      "a hung replica may get no failover (Exp 6). The measured `A` values, such as 55.95 % or 44.95 %, cannot be compared "
      "with the illustrative 0.999: they describe short windows built around a deliberate fault, and the illustrative "
      "figure describes a long-run process that was not observed.", align="justify")
    H2("10.13 Trade-offs")
    table("Trade-offs of the improved configuration, measured unless marked.",
          ["Trade-off", "Evidence"],
          [["Fewer late successes during a dependency outage", "Exp 2: 8 % vs 37 % during the outage; response bounded at 10.0 s"],
           ["Slower recovery after a database restore", "Exp 2: 4.0–12.3 s vs 3.2–8.0 s"],
           ["False-positive alert", "Exp 3: ApiHighErrorRate fired in 3 of 3 runs while clients saw no failure"],
           ["Slower shutdown", "Exp 8: bad pod gone in 6.11–6.49 s vs 0.76–1.94 s (preStop)"],
           ["Added latency", f"Exp 5 warm phase p50: 9.9 ms vs 9.1–9.3 ms. Retried requests cost about 5 s (Exp 3)"],
           ["Resources", f"Configured limits: 2 API + 2 gateways, 1600 m CPU and 768 Mi, against 500 m and 256 Mi (from the manifests). "
            f"Sampled in the Exp 5 step: API {IMP_API_MIN:.1f}–{IMP_API_MAX:.1f} m per pod, gateway {IMP_GW_MIN:.1f}–{IMP_GW_MAX:.1f} m per pod, baseline API {BASE_CPU} m"],
           ["Complexity", "Gateway config with timeouts, retries, per-pod DNS; rules can be wrong, as the endterm's ApiReplicaDown was"],
           ["Limiter behaviour changes with replica count", "Exp 5, Exp 7: effective limits double, lockout needs more attempts"]],
          [2.6, 4.4], size=8)


def s11_disc():
    H1("11. Discussion, Limitations and Future Work")
    H2("11.1 What the evidence supports")
    P("Redundancy with a retrying gateway turns the loss of one instance from an outage into no visible failure (Exp 1), and "
      "gateway timeouts bound the damage of one slow replica (Exp 3). Alerts detect a hung replica, a lost database and "
      "contention in 11–16 s, and stay silent in quiet windows (Exp 6). Correct records survived every injected fault in "
      "both configurations, thanks to the application's transaction and idempotency key, which are not improvements of "
      "this project's configuration. The clearest mechanism-level value is in Exp 1, Exp 3 and the retry time of Exp 4.",
      align="justify")
    H2("11.2 What the evidence does not support")
    bullets([
        "**Availability during a database outage.** Improved is lower on success ratio; the gain is bounded response time.",
        "**Capacity from two replicas.** Exp 5 is confounded by a per-process limiter.",
        "**Rollback safety from the improved configuration.** Exp 8 shows no gain over the Kubernetes default.",
        "**Failure rates.** MTTF, MTBF and λ reflect injection timing.",
        "**Causes marked as hypotheses or not established:** the graceful-delete outage in Exp 1, the `WriteTimeout` in Exp 3, the "
        "41 connection errors in Exp 5, the DNS explanation in Exp 6, kubelet back-off in Exp 4, and memory pressure as the "
        "cause of the Postgres OOM run.",
    ])
    H2("11.3 Limitations")
    bullets([
        "**One node, one laptop.** All replicas share one host, and the load generator runs on it. Pod-level faults cannot show "
        "node failure, and **replicas on one node do not protect against node failure.**",
        "**PostgreSQL remains a single point of failure.** There is no replica; only restart in place and an earlier manual backup "
        "drill. SLO-9 was not tested.",
        "**Small repetition counts** (2–4 per scenario): ranges describe behaviour, not statistics, and no confidence intervals are "
        "claimed. Exp 3 runs differ widely even within a config, and Exp 4 baseline runs affect each other through restart back-off.",
        "**Shared environment.** Both configurations share one database, and baseline and improved pods stay deployed during each "
        "other's runs. Exp 2 probes both at once, which is fair for the fault but puts both loads on one recovering database.",
        "**Test defects kept as they are:** one wrong expected value (Exp 7) and a bad `kill` in the first Exp 4 runs.",
        "**Tests cited, not run:** integration and API tests. M1 and M4 have no Go test.",
        "**Reproduction needs the images.** The API image is built from hoWallet at `06aea177` plus the metrics patch; the "
        "application source and the patch are private, available on request (Appendix 14.4).",
    ])
    H2("11.4 Future work")
    bullets([
        "A database replica with failover, or at least scheduled backups with a timed restore drill, to address the SPOF and SLO-9.",
        "A separate startup gate or database-aware readiness, and an experiment that finds the cause of the graceful-delete outage.",
        "Shared rate-limit and lockout counters, then a repeat of Exp 5 with the limiter removed from the comparison.",
        "A `statement_timeout` and a pool-acquire timeout, so a blocked database fails inside the API.",
        "An alert on stuck rollouts, and an error-rate alert computed at the gateway, to remove the Exp 3 false positive.",
        "More repetitions on several nodes, with a load generator on another machine.",
    ])


def s12_conc():
    H1("12. Conclusion")
    P("The dependability objectives were met for instance failure, bounded response under delay, write integrity, detection "
      "and recovery time, with the caveats given per objective. SLO-9 was not tested, and Exp 7 stands as a recorded FAIL for a "
      "wrong expected value. The improved configuration clearly helps when one instance fails or slows down. It does not "
      "make a database outage more available; it changes how that outage looks to the client. It gains nothing measurable in "
      "the rollback experiment, its load result is partly a limiter artefact, and it adds a false-positive alert. The main "
      "residual risk is the single PostgreSQL instance, and the lab's single node limits every availability claim to pod-level "
      "faults.", align="justify")


def s13_refs():
    H1("13. References")
    for i, r in enumerate(parse_refs(), 1):
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Inches(0.3)
        p.paragraph_format.first_line_indent = Inches(-0.3)
        p.paragraph_format.space_after = Pt(2)
        runs(p, f"[{i}] {r}", size=9)


def s14_app():
    H1("14. Appendix: Code Repository, Configuration, Test Scripts, Logs and Supplementary Tables")
    H2("14.1 Repository layout")
    code("""ftr-final/
  README.md             reproduction steps, ports, gateway behaviour
  run_all.sh            one command: all eight experiments, then the summary
  kind-config.yaml, Dockerfile.migrate
  k8s/                  00-namespace, 10-postgres, 20-migrate-job, 30-api, 40-gateway, 50-prometheus, 60-grafana,
                        70-network-policy; baseline/api-baseline.yaml; optional/block-egress.yaml
  scripts/              run_exp.py, faults.py, final.py, exps.py, ftlab.py, build_summary.py, log_samples.py, demo.py,
                        setup_users.py, up.sh, down.sh, architecture.html, grafana_shot.py
  analysis/             reliability.py, reliability-computed.json, ft-unit-tests.txt
  notes/                requirements, reliability, architecture, software-ft, threat-model, security-controls,
                        graceful-degradation, test-inventory, references
  evidence/             test-plan.md, anomalies.txt, log-samples.txt, exp1..8.txt, results/, screenshots/, endterm-results/
  report/               this report, slides, demo-runbook.md, build_report.py, build_deck.js, fig/""", size=7.5)
    P("The repository is a local git repository. `evidence/token.txt` and `evidence/lab-users.json` are git-ignored. Secrets in "
      "`k8s/00-namespace.yaml` and the password in `scripts/ftlab.py` are **synthetic lab placeholders**, and the JWT in "
      "`scripts/exps.py` is the deliberately forged test token of Exp 7 (subject all zeros, signed with a wrong secret). No real "
      "credential is included.", align="justify")
    H2("14.2 One-command reproduction")
    code("""bash scripts/up.sh --baseline     # create the cluster, load images, apply manifests, wait for Ready
python scripts/setup_users.py     # synthetic users; tokens go to the git-ignored evidence/lab-users.json (valid 24 h)
bash run_all.sh                   # all eight experiments, about 1 hour, then evidence/results/summary.md
python analysis/reliability.py --json-out analysis/reliability-computed.json   # reliability table
python report/build_report.py     # this report (needs Word for the PDF; --no-pdf for DOCX only)
bash scripts/down.sh              # delete the cluster""", size=8)
    P("A single experiment: `python scripts/run_exp.py <1..8> [rep ...]`. Rerunning a repetition overwrites its JSON.")
    H2("14.3 Raw data locations")
    table("Where each kind of evidence lives.", ["What", "Where"],
          [["Hypotheses, criteria, amendments", "`evidence/test-plan.md`"],
           ["Readable record per experiment", "`evidence/exp1.txt` … `exp8.txt`"],
           ["Raw per-request records and per-run results", "`evidence/results/exp*.json`"],
           ["Consolidated table and reading notes", "`evidence/results/summary.md`"],
           ["Inputs and computed reliability values", "`evidence/results/reliability-inputs.json`, `analysis/reliability-computed.json`"],
           ["Excluded and set-aside runs", "`*-INVALID-*.json`, `results/pre-pg-memory-fix/`, `results/superseded/`, `evidence/anomalies.txt`"],
           ["Log samples with correlation ids", "`evidence/log-samples.txt`"],
           ["Dashboards and firing alerts", "`evidence/screenshots/`"],
           ["Unit test output for the mechanisms", "`analysis/ft-unit-tests.txt`"],
           ["Endterm data, for reference only", "`evidence/endterm-results/`"]],
          [3.0, 4.0], size=8)
    H2("14.4 Building the images")
    P("`scripts/up.sh` needs local images `howallet-api:endterm` and `ftr-migrate:endterm`. The API image is built from the "
      "hoWallet repository at commit `06aea177` with the opt-in `/metrics` patch applied (commit `60cd5791` on `feat/metrics`), "
      "using its `deployments/Dockerfile.api`. The application source and the patch are private and not in this repository; "
      "they are available to the instructor on request. Without them, the "
      "instructor can inspect the configuration, scripts and raw data, and the demonstration runs on the author's machine.",
      align="justify")
    H2("14.5 Supplementary: reliability per run")
    rows = []
    for k, r in REL.items():
        for p in r["per_run"]:
            c = p["censored"]
            rows.append([k[0], k[1], p["rep"], f"{p['T_obs_s']:.1f}", p["N_f"], f"{p['downtime_s']:.1f}",
                         "–" if c else f"{p['MTTR_s']:.1f}", f"{p['A_time']*100:.2f} %", f"{p['requests_ok']} / {p['requests_failed']}",
                         f"{p['R_req']*100:.2f} %"])
    table("Reliability quantities per repetition (`python analysis/reliability.py --per-run`).",
          ["Exp", "Config", "Run", "T_obs (s)", "N_f", "D (s)", "MTTR (s)", "A", "ok / failed", "R_req"],
          rows, [0.3, 0.6, 2.3, 0.55, 0.3, 0.45, 0.55, 0.6, 0.7, 0.6], size=6.5)
    H2("14.6 AI-generated architecture diagram: prompt")
    P("The architecture diagram (Section 6.1) was generated by an AI coding assistant (Claude Code) inside a longer working "
      "session, so the exact wording of the original request was not preserved. The prompt below reproduces it: given to "
      "the assistant together with the `k8s/` manifests, it yields `scripts/architecture.html`, which is then rendered "
      "to `report/fig/architecture.png`. All facts on the diagram were checked by the author against the manifests.",
      align="justify")
    code(ARCH_PROMPT)


ARCH_PROMPT = """\
Read the Kubernetes manifests in k8s/ (namespace, postgres, migrate job, api, gateway, prometheus,
grafana, network policies) and k8s/baseline/api-baseline.yaml. Draw the architecture of this lab as a
single self-contained HTML page with an inline SVG (1400 x 760, white background, Segoe UI), so it can
be rendered to PNG for a report. Use only facts from the manifests.

Show:
- a dashed frame for the single kind node and the namespace "university", noting the default-deny
  ingress NetworkPolicy with an allowlist;
- the Client on the left (probe/load scripts, browser);
- the improved path on top: Gateway (NodePort 30080) with two nginx pods, then the api Deployment with
  two pods (:8080, /health, /metrics), then PostgreSQL 16 (StatefulSet postgres-0, PVC, :5432,
  migrated by a Job);
- under each box, its resilience settings in small text: gateway timeouts and retries (GET only),
  JSON 503/413, X-Request-Id, X-Real-IP overwritten, body cap, per-pod DNS upstream; api replicas,
  maxUnavailable 0, preStop, readiness and liveness probes, non-root and read-only rootfs, JWT, rate
  limits, client_id idempotency;
- monitoring on the right: Prometheus (NodePort 30090, scrape interval, dns_sd per pod, 3 alert
  rules) and Grafana (NodePort 30300, dashboard), with arrows "scrape /metrics" and "PromQL";
- the baseline path at the bottom: api-baseline, 1 replica, no preStop, no gateway, NodePort 30081
  direct, no retries, no JSON 503, trusts X-Real-IP, scraped as job "api-baseline";
- that both paths share the same PostgreSQL, which is a single point of failure.

Label the arrows (HTTP, round robin + retry, pgx pool, "same database"). Use one colour per tier
(client grey, gateway blue, api green, database amber, monitoring violet, baseline red), a title on top and a
one-line subtitle explaining the two paths. Keep the text readable when the image is 17 cm wide."""


# ----------------------------------------------------------------------------- build

def build_body():
    title_block()
    s1_abstract()
    s2_intro()
    s3_req()
    s4_fault()
    s5_rel()
    s6_arch()
    s7_impl()
    s8_obs()
    s9_method()
    s10_results()
    s11_disc()
    s12_conc()
    s13_refs()
    s14_app()


def build():
    global doc
    verify_facts()
    for _pass in (1, 2):   # pass 1 collects table/figure numbers for forward references
        counters.update(fig=0, tab=0)
        doc = open_template()
        set_doc(doc)
        build_body()
    doc.save(str(OUT_DOCX))
    print("saved", OUT_DOCX, "figures", counters["fig"], "tables", counters["tab"])


def to_pdf():
    ps = (f"$w = New-Object -ComObject Word.Application; $w.Visible = $false; "
          f"$d = $w.Documents.Open('{OUT_DOCX}'); $d.Fields.Update() | Out-Null; "
          f"$d.SaveAs([ref]'{OUT_PDF}', [ref]17); $d.Close([ref]0); $w.Quit()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print("saved", OUT_PDF)


if __name__ == "__main__":
    build()
    if "--no-pdf" not in sys.argv:
        to_pdf()
