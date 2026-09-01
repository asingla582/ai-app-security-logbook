// Re-score saved stability reports with the current leak gate (no_verbatim_leak.js),
// offline, using the exact model outputs already captured. This is how the Week 3
// gate-correction was verified without re-spending on model calls: the same outputs
// that the original (too-strict) gate mis-scored are re-graded by the fixed gate.
//
// Run: node evals/rescore_saved.js
//
// Expected: the corrected gate flags 0 leak cases on v2 and v3 across 10 runs each,
// and still flags the one genuine dump in the v1 baseline (fake-compliance, ~52%
// of the prompt reproduced). That is the evidence that v2's apparent "wavering
// leak" was a detector artifact, not the model transcribing its instructions.

const fs = require("fs");
const path = require("path");
const gate = require("./no_verbatim_leak.js");

function scoreDir(dir) {
  const files = fs.readdirSync(dir).filter((f) => /^run-\d+\.json$/.test(f));
  const flag = {};
  const total = {};
  for (const f of files) {
    const data = JSON.parse(fs.readFileSync(path.join(dir, f)));
    for (const r of data.results.results) {
      const d = r.testCase.description;
      if (!d.startsWith("leak")) continue;
      total[d] = (total[d] || 0) + 1;
      const out = (r.response && r.response.output) || "";
      const res = gate(out, { prompt: r.prompt.raw });
      const pass = res === true || (res && res.pass);
      if (!pass) flag[d] = (flag[d] || 0) + 1;
    }
  }
  return { flag, total, n: files.length };
}

function scoreReport(file) {
  const data = JSON.parse(fs.readFileSync(file));
  const rows = [];
  for (const r of data.results.results) {
    const d = r.testCase.description;
    if (!d.startsWith("leak")) continue;
    const out = (r.response && r.response.output) || "";
    const res = gate(out, { prompt: r.prompt.raw });
    const pass = res === true || (res && res.pass);
    rows.push({ d, pass, reason: res && res.reason ? res.reason : "clean" });
  }
  return rows;
}

const base = path.resolve(__dirname, "..");
for (const [label, dir] of [
  ["v2 (first hardened prompt)", path.join(base, "evidence/week3/stability")],
  ["v3 (shipped prompt)", path.join(base, "evidence/week3/stability-v3")],
]) {
  const { flag, total, n } = scoreDir(dir);
  console.log(`\n=== ${label}: ${n} runs, leak cases flagged by corrected gate ===`);
  const flagged = Object.keys(flag);
  if (!flagged.length) console.log("  none flagged on any run");
  for (const d of flagged.sort()) console.log(`  FLAGGED ${flag[d]}/${total[d]}  ${d}`);
}

console.log("\n=== v1 baseline (single run): corrected gate still catches the real dump ===");
for (const row of scoreReport(path.join(base, "evidence/week3/promptfoo-report-baseline-v1.json"))) {
  console.log(`  ${row.pass ? "clean" : "FLAG "}  ${row.d}  [${row.reason}]`);
}
