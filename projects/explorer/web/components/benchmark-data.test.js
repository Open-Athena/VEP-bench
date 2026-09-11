import assert from "node:assert/strict";
import {gzipSync} from "node:zlib";
import test from "node:test";

import {
  answerPath,
  assayCutoffRelation,
  defaultQuestionForExplorer,
  displayScore,
  executionSummaryForRow,
  fetchAnswer,
  fetchAnswerIfAvailable,
  fetchJson,
  fetchOutcomeIndex,
  groupCurrentRuns,
  formatRunLabel,
  leaderboardRows,
  leaderboardRowsForScope,
  modelSelectionRows,
  orderQuestionsForExplorer,
  orderTaskFamilies,
  overallLeaderboardRows,
  outcomeIndexPath,
  predictionComparisonRows,
  questionDisplayMetadata,
  rankingOutcomeMetrics,
  resultTypeForAnswer,
  resultTypeLabel,
  runForTask,
  supportsOverallLeaderboard,
  variantType
} from "./benchmark-data.js";

test("reselected windows cannot relabel questions from an earlier source", () => {
  const metadata = {element: "New exon", source_record_sha256: "new-source"};
  const document = {by_task_family: {opensplice_snv: {E01: metadata}}};
  const question = {
    metadata: {task_family: "opensplice_snv"},
    provenance: {source_record_id: "E01", source_record_sha256: "new-source"}
  };
  assert.equal(questionDisplayMetadata(question, document), metadata);
  question.provenance.source_record_sha256 = "old-source";
  assert.equal(questionDisplayMetadata(question, document), null);
  delete question.provenance.source_record_sha256;
  delete metadata.source_record_sha256;
  assert.equal(questionDisplayMetadata(question, document), null);
});

test("display scores use a fixed zero-to-one percentage domain", () => {
  assert.equal(displayScore(-0.22), 0);
  assert.equal(displayScore(0.42), 0.42);
  assert.equal(displayScore(1.2), 1);
  assert.equal(displayScore(null), null);
  assert.equal(displayScore(Number.NaN), null);
});

test("ranking outcome metrics support current and legacy compact indexes", () => {
  assert.deepEqual(
    rankingOutcomeMetrics({value: -0.2, spearman_rho: -0.2, pearson_r: 0.4}),
    {spearman_rho: -0.2, pearson_r: 0.4}
  );
  assert.deepEqual(
    rankingOutcomeMetrics({value: 0.3, valid: true}),
    {spearman_rho: 0.3, pearson_r: null}
  );
  assert.deepEqual(
    rankingOutcomeMetrics({value: null, spearman_rho: null, pearson_r: null}),
    {spearman_rho: null, pearson_r: null}
  );
});

test("assay dates are compared with an explicit model knowledge cutoff", () => {
  assert.equal(
    assayCutoffRelation({date: "2024-01-01"}, "2026-02-16"),
    "Before cutoff"
  );
  assert.equal(
    assayCutoffRelation({date: "2026-02-16"}, "2026-02-16"),
    "Before cutoff"
  );
  assert.equal(
    assayCutoffRelation({date: "2026-02-17"}, "2026-02-16"),
    "After cutoff"
  );
  assert.equal(assayCutoffRelation({date: "2026-04-30"}, "2026-05"), "Before cutoff");
  assert.equal(assayCutoffRelation({date: "2026-05-31"}, "2026-05"), "Unknown");
  assert.equal(
    assayCutoffRelation({date: "2026-06-01"}, "2026-05"),
    "After cutoff"
  );
  assert.equal(assayCutoffRelation({date: "2026-02-17"}, null), "Unknown");
  assert.equal(assayCutoffRelation(null, "2026-02-16"), "Unknown");
  assert.equal(assayCutoffRelation({date: "2026-02-17"}, "2026-13"), "Unknown");
});

