// Builds report/Final_FTR_Yakupov_255193.pptx. Needs pptxgenjs (NODE_PATH) and, for theme colours, apply_theme.js from
// the deck tooling (APPLY_THEME=<path>); without it the deck keeps the stock Office theme colours.
// Numbers are read from evidence/results/*.json and analysis/reliability-computed.json, not typed.
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const ROOT = path.join(__dirname, "..");
const J = (n) => JSON.parse(fs.readFileSync(path.join(ROOT, "evidence/results", n + ".json"), "utf8"));
const REL = Object.fromEntries(JSON.parse(fs.readFileSync(path.join(ROOT, "analysis/reliability-computed.json"), "utf8"))
  .results.map((r) => [r.experiment + "-" + r.config, r]));
const sum = (xs) => xs.reduce((a, b) => a + b, 0);
const pct = (ok, n) => +(100 * ok / n).toFixed(2);

// ---- data
const e1 = (c) => [[1, "graceful"], [2, "graceful"], [3, "crash"]].map(([r, m]) => J(`exp1-${c}-rep${r}-${m}`));
const e1b = e1("baseline"), e1i = e1("improved");
const out1 = e1b.slice(0, 2).map((d) => d.recovery.user_visible_outage_s);
const e2 = ["rep1-stop", "rep2-stop", "rep3-freeze", "rep4-freeze"].map((n) => J("exp2-" + n));
const out2 = (c) => e2.map((d) => d.configs[c].stats_during_outage);
const e3 = (c) => [1, 2, 3].map((r) => J(`exp3-${c}-rep${r}`));
const e4 = (c) => [1, 2, 3].map((r) => J(`exp4-${c}-rep${r}`));
const e5 = (c) => [1, 2].map((r) => J(`exp5-${c}-rep${r}`).phases["step 30 req/s"]);
const e6r = [1, 2, 3].map((r) => J(`exp6-replica-down-rep${r}`).probe_after_injection);
const e8 = (c) => [1, 2].map((r) => J(`exp8-${c}-rep${r}`).stats_fault_window);
const ratio = (ss) => pct(sum(ss.map((s) => s.success)), sum(ss.map((s) => s.requests)));
const e5ratio = (c) => { const s = e5(c); return pct(sum(s.map((x) => x.success)), sum(s.map((x) => x.success + x.failed))); };
const success = [
  ["Exp 1 instance", ratio(e1b.map((d) => d.stats_after_injection)), ratio(e1i.map((d) => d.stats_after_injection))],
  ["Exp 2 database", ratio(out2("baseline")), ratio(out2("improved"))],
  ["Exp 3 delay", ratio(e3("baseline").map((d) => d.stats_during)), ratio(e3("improved").map((d) => d.stats_during))],
  ["Exp 4 kill", ratio(e4("baseline").map((d) => d.probe_after_kill)), ratio(e4("improved").map((d) => d.probe_after_kill))],
  ["Exp 5 load*", e5ratio("baseline"), e5ratio("improved")],
  ["Exp 8 rollback", ratio(e8("baseline")), ratio(e8("improved"))],
];
const maxOf = (ss) => Math.max(...ss.map((s) => s.lat_max_ms)) / 1000;
const m2b = maxOf(out2("baseline")), m2i = maxOf(out2("improved"));
const m3b = maxOf(e3("baseline").map((d) => d.stats_during)), m3i = maxOf(e3("improved").map((d) => d.stats_during));
const retry4i = e4("improved").map((d) => d.recovery.time_to_successful_retry_s);
const retry4b = e4("baseline").map((d) => d.recovery.time_to_successful_retry_s);
const fire6 = [...[1, 2, 3].map((r) => J(`exp6-replica-down-rep${r}`).alerts.ApiReplicaDown.firing_s),
  ...[1, 2].map((r) => J(`exp6-error-rate-rep${r}`).alerts.ApiHighErrorRate.firing_s),
  ...[1, 2].map((r) => J(`exp6-latency-rep${r}`).alerts.ApiHighLatencyP95.firing_s)];
const ack2 = (c) => sum(e2.map((d) => d.configs[c].integrity.acknowledged));
const e5b = e5("baseline");
const n429 = sum(e5b.map((s) => s.codes["429"])), nConn = sum(e5b.map((s) => s.codes.conn_error));
const fail6 = sum(e6r.map((s) => s.failed)), req6 = sum(e6r.map((s) => s.requests));
const retries3 = sum(e3("baseline").map((d) => d.client_retries_writer));
const failed3 = sum(e3("baseline").map((d) => d.stats_during.failed)), req3 = sum(e3("baseline").map((d) => d.stats_during.requests));
const oom = J("exp8-improved-rep1-INVALID-confounded-postgres-oom").stats_fault_window;
const f1 = (x) => x.toFixed(1);

