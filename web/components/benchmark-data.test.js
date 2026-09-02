import assert from "node:assert/strict";
import {gzipSync} from "node:zlib";
import test from "node:test";

import {
  answerPath,
  defaultQuestionForExplorer,
  fetchAnswer,
  fetchAnswerIfAvailable,
  fetchJson,
  fetchOutcomeIndex,
  groupCurrentRuns,
  leaderboardRows,
  leaderboardRowsForScope,
  modelSelectionRows,
  orderQuestionsForExplorer,
  orderTaskFamilies,
  overallLeaderboardRows,
  outcomeIndexPath,
  runForTask
} from "./benchmark-data.js";

function run({
  accuracy = 0.5,
  complete = true,
  completedAt = "2026-08-31T00:00:00Z",
  configurationKey = `cfg-${"0".repeat(64)}`,
  effort = "medium",
  evaluationProfile = "synthetic_effect:mc-effect-v1@1.0",
  family = "Test family",
  modelId = "test/model",
  releaseDate = "2026-07-09",
  runId = "test-run",
  tokens = 1200,
  cost = 0.25
} = {}) {
  return {
    answer_prefix: `answers/${runId}/`,
    completed_at: completedAt,
    configuration_key: configurationKey,
    coverage: {complete},
    generation_parameters: {reasoning: {effort}},
    evaluation_profile: evaluationProfile,
    metrics: {
      accuracy,
      format_failures: 0,
      total_tokens: tokens,
      total_cost_usd: cost
    },
    model: {
      model_id: modelId,
      family,
      release_date: releaseDate,
      upstream_provider: "Test provider"
    },
    outcome_index_path: `outcomes/${runId}.json.gz`,
    question_set_sha256: "0".repeat(64),
    question_set_size: 1,
    run_id: runId
  };
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
  assert.equal(rows[0].tokens, 1200);
  assert.equal(rows[0].cost, 0.25);
});

test("overall leaderboard macro-averages complete task profiles", () => {
  const leaderboard = {
    aggregation_method: "task_macro_average_v0",
    evaluation_profiles: [
      {
        task_family: "clinvar",
        evaluation_profile: "clinvar:clinvar-snv-v1@1.0"
      },
      {
        task_family: "vep_most_severe_consequence",
        evaluation_profile: "vep_most_severe_consequence:vep-most-severe-v1@1.2"
      }
    ]
  };
  const rows = overallLeaderboardRows([
    run({
      accuracy: 0.75,
      configurationKey: `cfg-${"1".repeat(64)}`,
      cost: 0.5,
      evaluationProfile: "clinvar:clinvar-snv-v1@1.0",
      runId: "medium-clinvar",
      tokens: 300
    }),
    run({
      accuracy: 0.25,
      configurationKey: `cfg-${"2".repeat(64)}`,
      cost: 0.25,
      evaluationProfile: "vep_most_severe_consequence:vep-most-severe-v1@1.2",
      runId: "medium-consequence",
      tokens: 200
    }),
    run({
      accuracy: 0.9,
      configurationKey: `cfg-${"3".repeat(64)}`,
      effort: "low",
      evaluationProfile: "vep_most_severe_consequence:vep-most-severe-v1@1.2",
      runId: "low-consequence-only"
    })
  ], leaderboard);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].accuracy, 0.5);
  assert.equal(rows[0].tokens, 500);
  assert.equal(rows[0].cost, 0.75);
  assert.deepEqual(
    rows[0].task_scores.map((task) => [task.task_family, task.accuracy]),
    [["clinvar", 0.75], ["vep_most_severe_consequence", 0.25]]
  );
  assert.deepEqual(rows[0].runs.map((candidate) => candidate.run_id), [
    "medium-clinvar",
    "medium-consequence"
  ]);
});

test("overall leaderboard rejects unknown aggregation metadata", () => {
  assert.deepEqual(overallLeaderboardRows([], null), []);
  assert.deepEqual(overallLeaderboardRows([], {
    aggregation_method: "question_micro_average_v0",
    evaluation_profiles: []
  }), []);
});