function run({
  accuracy = 0.5,
  complete = true,
  completedAt = "2026-08-31T00:00:00Z",
  configurationKey = `cfg-${"0".repeat(64)}`,
  effort = "medium",
  evaluationProfile = "synthetic_effect:mc-effect-v1@1.0",
  family = "Test family",
  knowledgeCutoff = "2024-01-01",
  modelId = "test/model",
  provider = "Test provider",
  pearson = null,
  releaseDate = "2026-07-09",
  retryCount = undefined,
  runId = "test-run",
  spearman = null,
  taskType = null,
  tokens = 1200,
  cost = 0.25,
  validOutputRate = null
} = {}) {
  const metrics = taskType === "ranking"
    ? {
        format_failures: validOutputRate === 1 ? 0 : 1,
        mean_pearson_r: pearson,
        mean_spearman_rho: spearman,
        total_tokens: tokens,
        total_cost_usd: cost,
        valid_output_rate: validOutputRate
      }
    : {
        accuracy,
        format_failures: 0,
        total_tokens: tokens,
        total_cost_usd: cost
      };
  const value = {
    answer_prefix: `answers/${runId}/`,
    completed_at: completedAt,
    configuration_key: configurationKey,
    coverage: {complete},
    generation_parameters: {reasoning: {effort}},
    evaluation_profile: evaluationProfile,
    metrics,
    model: {
      model_id: modelId,
      family,
      knowledge_cutoff: knowledgeCutoff,
      release_date: releaseDate,
      upstream_provider: provider
    },
    outcome_index_path: `outcomes/${runId}.json.gz`,
    question_set_sha256: "0".repeat(64),
    question_set_size: 1,
    run_id: runId
  };
  if (taskType) value.task_type = taskType;
  if (retryCount !== undefined) value.retry_count = retryCount;
  return value;
}

test("leaderboard keeps the latest complete run per model configuration", () => {
  const rows = leaderboardRows([
    run({accuracy: 0.2, completedAt: "2026-08-01T00:00:00Z", runId: "old"}),
    run({accuracy: 0.8, completedAt: "2026-08-02T00:00:00Z", runId: "new"}),
    run({complete: false, configurationKey: `cfg-${"1".repeat(64)}`, runId: "bad"})
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].run.run_id, "new");
  assert.equal(rows[0].accuracy, 0.8);
  assert.equal(rows[0].model_cell.model, "model (medium)");
  assert.equal(rows[0].family, "Test family");
  assert.equal(rows[0].release_date, "2026-07-09");
  assert.equal(rows[0].knowledge_cutoff, "2024-01-01");
  assert.equal(rows[0].tokens, 1200);
  assert.equal(rows[0].cost, 0.25);
});

test("execution summaries aggregate counts across unequal task sizes and preserve maxima", () => {
  const first = run({taskType: "ranking", spearman: 0.4});
  const second = run({taskType: "ranking", spearman: 0.5});
  Object.assign(first, {question_set_size: 2, generation_parameters: {max_tokens: 128000}});
  Object.assign(second, {question_set_size: 8, generation_parameters: {max_tokens: 262144}});
  Object.assign(first.metrics, {valid_outputs: 1, truncated_outputs: 1,
    total_output_tokens: 140000, max_output_tokens_used: 128000});
  Object.assign(second.metrics, {valid_outputs: 8, truncated_outputs: 0,
    total_output_tokens: 80000, max_output_tokens_used: 20000});
  const row = {runs: [first, second], model_cell: {model: "Test"}, tokens: 221000, cost: 0.4};
  const summary = executionSummaryForRow(row);
  assert.equal(summary.questions, 10);
  assert.equal(summary.valid_answers, 9);
  assert.equal(summary.valid_rate, 0.9);
  assert.equal(summary.truncation_rate, 0.1);
  assert.equal(summary.output_tokens, 220000);
  assert.equal(summary.max_output_tokens, 128000);
  assert.deepEqual(summary.output_limits, [128000, 262144]);
  assert.equal(summary.total_tokens, 221000);
  assert.equal(summary.cost, 0.4);

  const single = executionSummaryForRow({...row, runs: undefined, run: first});
  assert.equal(single.questions, 2);
  assert.equal(single.valid_rate, 0.5);
  assert.equal(single.truncation_rate, 0.5);
});

test("execution summaries keep unavailable or partially reported usage unknown", () => {
  const first = run({taskType: "ranking"});
  const second = run({taskType: "ranking"});
  Object.assign(first.metrics, {valid_outputs: 1, total_output_tokens: 0,
    max_output_tokens_used: 0, truncated_outputs: 0});
  const row = {runs: [first, second], model_cell: {model: "Test"}, tokens: null, cost: null};
  const summary = executionSummaryForRow(row);
  assert.equal(summary.output_tokens, null);
  assert.equal(summary.max_output_tokens, null);
  assert.equal(summary.truncation_rate, null);
  assert.equal(summary.output_limits, null);
  assert.equal(summary.cost, null);
  const knownZero = executionSummaryForRow({...row, runs: undefined, run: first});
  assert.equal(knownZero.output_tokens, 0);
  assert.equal(knownZero.max_output_tokens, 0);
  assert.equal(knownZero.truncation_rate, 0);
});

test("valid multiple-choice answers include incorrect answers but exclude refusals", () => {
  const classified = run();
  classified.question_set_size = 4;
  classified.metrics.result_counts = {correct: 1, incorrect: 1, refusal: 1, format_error: 1};
  const summary = executionSummaryForRow(leaderboardRows([classified])[0]);
  assert.equal(summary.valid_answers, 2);
  assert.equal(summary.valid_rate, 0.5);
});