const THEME = {
  name: "FTR Final", headFontFace: "Cambria", bodyFontFace: "Calibri",
  colors: { dk1: "13262F", dk2: "0F4C5C", lt1: "FFFFFF", lt2: "EDF3F5", accent1: "2A78D6", accent2: "EB6834", accent3: "1BAF7A",
    accent4: "EDA100", accent5: "6250D6", accent6: "52514E", hlink: "1C5CAB", folHlink: "6250D6" },
};

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.title = "Fault-Tolerant University Records Service";
pres.author = "Shamil Yakupov (255193)";
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
const C = pres.SchemeColor;

pres.defineSlideMaster({
  title: "CONTENT", background: { color: C.background1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.35, w: 12.1, h: 0.95, fontSize: 32, bold: true, color: C.text2, margin: 0, align: "left", valign: "top" }, text: "" } },
    { text: { text: "Shamil Yakupov, 255193 | Fault Tolerance and Dependable Computing | all data synthetic, measured on one laptop", options: { x: 0.6, y: 7.03, w: 10.5, h: 0.3, fontSize: 10, color: C.accent6, margin: 0, isTextBox: true } } },
  ],
  slideNumber: { x: 12.1, y: 7.03, w: 0.6, h: 0.3, fontSize: 10, color: C.accent6, align: "right" },
});
pres.defineSlideMaster({
  title: "DARK", background: { color: C.text2 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.8, y: 0.9, w: 11.7, h: 1.7, fontSize: 40, bold: true, color: C.background1, margin: 0, align: "left", valign: "top" }, text: "" } },
  ],
});

function title(s, t) { s.addText(t, { placeholder: "title" }); }
function card(s, x, y, w, h, fill) {
  s.addShape(pres.ShapeType.roundRect, { x, y, w, h, rectRadius: 0.06, fill: { color: fill || C.background2 }, line: { type: "none" } });
}
function txt(s, t, x, y, w, h, o) {
  s.addText(t, Object.assign({ x, y, w, h, fontSize: 14, color: C.text1, margin: 0, valign: "top", isTextBox: true }, o || {}));
}
function stat(s, x, y, w, h, big, label, color) {
  card(s, x, y, w, h);
  txt(s, big, x + 0.2, y + 0.15, w - 0.4, 0.9, { fontSize: 34, bold: true, color: color || C.text2, fontFace: THEME.headFontFace, fit: "shrink" });
  txt(s, label, x + 0.2, y + 1.05, w - 0.4, h - 1.15, { fontSize: 14 });
}

// 1 title
let s = pres.addSlide({ masterName: "DARK" });
title(s, "Fault-Tolerant University Records Service");
txt(s, "Baseline versus improved: eight fault-injection experiments, measured", 0.8, 2.75, 11.5, 0.6, { fontSize: 22, color: C.background1 });
txt(s, "Final Project, Fault Tolerance and Dependable Computing, Astana IT University", 0.8, 4.7, 11.5, 0.4, { fontSize: 16, color: C.background2 });
txt(s, "Shamil Yakupov, ID 255193 | Instructor: Azamat Serek | Individual work | 6 November 2026", 0.8, 5.15, 11.5, 0.4, { fontSize: 16, color: C.background2 });
s.addNotes("Option A: the university information system from the midterm and endterm. All data synthetic, local lab, no production system.");

// 2 system
s = pres.addSlide({ masterName: "CONTENT" });
title(s, "A wrong record is worse than an outage");
const steps = ["Sign in", "Choose record space", "Read ledgers", "Post a record", "Row + balance move in one transaction"];
steps.forEach((t, i) => {
  const x = 0.6 + i * 2.45;
  s.addShape(pres.ShapeType.roundRect, { x, y: 1.6, w: 2.25, h: 1.15, rectRadius: 0.06, fill: { color: i >= 3 ? C.text2 : C.background2 }, line: { type: "none" } });
  txt(s, t, x + 0.12, 1.6, 2.01, 1.15, { fontSize: 15, bold: true, valign: "middle", align: "center", color: i >= 3 ? C.background1 : C.text1 });
});
const cols2 = [["System", "hoWallet Go API as a stand-in for a university records service. Gateway, API, PostgreSQL, Prometheus, Grafana, migration job."],
  ["Data", "All synthetic: household = a student's record space, account = a ledger, transaction = a fee payment."],
  ["If it fails", "Unavailable: deadline risk. Duplicate or lost record: a fee charged twice. Disclosure: one student sees another's records."]];