test("leaderboard scope switches score, tokens, and cost to one task", () => {
  const leaderboard = {
    aggregation_method: "task_macro_average_v0",
    evaluation_profiles: [
      {task_family: "clinvar", evaluation_profile: "clinvar:clinvar-snv-v1@1.0"},
      {
        task_family: "vep_most_severe_consequence",
        evaluation_profile: "vep_most_severe_consequence:vep-most-severe-v1@1.2"
      }
    ]
  };
  const runs = [
    run({
      accuracy: 0.8,
      cost: 0.6,
      evaluationProfile: "clinvar:clinvar-snv-v1@1.0",
      runId: "model-clinvar",
      tokens: 600
    }),
    run({
      accuracy: 0.2,
      configurationKey: `cfg-${"1".repeat(64)}`,
      cost: 0.4,
      evaluationProfile: "vep_most_severe_consequence:vep-most-severe-v1@1.2",
      runId: "model-consequence",
      tokens: 400
    })
  ];

  const allTasks = leaderboardRowsForScope(runs, leaderboard);
  assert.equal(allTasks.length, 1);
  assert.equal(allTasks[0].accuracy, 0.5);
  assert.equal(allTasks[0].tokens, 1000);
  assert.equal(allTasks[0].cost, 1);

  const consequence = leaderboardRowsForScope(
    runs,
    leaderboard,
    "vep_most_severe_consequence"
  );
  assert.equal(consequence.length, 1);
  assert.equal(consequence[0].accuracy, 0.2);
  assert.equal(consequence[0].tokens, 400);
  assert.equal(consequence[0].cost, 0.4);
  assert.equal(consequence[0].run.run_id, "model-consequence");
  assert.deepEqual(leaderboardRowsForScope(runs, leaderboard, "unknown"), []);
});

test("task selectors use the benchmark presentation order", () => {
  assert.deepEqual(
    orderTaskFamilies(["clinvar", "future_task", "vep_most_severe_consequence"]),
    ["vep_most_severe_consequence", "clinvar", "future_task"]
  );
});

test("model selection has one best-first row with a task run for each model", () => {
  const leaderboard = {
    aggregation_method: "task_macro_average_v0",
    evaluation_profiles: [
      {task_family: "clinvar", evaluation_profile: "clinvar:clinvar-snv-v1@1.0"},
      {
        task_family: "vep_most_severe_consequence",
        evaluation_profile: "vep_most_severe_consequence:vep-most-severe-v1@1.2"
      }
    ]
  };
  const specifications = [
    ["gpt-5.6-luna", "clinvar", 0.55],
    ["gpt-5.6-luna", "vep", 0.27],
    ["gpt-5.6-sol", "clinvar", 0.55],
    ["gpt-5.6-sol", "vep", 0.41]
  ];
  const rows = modelSelectionRows(specifications.map(([model, task, accuracy], index) => run({
    accuracy,
    configurationKey: `cfg-${String(index).repeat(64)}`,
    evaluationProfile: task === "clinvar"
      ? "clinvar:clinvar-snv-v1@1.0"
      : "vep_most_severe_consequence:vep-most-severe-v1@1.2",
    modelId: `openai/${model}`,
    runId: `${model}-${task}`
  })), leaderboard);

  assert.equal(rows.length, 2);
  assert.equal(rows[0].model_cell.model, "GPT 5.6 Sol (medium)");
  assert.equal(rows[0].accuracy, 0.48);
  assert.equal(runForTask(rows[0], "clinvar").run_id, "gpt-5.6-sol-clinvar");
  assert.equal(
    runForTask(rows[0], "vep_most_severe_consequence").run_id,
    "gpt-5.6-sol-vep"
  );
});

test("question explorer puts consequence classification before ClinVar", () => {
  const questions = [
    {question_id: "clinvar-2", metadata: {task_family: "clinvar"}},
    {question_id: "future-1", metadata: {task_family: "future_task"}},
    {
      question_id: "consequence-2",
      metadata: {task_family: "vep_most_severe_consequence"}
    },
    {question_id: "clinvar-1", metadata: {task_family: "clinvar"}},
    {
      question_id: "consequence-1",
      metadata: {task_family: "vep_most_severe_consequence"}
    }
  ];

  assert.deepEqual(
    orderQuestionsForExplorer(questions).map((question) => question.question_id),
    ["consequence-1", "consequence-2", "clinvar-1", "clinvar-2", "future-1"]
  );
  assert.equal(questions[0].question_id, "clinvar-2");
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