test("new comparison models have human-readable labels", () => {
  assert.match(
    formatRunLabel(run({modelId: "anthropic/claude-fable-5.1"})),
    /^Claude Fable 5\.1 \(medium\)/
  );
  assert.match(
    formatRunLabel(run({modelId: "anthropic/claude-opus-5"})),
    /^Claude Opus 5 \(medium\)/
  );
  assert.match(
    formatRunLabel(run({modelId: "deepseek/deepseek-v4-flash-0731"})),
    /^DeepSeek V4 Flash 0731 \(medium\)/
  );
});

test("overall leaderboard macro-averages complete task profiles", () => {
  const leaderboard = {
    aggregation_method: "task_score_macro_average_v1",
    evaluation_profiles: [
      {
        task_family: "synthetic_alpha",
        evaluation_profile: "synthetic_alpha:synthetic_alpha-snv-v1@1.0"
      },
      {
        task_family: "synthetic_beta",
        evaluation_profile: "synthetic_beta:synthetic-beta-v1@1.2"
      }
    ]
  };
  const rows = overallLeaderboardRows([
    run({
      accuracy: 0.75,
      configurationKey: `cfg-${"1".repeat(64)}`,
      cost: 0.5,
      evaluationProfile: "synthetic_alpha:synthetic_alpha-snv-v1@1.0",
      runId: "medium-synthetic_alpha",
      tokens: 300
    }),
    run({
      accuracy: 0.25,
      configurationKey: `cfg-${"2".repeat(64)}`,
      cost: 0.25,
      evaluationProfile: "synthetic_beta:synthetic-beta-v1@1.2",
      provider: "Another routed provider",
      runId: "medium-consequence",
      tokens: 200
    }),
    run({
      accuracy: 0.9,
      configurationKey: `cfg-${"3".repeat(64)}`,
      effort: "low",
      evaluationProfile: "synthetic_beta:synthetic-beta-v1@1.2",
      runId: "low-consequence-only"
    })
  ], leaderboard);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].accuracy, 0.5);
  assert.equal(rows[0].tokens, 500);
  assert.equal(rows[0].cost, 0.75);
  assert.equal(rows[0].model_cell.provider, "OpenRouter auto-routing");
  assert.deepEqual(
    rows[0].task_scores.map((task) => [task.task_family, task.accuracy]),
    [["synthetic_alpha", 0.75], ["synthetic_beta", 0.25]]
  );
  assert.deepEqual(rows[0].runs.map((candidate) => candidate.run_id), [
    "medium-synthetic_alpha",
    "medium-consequence"
  ]);
});

test("overall leaderboard macro-averages task Score across different metrics", () => {
  const leaderboard = {
    aggregation_method: "task_score_macro_average_v1",
    evaluation_profiles: [
      {
        task_family: "synthetic_effect",
        evaluation_profile: "synthetic_effect:mc-effect-v1@1.0",
        primary_metric: "exact_match",
        task_type: "multiple_choice"
      },
      {
        task_family: "synthetic_ranking",
        evaluation_profile: "synthetic_ranking:ranking-v1@1.0",
        primary_metric: "spearman",
        task_type: "ranking"
      }
    ]
  };
  const rows = overallLeaderboardRows([
    run({
      accuracy: 0.8,
      evaluationProfile: "synthetic_effect:mc-effect-v1@1.0",
      runId: "mixed-model-classification"
    }),
    run({
      accuracy: null,
      configurationKey: `cfg-${"1".repeat(64)}`,
      evaluationProfile: "synthetic_ranking:ranking-v1@1.0",
      runId: "mixed-model-ranking",
      spearman: -0.2,
      taskType: "ranking",
      validOutputRate: 1
    })
  ], leaderboard);

  assert.equal(rows.length, 1);
  assert.ok(Math.abs(rows[0].score - 0.3) < Number.EPSILON);
  assert.equal(rows[0].accuracy, null);
  assert.equal(rows[0].primary_metric, "task_macro_average");
  assert.deepEqual(
    rows[0].task_scores.map((task) => [task.task_family, task.score, task.primary_metric]),
    [
      ["synthetic_effect", 0.8, "exact_match"],
      ["synthetic_ranking", -0.2, "spearman"]
    ]
  );
});

test("overall leaderboard rejects unknown aggregation metadata", () => {
  assert.deepEqual(overallLeaderboardRows([], null), []);
  assert.deepEqual(overallLeaderboardRows([], {
    aggregation_method: "question_micro_average_v0",
    evaluation_profiles: []
  }), []);
});

