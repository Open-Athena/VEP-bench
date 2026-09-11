import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {compareScores, matchStatus, summarizePairs} from "./comparisons.js";

const pairs = (x, y) => x.map((v, i) => ({model_id: `model-${i}`, vep_score: v, external_score: y[i]}));

test("correlations use tied ranks within the shared models", () => {
  const result = summarizePairs(pairs([1, 2, 2, 4, 5], [5, 3, 3, 2, 1]));
  assert.equal(result.spearman, -1);
  assert.ok(result.pearson > -1);
  assert.deepEqual(result.leave_one_out, []);
});

test("sparse, constant, duplicate, and missing observations cannot yield a correlation", () => {
  assert.equal(summarizePairs(pairs([1, 2, 3, 4], [4, 3, 2, 1])).spearman, null);
  assert.equal(summarizePairs(pairs([1, 1, 1, 1, 1], [1, 2, 3, 4, 5])).spearman, null);
  const repeated = pairs([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]);
  repeated[4].model_id = repeated[0].model_id;
  assert.throws(() => summarizePairs(repeated), /Repeated model/);
  assert.throws(() => summarizePairs(pairs([1], [null])), /nonfinite/);
});

test("leave-one-model-out recomputes ranks and identifies an influential observation", () => {
  const result = summarizePairs(pairs([0, 1, 2, 3, 4, 5], [5, 1, 2, 3, 4, 0]));
  assert.equal(result.leave_one_out.length, 6);
  for (let i = 0; i < 6; i++) {
    const x = [0, 1, 2, 3, 4, 5].filter((_, j) => i !== j);
    const y = [5, 1, 2, 3, 4, 0].filter((_, j) => i !== j);
    assert.equal(result.leave_one_out[i].spearman, summarizePairs(pairs(x, y)).spearman);
  }
  assert.ok(result.leave_one_out.some((r) => r.material_change));
});

test("matching never substitutes efforts, revisions, releases, or unknown settings", () => {
  const configurations = [{model_id: "model-v2", model_revision: null, effort: "high"}];
  const result = {model_id: "model-v2", effort: "high"};
  assert.equal(matchStatus(result, configurations), "Exact version and effort match");
  assert.match(matchStatus({...result, effort: "max"}, configurations), /No exact effort/);
  assert.match(matchStatus({...result, effort: null}, configurations), /not explicitly reported/);
  assert.match(matchStatus({...result, model_id: "model-v1"}, configurations), /No complete/);
  assert.match(matchStatus({...result, model_revision: "new"}, configurations), /No complete/);
  assert.match(matchStatus(result, [...configurations, ...configurations]), /Ambiguous/);
});

const readSnapshot = (file) => JSON.parse(readFileSync(new URL(`./comparisons-data/${file}`, import.meta.url)));

test("frozen snapshot respects exact efforts, independent models, and separate harnesses", () => {
  const vep = readSnapshot("vep-runs.json"), external = readSnapshot("external.json");
  const analysis = compareScores(vep, external);
  assert.deepEqual(analysis.comparisons, readSnapshot("analysis.json").primary);
  const aa = analysis.comparisons.find((c) => c.id === "aa-intelligence" && c.scope === "overall");
  assert.equal(aa.pairs.length, 4);
  assert.equal(aa.summary.spearman, null);
  assert.ok(aa.pairs.every((p) => p.effort === "high"));
  for (const c of analysis.comparisons) {
    assert.equal(new Set(c.pairs.map((p) => p.model_id)).size, c.pairs.length);
    for (const p of c.pairs) {
      const result = external.results.find((r) => r.id === p.external_result_id);
      assert.equal(result.group, c.group);
      assert.equal(result.effort, p.generation_parameters.reasoning.effort);
    }
  }
  const lower = compareScores(vep, external, {effort: "medium"});
  assert.ok(lower.comparisons.every((c) => c.pairs.every((p) => p.effort === "medium")));
  const low = compareScores(vep, external, {effort: "low"});
  assert.equal(low.comparisons.find((c) => c.id === "aa-intelligence" && c.scope === "overall").pairs.length, 4);
  assert.equal(low.comparisons.find((c) => c.id === "gene" && c.scope === "overall").pairs.length, 2);
  const deepseek = analysis.matches.find((r) => r.model_id === "deepseek/deepseek-v4.1-flash");
  assert.equal(deepseek.effort, "max");
  assert.equal(deepseek.match_status, "No exact effort match");
  // Perturbing scores must not alter selection; this catches best-score selection.
  const changed = structuredClone(external);
  changed.results.forEach((r) => Object.keys(r.scores).forEach((key) => { r.scores[key] *= -1; }));
  assert.deepEqual(compareScores(vep, changed).comparisons.map((c) => c.pairs.map((p) => p.external_result_id)),
    analysis.comparisons.map((c) => c.pairs.map((p) => p.external_result_id)));
});