cols2.forEach(([h, b], i) => {
  const x = 0.6 + i * 4.1;
  txt(s, h, x, 3.4, 3.8, 0.45, { fontSize: 20, bold: true, color: C.text2, fontFace: THEME.headFontFace });
  txt(s, b, x, 3.95, 3.8, 2.6, { fontSize: 18 });
});
s.addNotes("The critical workflow is multi-step: insert a row and move a balance. Comparison: same image and database, different infrastructure.");

// 3 objectives
s = pres.addSlide({ masterName: "CONTENT" });
title(s, "Objectives were set as numbers before the runs");
const obj = [["≥ 99 %", "success during one instance loss (SLO-2)"], ["≤ 500 ms", "p95 at 30 req/s (SLO-3)"], ["≤ 10.5 s", "worst GET answer under a dependency fault (SLO-4)"],
  ["≤ 10 / 30 s", "recovery: instance / database (SLO-5)"], ["0 lost, 0 dup", "acknowledged writes, balance = −Σ (SLO-6)"], ["≤ 30 s", "fault to alert firing, none in quiet windows (SLO-7)"]];
obj.forEach(([b, l], i) => stat(s, 0.6 + (i % 3) * 4.1, 1.55 + Math.floor(i / 3) * 2.4, 3.9, 2.15, b, l));
txt(s, "Plan: evidence/test-plan.md, written before the runs, with two dated amendments.", 0.6, 6.45, 12, 0.35, { fontSize: 14, color: C.accent6 });

// 4 architecture
s = pres.addSlide({ masterName: "CONTENT" });
title(s, "Two paths, one database: redundancy stops at PostgreSQL");
s.addImage({ path: path.join(ROOT, "report/fig/architecture.png"), x: (13.333 - 10.6 * 5.4 / 5.75) / 2, y: 1.25, w: 10.6 * 5.4 / 5.75, h: 5.4, altText: "Lab architecture: client, gateway x2, API x2, PostgreSQL, Prometheus, Grafana, and the baseline path" });
txt(s, "AI-generated figure (Claude Code, HTML/SVG rendered to PNG), checked by the author against k8s/; prompt in report Appendix 14.6.", 1.35, 6.68, 10.6, 0.3, { fontSize: 10, color: "64748B", italic: true });
s.addNotes("One kind node. Replicas on one node do not protect against node failure. PostgreSQL is a single point of failure in both configurations.");

// 5 mechanisms
s = pres.addSlide({ masterName: "CONTENT" });
title(s, "Mechanisms and their evidence");
card(s, 0.6, 1.5, 5.3, 5.2);
txt(s, "Infrastructure", 0.85, 1.65, 4.8, 0.45, { fontSize: 20, bold: true, color: C.text2, fontFace: THEME.headFontFace });
txt(s, [["API × 2, gateway × 2"], ["Replacement and liveness restart"], ["Rolling update and rollback"], ["preStop drain"], ["Postgres restart in place"]].map((t, i, a) => ({ text: t[0], options: { bullet: true, breakLine: i < a.length - 1 } })), 0.85, 2.2, 4.8, 3.1, { fontSize: 18, paraSpaceAfter: 8 });
txt(s, "Prometheus 3 alerts, Grafana, request id in gateway and API logs", 0.85, 5.55, 4.8, 0.9, { fontSize: 14, color: C.accent6 });
const mech = [["M1", "Timeouts, bounded retries", "experiments only"], ["M2", "Idempotent create", "16 unit tests"], ["M3", "Transaction rollback", "Exp 4; integration tests cited"],
  ["M4", "Health and readiness", "experiments only"], ["M5", "JSON 503 degradation", "2 unit tests"], ["M6", "Rate limit, Retry-After", "1 unit test"]];
const mrows = [[{ text: "", options: { fill: { color: C.text2 } } }, { text: "Mechanism", options: { bold: true, color: C.background1, fill: { color: C.text2 } } }, { text: "Proved by", options: { bold: true, color: C.background1, fill: { color: C.text2 } } }]]
  .concat(mech.map((r) => [{ text: r[0], options: { bold: true } }, { text: r[1] }, { text: r[2] }]));