test("overall leaderboard requires the task Score macro-average contract", () => {
  assert.equal(supportsOverallLeaderboard({
    aggregation_method: "classification_task_macro_average_v0",
    evaluation_profiles: [
      {task_family: "satmut_mpra", task_type: "ranking"},
      {task_family: "sge", task_type: "ranking"}
    ]
  }), false);
  assert.equal(supportsOverallLeaderboard({
    aggregation_method: "classification_task_macro_average_v0",
    evaluation_profiles: [{task_family: "synthetic", task_type: "multiple_choice"}]
  }), false);
  assert.equal(supportsOverallLeaderboard({
    aggregation_method: "task_macro_average_v0",
    evaluation_profiles: [{task_family: "synthetic", task_type: "multiple_choice"}]
  }), false);
  assert.equal(supportsOverallLeaderboard({
    aggregation_method: "task_score_macro_average_v1",
    evaluation_profiles: [{task_family: "synthetic", task_type: "multiple_choice"}]
  }), true);
});

test("leaderboard scope switches score, tokens, and cost to one task", () => {
  const leaderboard = {
    aggregation_method: "task_score_macro_average_v1",
    evaluation_profiles: [
      {task_family: "synthetic_alpha", evaluation_profile: "synthetic_alpha:synthetic_alpha-snv-v1@1.0"},
      {
        task_family: "synthetic_beta",
        evaluation_profile: "synthetic_beta:synthetic-beta-v1@1.2"
      }
    ]
  };
  const runs = [
    run({
      accuracy: 0.8,
      cost: 0.6,
      evaluationProfile: "synthetic_alpha:synthetic_alpha-snv-v1@1.0",
      runId: "model-synthetic_alpha",
      retryCount: 1,
      tokens: 600
    }),
    run({
      accuracy: 0.2,
      configurationKey: `cfg-${"1".repeat(64)}`,
      cost: 0.4,
      evaluationProfile: "synthetic_beta:synthetic-beta-v1@1.2",
      runId: "model-consequence",
      tokens: 400
    })
  ];

  const allTasks = leaderboardRowsForScope(runs, leaderboard);
  assert.equal(allTasks.length, 1);
  assert.equal(allTasks[0].accuracy, 0.5);
  assert.equal(allTasks[0].tokens, 1000);
  assert.equal(allTasks[0].cost, 1);
  assert.equal(allTasks[0].retry_count, 1);

  const consequence = leaderboardRowsForScope(
    runs,
    leaderboard,
    "synthetic_beta"
  );
  assert.equal(consequence.length, 1);
  assert.equal(consequence[0].accuracy, 0.2);
  assert.equal(consequence[0].tokens, 400);
  assert.equal(consequence[0].cost, 0.4);
  assert.equal(consequence[0].retry_count, 0);
  assert.equal(leaderboardRowsForScope(runs, leaderboard, "synthetic_alpha")[0].retry_count, 1);
  assert.equal(consequence[0].run.run_id, "model-consequence");
  assert.deepEqual(leaderboardRowsForScope(runs, leaderboard, "unknown"), []);
});

test("selective retry policies group complete tasks without mixing baseline runs", () => {
  const leaderboard = {
    aggregation_method: "task_score_macro_average_v1",
    evaluation_profiles: [
      {task_family: "alpha", evaluation_profile: "alpha:v1"},
      {task_family: "beta", evaluation_profile: "beta:v1"}
    ]
  };
  const baseline = ["alpha", "beta"].map((family, index) => ({
    ...run({
      runId: family, evaluationProfile: `${family}:v1`, configurationKey: `base-${index}`,
      accuracy: 0.2, modelId: "deepseek/deepseek-v4.1-flash"
    }),
    generation_parameters: {max_tokens: 128000, reasoning: {effort: "low"}}
  }));
  const retried = baseline.map((value, index) => ({
    ...structuredClone(value), run_id: `${value.run_id}-retry`,
    configuration_key: `retry-${index}`,
    retry_policy: {
      kind: "invalid_truncation_once", initial_max_tokens: 128000, retry_max_tokens: 262144
    },
    ...(index === 0 ? {retry_count: 1} : {})
  }));
  const rows = overallLeaderboardRows([...baseline, ...retried], leaderboard);
  assert.equal(rows.length, 2);
  const retryRow = rows.find((row) => row.retry_policy);
  assert.equal(retryRow.retry_count, 1);
  assert.equal(retryRow.runs.length, 2);
  assert.equal(retryRow.model_cell.model, "DeepSeek V4.1 Flash (low) · selective retries");
  const summary = executionSummaryForRow(retryRow);
  assert.equal(summary.retries, 1);
  assert.deepEqual(summary.output_limits, [128000, 262144]);
  assert.deepEqual(executionSummaryForRow(leaderboardRows([retried[1]])[0]).output_limits,
    [128000]);
  assert.equal(overallLeaderboardRows([baseline[0], retried[1]], leaderboard).length, 0);
});

