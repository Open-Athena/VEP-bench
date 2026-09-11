import assert from "node:assert/strict";
import test from "node:test";
import {cutoffCsv, sgeCutoffModels, sgeCutoffPerformance} from "./cutoff.js";

function fixture(cutoff = "2025-06") {
  const dates = ["2020-01-01", "2025-05-31", "2025-06-01", "2025-07-01"];
  const questions = dates.map((date, i) => ({
    question_id: `q${i}`, metadata: {task_family: "sge"},
    provenance: {source_record_id: `G${i}`, source_record_sha256: `source${i}`}
  }));
  const metadata = {by_task_family: {sge: Object.fromEntries(dates.map((date, i) => [
    `G${i}`, {element: `G${i}`, source_record_sha256: `source${i}`, assay_first_indexed: {date, url: "https://example.test/assay"}}
  ]))}};
  const run = {
    run_id: "test", configuration_key: "config", evaluation_profile: "sge:v2",
    model: {model_id: "test/model", knowledge_cutoff: cutoff, knowledge_cutoff_url: "https://example.test/model"},
    question_set_size: 4, question_set_sha256: "set", task_type: "ranking",
    coverage: {complete: true}, metrics: {mean_spearman_rho: 0.05},
    completed_at: "2026-09-01T00:00:00Z"
  };
  const models = [{run, model_cell: {model: "Test model", provider: "Test provider"}}];
  const document = {
    schema_version: "1.0", run_id: "test", question_set_size: 4, question_set_sha256: "set",
    outcomes: [-0.4, 0, 0.4, 0.2].map((score, i) => ({question_id: `q${i}`, spearman_rho: score, valid: i !== 1}))
  };
  const states = new Map([["test", {document}]]);
  return {models, run, questions, metadata, states, document};
}

const analyze = (f) => sgeCutoffPerformance(f.models, f.questions, f.metadata, f.states);

test("cutoff comparison equally weights genes, keeps negative and invalid zero scores, and excludes the cutoff month", () => {
  const f = fixture();
  f.questions.push({question_id: "other", metadata: {task_family: "satmut_mpra"}});
  const {summaries: [row], scores} = analyze(f);
  assert.equal(row.before_n, 2);
  assert.equal(row.before_mean, -0.2);
  assert.equal(row.before_format_failures, 1);
  assert.equal(row.after_n, 1);
  assert.equal(row.after_mean, 0.2);
  assert.equal(row.difference, -0.4);
  assert.equal(row.excluded_n, 1);
  assert.equal(scores[2].relation, "Unknown");
  assert.equal(scores.length, 4);
});

test("exact-day cutoff includes that day; different model cutoffs change membership", () => {
  const f = fixture("2025-06-01");
  const row = analyze(f).summaries[0];
  assert.equal(row.before_n, 3);
  assert.equal(row.after_n, 1);
  assert.equal(row.excluded_n, 0);
  assert.equal(row.before_mean, 0);
});

test("missing or mismatched provenance excludes panels; empty groups have no score or difference", () => {
  const f = fixture("2026-01");
  delete f.metadata.by_task_family.sge.G0.assay_first_indexed;
  f.metadata.by_task_family.sge.G1.source_record_sha256 = "wrong";
  const row = analyze(f).summaries[0];
  assert.equal(row.excluded_n, 2);
  assert.equal(row.before_n, 2);
  assert.equal(row.after_n, 0);
  assert.equal(row.after_mean, null);
  assert.equal(row.difference, null);
});

test("incomplete, duplicate, foreign or malformed outcomes omit the whole run", () => {
  for (const change of [
    (f) => f.states.clear(),
    (f) => f.states.set("test", {error: new Error("offline")}),
    (f) => delete f.document.outcomes,
    (f) => f.document.outcomes.pop(),
    (f) => f.document.outcomes[0].question_id = "q1",
    (f) => f.document.outcomes[0].question_id = "foreign",
    (f) => f.document.outcomes[0].spearman_rho = null,
    (f) => f.document.outcomes[0].spearman_rho = 1.1,
    (f) => f.document.question_set_sha256 = "wrong",
    (f) => f.document.run_id = "wrong"
  ]) {
    const f = fixture();
    change(f);
    assert.deepEqual(analyze(f), {summaries: [], scores: [], unavailable: ["Test model"]});
  }
});

test("model selection uses only latest complete SGE configurations with known cutoffs", () => {
  const {run} = fixture();
  const runs = [run,
    {...run, run_id: "old", completed_at: "2026-08-01T00:00:00Z"},
    {...run, run_id: "incomplete", configuration_key: "incomplete", coverage: {complete: false}},
    ...[null, "2025-02-30", "unknown"].map((cutoff, i) => ({...run, configuration_key: `unknown${i}`, model: {...run.model, model_id: `unknown${i}`, knowledge_cutoff: cutoff}})),
    {...run, configuration_key: "other", evaluation_profile: "mpra:v2"}
  ];
  const leaderboard = {evaluation_profiles: [{task_family: "sge", evaluation_profile: "sge:v2"}]};
  assert.deepEqual(sgeCutoffModels({runs, leaderboard}).map(({run}) => run.run_id), ["test"]);
});

test("selects highest effort per model even when lower efforts score better; ties use recency", () => {
  const {run} = fixture();
  const candidate = (id, effort, score, completed_at = run.completed_at) => ({...run,
    run_id: id, configuration_key: id, generation_parameters: {reasoning: {effort}},
    metrics: {mean_spearman_rho: score}, completed_at
  });
  const runs = [candidate("low", "low", 0.9), candidate("high-old", "high", 0.8),
    candidate("high-new", "high", 0.2, "2026-09-02T00:00:00Z"),
    {...candidate("other-medium", "medium", 0.5), model: {...run.model, model_id: "other/model"}}
  ];
  const leaderboard = {evaluation_profiles: [{task_family: "sge", evaluation_profile: "sge:v2"}]};
  assert.deepEqual(sgeCutoffModels({runs, leaderboard}).map(({run}) => run.run_id).sort(),
    ["high-new", "other-medium"]);
  runs[2].model = {...run.model, knowledge_cutoff: null};
  assert.deepEqual(sgeCutoffModels({runs, leaderboard}).map(({run}) => run.run_id), ["other-medium"]);
});

test("download preserves provenance, nulls, and CSV escaping", () => {
  assert.equal(cutoffCsv([{model: 'test, "model"', mean: null, n: 0}]),
    '"model","mean","n"\n"test, ""model""","","0"\n');
  const csv = cutoffCsv(analyze(fixture()).scores);
  for (const value of ["question_set_sha256", "assay_url", "knowledge_cutoff_url", "Unknown"]) {
    assert.ok(csv.includes(value));
  }
});