s.addTable(mrows, { x: 6.2, y: 1.5, w: 6.5, colW: [0.7, 2.9, 2.9], rowH: 0.72, fontSize: 15, color: C.text1, border: { type: "solid", pt: 0.5, color: "D5DEE1" }, valign: "middle" });
s.addNotes("20 unit tests passed. M1 and M4 have no Go test. For M3 the unit test only proves error propagation; Exp 4 is the evidence.");

// 6 matrix
s = pres.addSlide({ masterName: "CONTENT" });
title(s, "Eight experiments, 2–4 repetitions, baseline and improved");
const hdr = ["#", "Scenario", "Fault", "Runs", "Improved", "Baseline"].map((t) => ({ text: t, options: { bold: true, color: C.background1, fill: { color: C.text2 } } }));
const tag = (t) => ({ text: t, options: { bold: true, color: t.startsWith("FAIL") ? C.accent2 : (t.startsWith("PASS") ? C.accent3 : C.accent6) } });
const mx = [["1", "Instance failure", "pod delete, graceful and forced", "3 + 3", "PASS", "no target"], ["2", "Database unavailable", "stop or freeze Postgres, 20 s", "4 + 4", "PASS", "PASS"],
  ["3", "Network delay", "netem 2000 ms, one replica", "3 + 3", "PASS", "no target"], ["4", "Interrupted transaction", "SIGKILL between insert and balance", "3 + 3", "PASS", "PASS"],
  ["5", "High load", "5 → 30 → 5 req/s", "2 + 2", "PASS", "FAIL"], ["6", "Monitoring", "hang, database stop, locks, quiet", "9", "PASS", "no rules"],
  ["7", "Unauthorized, invalid", "22 access, 38 input cases", "4", "access FAIL, input PASS", "–"], ["8", "Rollback", "broken release, rollout undo", "2 + 2", "PASS", "PASS"]];
s.addTable([hdr].concat(mx.map((r) => [r[0], r[1], r[2], r[3], tag(r[4]), tag(r[5])].map((c) => (typeof c === "string" ? { text: c } : c)))), { x: 0.6, y: 1.45, w: 12.1, colW: [0.5, 2.8, 3.9, 0.8, 2.6, 1.5], rowH: 0.56, fontSize: 15, color: C.text1, border: { type: "solid", pt: 0.5, color: "D5DEE1" }, valign: "middle" });
s.addNotes("Exp 7 has two sets: access control is a recorded FAIL because of one wrong expected value; input and limits passed. INVALID runs are kept and excluded.");

// 7 key results chart
s = pres.addSlide({ masterName: "CONTENT" });
title(s, "Success during the fault: Exp 2 is the exception");
s.addChart(pres.charts.BAR, [
  { name: "Baseline", labels: success.map((r) => r[0]), values: success.map((r) => r[1]) },
  { name: "Improved", labels: success.map((r) => r[0]), values: success.map((r) => r[2]) },
], { x: 0.6, y: 1.4, w: 8.4, h: 5.4, barDir: "col", barGrouping: "clustered", chartColors: [THEME.colors.accent2, THEME.colors.accent1], showLegend: true, legendPos: "t", legendFontSize: 14, legendFontFace: "+mn-lt",
  showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 12, dataLabelFormatCode: "0", dataLabelFontFace: "+mn-lt", valAxisMaxVal: 110, valAxisMinVal: 0, valAxisMajorUnit: 25, valAxisLabelFontSize: 12, catAxisLabelFontSize: 13, catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt",
  showValAxisTitle: true, valAxisTitle: "requests OK, %", valAxisTitleFontSize: 12, valGridLine: { color: "E1E0D9", size: 0.5 }, catGridLine: { style: "none" } });
txt(s, [{ text: "Exp 2 is not an availability gain for either.", options: { bold: true, breakLine: true } },
  { text: "Baseline requests wait up to 12 s, so some finish after the restore. The gain is bounded response time.", options: { breakLine: true } },
  { text: " ", options: { breakLine: true } },
  { text: "Exp 5* excludes 429.", options: { bold: true, breakLine: true } },
  { text: `Baseline also got ${n429} × 429 from the per-process limiter.` }], 9.3, 1.6, 3.4, 4.8, { fontSize: 16 });
s.addNotes("Fault-window ratios from the consolidated table. Exp 1 and Exp 4 are after the injection, Exp 2 and Exp 3 during the fault, Exp 5 in the step phase.");