test("task ordering keeps benchmark ranking tasks before unknown families", () => {
  assert.deepEqual(
    orderTaskFamilies([
      "sge",
      "satmut_mpra",
      "opensplice_snv",
      "synthetic_alpha",
      "future_task",
      "synthetic_beta"
    ]),
    [
      "sge",
      "satmut_mpra",
      "opensplice_snv",
      "future_task",
      "synthetic_alpha",
      "synthetic_beta"
    ]
  );
});

test("ranking scope uses Spearman and exposes ranking diagnostics", () => {
  const leaderboard = {
    aggregation_method: "classification_task_macro_average_v0",
    evaluation_profiles: [{
      task_family: "satmut_mpra",
      evaluation_profile: "satmut_mpra:satmut-mpra-ranking-v1@1.0",
      primary_metric: "spearman",
      task_type: "ranking"
    }]
  };
  const rows = leaderboardRowsForScope([
    run({
      accuracy: null,
      evaluationProfile: "satmut_mpra:satmut-mpra-ranking-v1@1.0",
      pearson: 0.41,
      runId: "ranking-run",
      spearman: -0.22,
      taskType: "ranking",
      validOutputRate: 0.9375
    })
  ], leaderboard, "satmut_mpra");

  assert.equal(rows.length, 1);
  assert.equal(rows[0].score, -0.22);
  assert.equal(rows[0].pearson, 0.41);
  assert.equal(rows[0].valid_output_rate, 0.9375);
  assert.equal(rows[0].primary_metric, "spearman");
});

test("leaderboard metric selection recomputes task and overall ranking scores", () => {
  const leaderboard = {
    aggregation_method: "task_score_macro_average_v1",
    evaluation_profiles: [
      {
        task_family: "synthetic_alpha",
        evaluation_profile: "synthetic_alpha:ranking-v1@1.0",
        primary_metric: "spearman",
        task_type: "ranking"
      },
      {
        task_family: "synthetic_beta",
        evaluation_profile: "synthetic_beta:ranking-v1@1.0",
        primary_metric: "spearman",
        task_type: "ranking"
      }
    ]
  };
  const runs = [
    run({
      accuracy: null,
      configurationKey: `cfg-${"1".repeat(64)}`,
      evaluationProfile: "synthetic_alpha:ranking-v1@1.0",
      pearson: 0.8,
      runId: "alpha",
      spearman: 0.2,
      taskType: "ranking",
      validOutputRate: 1
    }),
    run({
      accuracy: null,
      configurationKey: `cfg-${"2".repeat(64)}`,
      evaluationProfile: "synthetic_beta:ranking-v1@1.0",
      pearson: 0.4,
      runId: "beta",
      spearman: 0.6,
      taskType: "ranking",
      validOutputRate: 1
    })
  ];

  const spearmanOverall = leaderboardRowsForScope(runs, leaderboard, null, "spearman");
  const pearsonOverall = leaderboardRowsForScope(runs, leaderboard, null, "pearson");
  assert.equal(spearmanOverall.length, 1);
  assert.equal(pearsonOverall.length, 1);
  assert.equal(spearmanOverall[0].score, 0.4);
  assert.equal(pearsonOverall[0].score, 0.6000000000000001);
  assert.equal(pearsonOverall[0].primary_metric, "task_macro_average");
  assert.deepEqual(
    pearsonOverall[0].task_scores.map((task) => task.primary_metric),
    ["pearson", "pearson"]
  );

  const pearsonTask = leaderboardRowsForScope(
    runs,
    leaderboard,
    "synthetic_alpha",
    "pearson"
  );
  assert.equal(pearsonTask[0].score, 0.8);
  assert.equal(pearsonTask[0].primary_metric, "pearson");
});

test("unsupported classification aggregation does not affect model selection", () => {
  const leaderboard = {
    aggregation_method: "classification_task_macro_average_v0",
    evaluation_profiles: [
      {
        task_family: "synthetic_alpha",
        evaluation_profile: "synthetic_alpha:synthetic_alpha-snv-v1@1.0",
        primary_metric: "exact_match",
        task_type: "multiple_choice"
      },
      {
        task_family: "satmut_mpra",
        evaluation_profile: "satmut_mpra:satmut-mpra-ranking-v1@1.0",
        primary_metric: "spearman",
        task_type: "ranking"
      }
    ]
  };
  const runs = [
    run({
      accuracy: 0.75,
      evaluationProfile: "synthetic_alpha:synthetic_alpha-snv-v1@1.0",
      runId: "model-synthetic_alpha"
    }),
    run({
      configurationKey: `cfg-${"1".repeat(64)}`,
      evaluationProfile: "satmut_mpra:satmut-mpra-ranking-v1@1.0",
      pearson: 0.3,
      runId: "model-ranking",
      spearman: 0.4,
      taskType: "ranking",
      validOutputRate: 1
    })
  ];

  const overall = overallLeaderboardRows(runs, leaderboard);
  assert.deepEqual(overall, []);

  const selections = modelSelectionRows(runs, leaderboard);
  assert.equal(selections.length, 1);
  assert.equal(runForTask(selections[0], "satmut_mpra").run_id, "model-ranking");
});

