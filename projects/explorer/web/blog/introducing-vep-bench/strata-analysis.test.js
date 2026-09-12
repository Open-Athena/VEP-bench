import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {gunzipSync} from "node:zlib";
import {createHash} from "node:crypto";
import test from "node:test";
import {stratumRows, stratumCsv, stratumModelOrder} from "./strata-analysis.js";
import {modelFamilyScale} from "../../components/benchmark-data.js";

const snapshot = JSON.parse(gunzipSync(readFileSync(new URL("./strata-2026-09-12.json.gz", import.meta.url))));
const intervals = JSON.parse(readFileSync(new URL("./strata-2026-09-12.intervals.json", import.meta.url)));

test("intervals are tied to the exact frozen scores and remain available in downloads", () => {
  const raw = readFileSync(new URL("./strata-2026-09-12.json.gz", import.meta.url));
  assert.equal(intervals.snapshot_sha256, createHash("sha256").update(raw).digest("hex"));
  assert.equal(intervals.statistics.method, "Student's t");
  assert.equal(intervals.statistics.confidence_level, 0.95);
  const rows = stratumRows(snapshot, intervals);
  assert.equal(rows.length, 96);
  for (const row of rows) {
    if (row.spearman_ci_status === "estimated") {
      assert.ok(row.spearman_ci_low < row.mean_spearman_rho);
      assert.ok(row.spearman_ci_high > row.mean_spearman_rho);
    } else {
      assert.equal(row.spearman_ci_low, null);
      assert.equal(row.spearman_ci_high, null);
    }
  }
  const csv = stratumCsv(snapshot, intervals);
  assert.ok(csv.includes('"spearman_ci_low","spearman_ci_high","spearman_ci_status"'));
  assert.ok(csv.includes(String(rows[0].spearman_ci_low)));
});

test("missing, duplicate or mismatched intervals fail instead of being silently plotted", () => {
  const changed = structuredClone(intervals);
  changed.intervals.pop();
  assert.throws(() => stratumRows(snapshot, changed), /do not match/);
  changed.intervals.push(changed.intervals[0]);
  assert.throws(() => stratumRows(snapshot, changed), /do not match/);
  const wrongMean = structuredClone(intervals);
  wrongMean.intervals[0].mean_spearman_rho += 0.01;
  assert.throws(() => stratumRows(snapshot, wrongMean), /differs/);
});

test("model order follows the frozen overall leaderboard, independently of stratum scores", () => {
  const expected = ["GPT 6 Astra (high)", "Gemini 3.8 Flash (high)", "GPT 5.6 Sol (max)",
    "GPT 5.6 Terra (max)", "GPT 5.6 Luna (max)", "Muse Spark 1.3 (medium)",
    "GLM 5.3 (low)", "DeepSeek V4.1 Flash (low)"];
  assert.deepEqual(stratumModelOrder(snapshot), expected);
  const changed = structuredClone(snapshot);
  changed.results.reverse().forEach((row) => {row.mean_spearman_rho = -row.mean_spearman_rho;});
  assert.deepEqual(stratumModelOrder(changed), expected);
  const topModel = snapshot.runs.find((run) => run.model.family === "GPT 6 Astra").model.model_id;
  changed.leaderboard.runs.filter((run) => run.model.model_id === topModel)
    .forEach((run) => {run.metrics.mean_spearman_rho = -1;});
  assert.deepEqual(stratumModelOrder(changed), [...expected.slice(1), expected[0]]);
});

test("the frozen blog palette matches the shared website palette", () => {
  const colors = JSON.parse(readFileSync(new URL("../../components/model-family-colors.json", import.meta.url)));
  const families = [...new Set(stratumRows(snapshot).map((row) => row.family))];
  assert.deepEqual(modelFamilyScale(families, snapshot.family_colors), modelFamilyScale(families, colors));
});

test("frozen snapshot contains only scores on the predeclared shared panel memberships", () => {
  assert.deepEqual(snapshot.policy, {minimum_variants_per_panel: 10, minimum_panels_per_task: 5});
  assert.equal(snapshot.coverage.filter((row) => row.status === "eligible").length, 12);
  assert.equal(snapshot.results.length, 96);
  const rows = stratumRows(snapshot);
  assert.equal(rows.length, snapshot.results.length);
  for (const coverage of snapshot.coverage) {
    assert.equal(coverage.variant_count, coverage.eligible_variants + coverage.below_cutoff_variants + coverage.unsupported_variants);
    assert.equal(coverage.total_panels, coverage.eligible_panels + coverage.excluded_panels);
    const scores = rows.filter((row) => row.task_family === coverage.task_family && row.axis === coverage.axis && row.category === coverage.category);
    if (coverage.status !== "eligible") {
      assert.equal(scores.length, 0);
      continue;
    }
    assert.equal(scores.length, 8);
    const panels = coverage.panels.filter((panel) => panel.status === "eligible");
    assert.ok(panels.every((panel) => panel.candidate_ids.length >= 10));
    for (const score of scores) {
      assert.deepEqual(score.panels.map((panel) => panel.question_id), panels.map((panel) => panel.question_id));
      assert.ok(Number.isFinite(score.mean_spearman_rho));
    }
  }
  const bytes = readFileSync(new URL("../../components/correlation-plot.js", import.meta.url));
  assert.equal(createHash("sha256").update(bytes).digest("hex"), snapshot.plot_sha256,
    "The frozen plot source changed; retain its rendering or document a dated correction.");
});

test("CSV retains insufficient groups with empty scores and keeps negative correlations", () => {
  const csv = stratumCsv(snapshot);
  assert.ok(csv.includes('"satmut_mpra","allele_type","Deletion","insufficient_coverage","60"'));
  const row = csv.split("\n").find((line) => line.startsWith('"satmut_mpra","allele_type","Deletion"'));
  assert.ok(row.endsWith(',"","","","",""'));
  assert.ok(stratumRows(snapshot).some((row) => row.mean_spearman_rho < 0));
  const changed = structuredClone(snapshot);
  changed.coverage.forEach((row) => {row.status = "insufficient_coverage";});
  assert.deepEqual(stratumRows(changed), []);
});