// 8 key results stats
s = pres.addSlide({ masterName: "CONTENT" });
title(s, "What the mechanisms bought, in time");
stat(s, 0.6, 1.5, 3.9, 2.35, `${f1(Math.min(...out1))}–${f1(Math.max(...out1))} s → 0`, "Exp 1 outage after a graceful delete, baseline → improved (forced delete: 2.0 s → 0)", C.text2);
stat(s, 4.7, 1.5, 3.9, 2.35, `${f1(m3b)} s → ${m3i.toFixed(2)} s`, `Exp 3 worst response under delay; improved keeps ${sum(e3("improved").map((d) => d.stats_during.success))}/${sum(e3("improved").map((d) => d.stats_during.requests))} OK`, C.text2);
stat(s, 8.8, 1.5, 3.9, 2.35, `${f1(m2b)} s → ${f1(m2i)} s`, "Exp 2 worst response with the database down, with a JSON 503", C.text2);
stat(s, 0.6, 4.05, 3.9, 2.35, `${retry4b[0]} s → ${Math.min(...retry4i)}–${Math.max(...retry4i)} s`, "Exp 4 retry after a first crash; later baseline retries are longer, likely kubelet back-off", C.text2);
stat(s, 4.7, 4.05, 3.9, 2.35, `${Math.min(...fire6)}–${Math.max(...fire6)} s`, "Exp 6 fault to alert firing; zero alerts in quiet windows", C.text2);
stat(s, 8.8, 4.05, 3.9, 2.35, "0 lost, 0 dup", `Every write run in both configs (Exp 2: ${ack2("baseline")} and ${ack2("improved")} acked). In the application, so it does not separate them`, C.text2);

// 9 honest failures
s = pres.addSlide({ masterName: "CONTENT" });
title(s, "What failed, or did not help");
const fails = [
  ["Exp 7: recorded FAIL", "One case expected 404, got 405: that route has no GET. Nothing disclosed. Stays as written."],
  ["Exp 3: hypothesis wrong", `Predicted 8–10 s; p50 10–18 s, ${Math.round(100 * failed3 / req3)} % failed, with ${retries3} client retries confounding.`],
  ["Exp 3: false positive", "ApiHighErrorRate fired in all improved runs while clients saw no failure."],
  ["Exp 5: limiter artefact", `Two replicas double the limit. Cause of ${nConn} connection errors not established.`],
  ["Exp 6: no failover", `${fail6} of ${req6} got 503 after 5 s on a hung replica. DNS explanation is a hypothesis.`],
  ["Exp 8: no gain", "A default rolling update also survived. No alert noticed the stuck rollout."],
  ["Postgres OOM at 256 Mi", `Run at ${pct(oom.success, oom.requests)} % kept INVALID, limit raised to 1 Gi by dated change. Supports memory pressure, does not prove it.`],
  ["Endterm figure corrected", `23 s was understated; now ${f1(Math.min(...out1))}–${f1(Math.max(...out1))} s. Why graceful is slower than forced: not established.`],
];
fails.forEach(([h, b], i) => {
  const x = 0.6 + (i % 4) * 3.07, y = 1.45 + Math.floor(i / 4) * 2.75;
  card(s, x, y, 2.9, 2.6);
  txt(s, h, x + 0.15, y + 0.12, 2.6, 0.72, { fontSize: 15, bold: true, color: C.text2 });
  txt(s, b, x + 0.15, y + 0.84, 2.6, 1.7, { fontSize: 14 });
});