test("model selection includes a configuration with only a complete ranking run", () => {
  const leaderboard = {
    aggregation_method: "classification_task_macro_average_v0",
    evaluation_profiles: [
      {
        task_family: "synthetic_alpha",
        evaluation_profile: "synthetic_alpha:synthetic_alpha-snv-v1@1.0",
        primary_metric: "exact_match",
        task_type: "multiple_choice"
      },
      {
        task_family: "satmut_mpra",
        evaluation_profile: "satmut_mpra:satmut-mpra-ranking-v1@1.0",
        primary_metric: "spearman",
        task_type: "ranking"
      }
    ]
  };
  const ranking = run({
    evaluationProfile: "satmut_mpra:satmut-mpra-ranking-v1@1.0",
    pearson: 0.3,
    runId: "ranking-only",
    spearman: 0.4,
    taskType: "ranking",
    validOutputRate: 1
  });

  const selections = modelSelectionRows([ranking], leaderboard);

  assert.equal(selections.length, 1);
  assert.equal(selections[0].accuracy, null);
  assert.equal(selections[0].score, 0.4);
  assert.equal(runForTask(selections[0], "satmut_mpra").run_id, "ranking-only");
});

test("model selection has one best-first row with a task run for each model", () => {
  const leaderboard = {
    aggregation_method: "task_score_macro_average_v1",
    evaluation_profiles: [
      {task_family: "synthetic_alpha", evaluation_profile: "synthetic_alpha:synthetic_alpha-snv-v1@1.0"},
      {
        task_family: "synthetic_beta",
        evaluation_profile: "synthetic_beta:synthetic-beta-v1@1.2"
      }
    ]
  };
  const specifications = [
    ["gpt-5.6-luna", "synthetic_alpha", 0.55],
    ["gpt-5.6-luna", "vep", 0.27],
    ["gpt-5.6-sol", "synthetic_alpha", 0.55],
    ["gpt-5.6-sol", "vep", 0.41]
  ];
  const rows = modelSelectionRows(specifications.map(([model, task, accuracy], index) => run({
    accuracy,
    configurationKey: `cfg-${String(index).repeat(64)}`,
    evaluationProfile: task === "synthetic_alpha"
      ? "synthetic_alpha:synthetic_alpha-snv-v1@1.0"
      : "synthetic_beta:synthetic-beta-v1@1.2",
    modelId: `openai/${model}`,
    runId: `${model}-${task}`
  })), leaderboard);

  assert.equal(rows.length, 2);
  assert.equal(rows[0].model_cell.model, "GPT 5.6 Sol (medium)");
  assert.equal(rows[0].accuracy, 0.48);
  assert.equal(runForTask(rows[0], "synthetic_alpha").run_id, "gpt-5.6-sol-synthetic_alpha");
  assert.equal(
    runForTask(rows[0], "synthetic_beta").run_id,
    "gpt-5.6-sol-vep"
  );
});

test("question explorer puts satMutMPRA before unknown task families", () => {
  const questions = [
    {question_id: "synthetic_alpha-2", metadata: {task_family: "synthetic_alpha"}},
    {question_id: "satmut-1", metadata: {task_family: "satmut_mpra"}},
    {question_id: "future-1", metadata: {task_family: "future_task"}},
    {
      question_id: "consequence-2",
      metadata: {task_family: "synthetic_beta"}
    },
    {question_id: "synthetic_alpha-1", metadata: {task_family: "synthetic_alpha"}},
    {
      question_id: "consequence-1",
      metadata: {task_family: "synthetic_beta"}
    }
  ];

  assert.deepEqual(
    orderQuestionsForExplorer(questions).map((question) => question.question_id),
    ["satmut-1", "future-1", "synthetic_alpha-1", "synthetic_alpha-2", "consequence-1", "consequence-2"]
  );
  assert.equal(questions[0].question_id, "synthetic_alpha-2");
});

