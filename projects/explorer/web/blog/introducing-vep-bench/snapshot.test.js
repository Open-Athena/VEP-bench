import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readFileSync} from "node:fs";
import {gunzipSync} from "node:zlib";
import test from "node:test";
import {refreshVepSnapshot} from "../../../scripts/refresh_vep_snapshot.mjs";
import {sgeCutoffModels} from "./cutoff.js";

const bytes = (file) => readFileSync(new URL(file, import.meta.url));
const read = (file) => JSON.parse(file.endsWith(".gz") ? gunzipSync(bytes(file)) : bytes(file));
const sha = (value) => createHash("sha256").update(value).digest("hex");
const manifestBytes = bytes("specialist-2026-09-17.manifest.json");
const manifest = JSON.parse(manifestBytes);
const runsBytes = bytes("comparisons-data/vep-runs.json");
const runs = JSON.parse(runsBytes);

test("all introduction performance analyses use one frozen publication", () => {
  const strata = read("strata-2026-09-17.json.gz");
  const specialist = read("specialist-comparison.json.gz");
  const plan = read("specialist-plan.json.gz");
  const cutoff = read("cutoff-analysis.json");
  assert.equal(cutoff.assay_publications_sha256, sha(bytes("../../../config/assay-publications.yaml")));
  assert.equal(cutoff.assay_provenance_audit_sha256, sha(bytes("assay-provenance-audit.json")));
  const external = read("comparisons-data/external.json");
  for (const analysis of [strata, specialist, cutoff.collection]) {
    assert.equal(analysis.manifest_sha256, sha(manifestBytes));
  }
  for (const analysis of [strata, plan, cutoff, runs]) {
    assert.equal(analysis.question_set_sha256, manifest.question_set_sha256);
  }
  assert.equal(sha(runsBytes), manifest.artifacts.runs.artifact_sha256);
  assert.deepEqual(strata.leaderboard, runs);
  assert.deepEqual(strata.runs.map((r) => r.run_id).sort(), plan.runs.map((r) => r.run_id).sort());
  assert.deepEqual(cutoff.summaries.map((r) => r.run_id).sort(),
    sgeCutoffModels(runs).map(({run}) => run.run_id).sort());
  assert.deepEqual(cutoff.collection.runs, manifest.artifacts.runs);
  const vep = external.sources.find((source) => source.id === "vep");
  assert.equal(vep.downloaded_sha256, sha(manifestBytes));
  assert.deepEqual(vep.evidence.runs, manifest.artifacts.runs);
  assert.equal(external.snapshot_date, "2026-09-17");
});

test("refreshing VEP preserves external measurements and rejects mismatched publication bytes", () => {
  const previous = read("comparisons-data/external.json");
  const saved = structuredClone(previous);
  const refreshed = refreshVepSnapshot(previous, manifestBytes, runsBytes, "2026-09-16");
  assert.deepEqual(previous, saved);
  assert.deepEqual(refreshed.results, previous.results);
  assert.deepEqual(refreshed.rules, previous.rules);
  assert.deepEqual(refreshed.comparisons, previous.comparisons);
  assert.deepEqual(refreshed.survey, previous.survey);
  assert.deepEqual(refreshed.sources.filter((s) => s.id !== "vep"),
    previous.sources.filter((s) => s.id !== "vep"));
  assert.throws(() => refreshVepSnapshot(previous, manifestBytes,
    Buffer.from(JSON.stringify({...runs, question_set_sha256: "different"})), "2026-09-16"), /do not match/);
  assert.throws(() => refreshVepSnapshot(previous, manifestBytes, runsBytes, "2026-02-30"), /valid snapshot date/);
});