// 10 measured vs illustrative
s = pres.addSlide({ masterName: "CONTENT" });
title(s, "Measured and theoretical are not the same claim");
const relRows = [["Exp", "Config", "MTTR (s)", "A", "R_req"]].map((r) => r.map((t) => ({ text: t, options: { bold: true, color: C.background1, fill: { color: C.text2 } } })));
["1-baseline", "1-improved", "2-baseline", "2-improved", "3-baseline", "3-improved"].forEach((k) => {
  const r = REL[k];
  relRows.push([k.split("-")[0], k.split("-")[1], r.censored ? "undefined" : f1(r.MTTR_s), (r.A_time * 100).toFixed(2) + " %", (r.R_req * 100).toFixed(2) + " %"].map((t) => ({ text: t })));
});
txt(s, "Measured, short fault windows", 0.6, 1.4, 6.7, 0.45, { fontSize: 20, bold: true, color: C.text2, fontFace: THEME.headFontFace });
s.addTable(relRows, { x: 0.6, y: 1.9, w: 6.7, colW: [0.7, 1.5, 1.5, 1.5, 1.5], rowH: 0.45, fontSize: 15, color: C.text1, border: { type: "solid", pt: 0.5, color: "D5DEE1" }, valign: "middle" });
txt(s, "MTTF and λ mostly reflect injection timing, so they are not field reliability.", 0.6, 5.3, 6.7, 0.7, { fontSize: 16, bold: true });
card(s, 7.7, 1.4, 5.0, 2.75);
txt(s, "ILLUSTRATIVE, not measured", 7.95, 1.52, 4.5, 0.4, { fontSize: 16, bold: true, color: C.accent2 });
txt(s, "Assume each instance A = 0.999. Improved path ≈ 0.998998, baseline path ≈ 0.998001. What remains is the database.", 7.95, 1.98, 4.5, 2.0, { fontSize: 16 });
txt(s, [["One node, laptop, load generator on it"], ["Small repetition counts: behaviour, not statistics"], ["Replicas do not protect against node failure"], ["PostgreSQL is a single point of failure"]].map((t, i, a) => ({ text: t[0], options: { bullet: true, breakLine: i < a.length - 1 } })), 7.7, 4.35, 5.0, 2.4, { fontSize: 15, paraSpaceAfter: 6 });
s.addNotes("Theory assumes independent failures and instant failover. Exp 2 shows both replicas failing together when the shared database is lost.");

// 11 demo
s = pres.addSlide({ masterName: "CONTENT" });
title(s, "Live demonstration: four scenarios");
const demo = [["1", "Instance failure", `Delete a pod under 8 req/s. Expect no failed request; replacement in about ${Math.min(...e1i.map((d) => d.recovery.recovery_s))}–${Math.max(...e1i.map((d) => d.recovery.recovery_s))} s.`],
  ["2", "Database outage", "Stop Postgres. Expect JSON 503 and 500 with a bound, alerts at 15–21 s, recovery without intervention."],
  ["3", "Interrupted transaction", `SIGKILL between insert and balance, retry with the same key. Expect one row, balance = −Σ, retry in ${Math.min(...retry4i)}–${Math.max(...retry4i)} s.`],
  ["4", "Unauthorized access", "No token 401, other tenant 404, oversized body 413, denial visible under one request id."]];
demo.forEach(([n, h, b], i) => {
  const y = 1.5 + i * 1.3;
  s.addShape(pres.ShapeType.ellipse, { x: 0.6, y: y + 0.1, w: 0.8, h: 0.8, fill: { color: C.text2 }, line: { type: "none" } });
  txt(s, n, 0.6, y + 0.1, 0.8, 0.8, { fontSize: 22, bold: true, align: "center", valign: "middle", color: C.background1 });
  txt(s, h, 1.7, y, 3.2, 1.0, { fontSize: 20, bold: true, color: C.text2, valign: "middle", fontFace: THEME.headFontFace });
  txt(s, b, 5.0, y, 7.7, 1.0, { fontSize: 16, valign: "middle" });
});
s.addNotes("Commands, screens and restore steps are in report/demo-runbook.md. Controlled local lab only.");

// 12 conclusions
s = pres.addSlide({ masterName: "DARK" });
title(s, "Conclusions");
const conc = ["Helps when one instance fails or slows down: Exp 1, Exp 3, retry time in Exp 4",
  "A database outage is not more available; it is bounded and signalled",
  "Write integrity comes from the application and holds in both configurations",
  "Next: database replica and timed restore drill, shared limiter, rollout alert, real multi-node runs"];
txt(s, conc.map((t, i, a) => ({ text: t, options: { bullet: true, breakLine: i < a.length - 1, color: C.background1 } })), 0.8, 2.3, 11.7, 3.6, { fontSize: 22, paraSpaceAfter: 14, color: C.background1 });
txt(s, "Raw data, scripts and report: repository, evidence/results and analysis", 0.8, 6.3, 11.7, 0.4, { fontSize: 14, color: C.background2 });

(async () => {
  const out = path.join(__dirname, "Final_FTR_Yakupov_255193.pptx");
  await pres.writeFile({ fileName: out });
  if (process.env.APPLY_THEME) {
    const { applyTheme } = require(process.env.APPLY_THEME);
    await applyTheme(out, THEME);
  }
  console.log("saved", out, JSON.stringify(success));
})();