test("question explorer preserves the current visible question across control changes", () => {
  const entries = [
    {question_id: "question-1"},
    {question_id: "question-2"},
    {question_id: "question-3"}
  ];
  const knownQuestionIds = new Set(entries.map((entry) => entry.question_id));

  assert.equal(
    defaultQuestionForExplorer(entries, {
      currentQuestionId: "question-2",
      knownQuestionIds,
      evaluatedQuestionIds: new Set(["question-1"]),
      preferEvaluated: true
    }).question_id,
    "question-2"
  );
  assert.equal(
    defaultQuestionForExplorer(entries.slice(0, 1), {
      currentQuestionId: "question-2",
      knownQuestionIds
    }).question_id,
    "question-1"
  );
  assert.equal(
    defaultQuestionForExplorer(entries, {
      currentQuestionId: "missing-question",
      knownQuestionIds
    }),
    null
  );
});

test("question explorer exposes only complete runs", () => {
  const runs = groupCurrentRuns([
    run({runId: "zeta"}),
    run({complete: false, runId: "ignored"}),
    run({runId: "alpha"})
  ]);
  assert.deepEqual(runs.map((candidate) => candidate.run_id), ["alpha", "zeta"]);
});

test("prediction comparison pairs reference effects with parsed ranking values", () => {
  const question = {
    task_type: "ranking",
    candidates: [
      {candidate_id: "V01", reference_score: -1.5},
      {candidate_id: "V02", reference_score: 0.25},
      {candidate_id: "V03", reference_score: 2}
    ]
  };
  const result = {
    scoring: {
      metric: "rank_correlation",
      parsed_answer: {V01: -0.9, V02: 0.5, V03: 1.75}
    }
  };

  assert.deepEqual(predictionComparisonRows(question, result).map(
    ({candidate_id, measured, predicted}) => ({candidate_id, measured, predicted})
  ), [
    {candidate_id: "V01", measured: -1.5, predicted: -0.9},
    {candidate_id: "V02", measured: 0.25, predicted: 0.5},
    {candidate_id: "V03", measured: 2, predicted: 1.75}
  ]);
  assert.deepEqual(predictionComparisonRows(question, {
    scoring: {metric: "rank_correlation", parsed_answer: null}
  }), []);
  assert.deepEqual(predictionComparisonRows(
    {...question, task_type: "multiple_choice"},
    result
  ), []);
});

test("variant shapes distinguish indels and complete multibase substitutions", () => {
  assert.equal(variantType("A", "T"), "SNV");
  assert.equal(variantType("AC", "AT"), "SNV");
  assert.equal(variantType("AC", "A"), "Indel");
  assert.equal(variantType("A", "ACT"), "Indel");
  assert.equal(variantType("AC", "GT"), "Multibase substitution");
  assert.equal(variantType("ACT", "AGT"), "SNV");
  assert.equal(variantType(undefined, undefined), "Unknown");
});

test("variant annotations join by candidate ID and exact source digest", () => {
  const question = {
    task_type: "ranking",
    provenance: {source_record_sha256: "current-source"},
    candidates: [
      {candidate_id: "V01", ref: "A", alt: "C", reference_score: 1},
      {candidate_id: "V02", ref: "ACT", alt: "A", reference_score: -2}
    ]
  };
  const result = {scoring: {metric: "rank_correlation", parsed_answer: {V02: -1, V01: 2}}};
  const genomic = {assembly: "GRCh38", chrom: "3", pos: 123, ref: "A", alt: "C"};
  const metadata = {
    source_record_sha256: "current-source",
    variants: {
      V02: {most_severe_consequence: "frameshift_variant"},
      V01: {most_severe_consequence: "missense_variant", genomic}
    }
  };
  const rows = predictionComparisonRows(question, result, metadata);
  assert.equal(rows[0].predicted, 2);
  assert.equal(rows[0].consequence, "missense_variant");
  assert.deepEqual(rows[0].genomic, genomic);
  assert.equal(rows[1].variant_type, "Indel");
  assert.equal(rows[1].consequence, "frameshift_variant");
  metadata.source_record_sha256 = "old-source";
  assert.equal(predictionComparisonRows(question, result, metadata)[0].genomic, null);
  assert.equal(predictionComparisonRows(question, result, metadata)[0].consequence, "Not annotated");
});

test("result types use stored values and preserve legacy fallbacks", () => {
  assert.equal(
    resultTypeForAnswer({scoring: {result_type: "refusal", parse_error: "missing"}}),
    "refusal"
  );
  assert.equal(
    resultTypeForAnswer({scoring: {correct: false, parse_error: "missing"}}),
    "format_error"
  );
  assert.equal(
    resultTypeForAnswer({
      response: {status: "completed", finish_reason: "content_filter"},
      scoring: {correct: true, parse_error: null}
    }),
    "refusal"
  );
  assert.equal(
    resultTypeForAnswer({
      response: {status: "completed", finish_reason: "length"},
      scoring: {correct: false, parse_error: "missing"}
    }),
    "token_limit"
  );
  assert.equal(
    resultTypeForAnswer({
      response: {status: "api_error", finish_reason: null},
      scoring: {correct: null, parse_error: null, result_type: null}
    }),
    null
  );
  assert.equal(resultTypeLabel("token_limit", false), "Token limit");
  assert.equal(resultTypeLabel(undefined, true), "Correct");
  assert.equal(resultTypeLabel(undefined, null), "Not scored");
});

test("answer path is one encoded object and rejects mismatched prefixes", () => {
  const candidate = run({runId: "demo-run"});
  assert.equal(
    answerPath(candidate, "task:question-1"),
    "answers/demo-run/task%3Aquestion-1.json.gz"
  );
  candidate.answer_prefix = "answers/another-run/";
  assert.throws(() => answerPath(candidate, "task:question-1"), /does not match/);
});

test("fetchAnswer downloads and decompresses exactly one gzip object", async () => {
  const answer = {question_id: "task:question-1", scoring: {correct: true}};
  const compressed = gzipSync(`${JSON.stringify(answer)}\n`, {mtime: 0});
  const requested = [];
  const fetcher = async (url) => {
    requested.push(url);
    return new Response(compressed, {status: 200});
  };

  const observed = await fetchAnswer(
    "https://example.test/versions/main",
    run({runId: "demo-run"}),
    "task:question-1",
    fetcher
  );

  assert.deepEqual(observed, answer);
  assert.deepEqual(requested, [
    "https://example.test/versions/main/answers/demo-run/task%3Aquestion-1.json.gz"
  ]);
});

test("fetchAnswerIfAvailable skips questions outside the selected run", async () => {
  let requests = 0;
  const fetcher = async () => {
    requests += 1;
    return new Response();
  };
  const outcomeIndex = {
    outcomes: [{question_id: "task:question-1", correct: true}]
  };

  assert.equal(
    await fetchAnswerIfAvailable(
      "https://example.test/versions/main",
      run({runId: "demo-run"}),
      "other-task:question-1",
      outcomeIndex,
      fetcher
    ),
    null
  );
  assert.equal(requests, 0);
});

test("fetchAnswerIfAvailable loads a question covered by the selected run", async () => {
  const answer = {question_id: "task:question-1", scoring: {correct: true}};
  const compressed = gzipSync(`${JSON.stringify(answer)}\n`, {mtime: 0});
  let requests = 0;
  const fetcher = async () => {
    requests += 1;
    return new Response(compressed, {status: 200});
  };

  assert.deepEqual(
    await fetchAnswerIfAvailable(
      "https://example.test/versions/main",
      run({runId: "demo-run"}),
      answer.question_id,
      {outcomes: [{question_id: answer.question_id, correct: true}]},
      fetcher
    ),
    answer
  );
  assert.equal(requests, 1);
});

test("fetchOutcomeIndex downloads the compact results for one run", async () => {
  const candidate = run({runId: "demo-run"});
  const outcomeIndex = {
    schema_version: "1.0",
    run_id: candidate.run_id,
    question_set_sha256: candidate.question_set_sha256,
    question_set_size: candidate.question_set_size,
    outcomes: [{question_id: "task:question-1", correct: true}]
  };
  const compressed = gzipSync(`${JSON.stringify(outcomeIndex)}\n`, {mtime: 0});
  const requested = [];
  const fetcher = async (url) => {
    requested.push(url);
    return new Response(compressed, {status: 200});
  };

  assert.deepEqual(
    await fetchOutcomeIndex("https://example.test/versions/main", candidate, fetcher),
    outcomeIndex
  );
  assert.deepEqual(requested, [
    "https://example.test/versions/main/outcomes/demo-run.json.gz"
  ]);
});

test("outcome index path rejects mismatched run metadata", () => {
  const candidate = run({runId: "demo-run"});
  candidate.outcome_index_path = "outcomes/another-run.json.gz";
  assert.throws(() => outcomeIndexPath(candidate), /does not match/);
});

test("fetchJson rejects non-success responses with the status code", async () => {
  const fetcher = async () => new Response("unavailable", {status: 503});

  await assert.rejects(
    fetchJson("https://example.test/versions/main/runs.json", fetcher),
    /HTTP 503/
  );
});

test("fetchAnswer rejects unsafe identifiers before making a request", async () => {
  let requested = false;
  const fetcher = async () => {
    requested = true;
    return new Response();
  };

  await assert.rejects(
    fetchAnswer(
      "https://example.test/versions/main",
      run({runId: "unsafe/run"}),
      "task:question-1",
      fetcher
    ),
    /Unsafe run or question ID/
  );
  assert.equal(requested, false);
});
