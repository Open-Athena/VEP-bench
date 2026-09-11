const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const AUTO_ROUTED_PROVIDER = "OpenRouter auto-routing";
const EXPLORER_TASK_ORDER = new Map([
  ["sge", 0],
  ["satmut_mpra", 1],
  ["opensplice_snv", 2]
]);
const RESULT_TYPE_LABELS = Object.freeze({
  correct: "Correct",
  incorrect: "Incorrect",
  refusal: "Refusal",
  token_limit: "Token limit",
  format_error: "Format error"
});

export function questionDisplayMetadata(question, document) {
  const metadata = document?.by_task_family?.[question.metadata?.task_family]
    ?.[question.provenance?.source_record_id];
  return metadata?.source_record_sha256
    && metadata.source_record_sha256 === question.provenance?.source_record_sha256
    ? metadata
    : null;
}

export function resultTypeForAnswer(result) {
  if (!result) return null;
  if (result.response?.status && result.response.status !== "completed") return null;
  const stored = result.scoring?.result_type;
  if (Object.hasOwn(RESULT_TYPE_LABELS, stored)) return stored;
  if (result.response?.finish_reason === "content_filter") {
    return "refusal";
  }
  if (result.scoring.parse_error !== null && result.response?.finish_reason === "length") {
    return "token_limit";
  }
  if (result.scoring.parse_error !== null) return "format_error";
  return result.scoring.correct ? "correct" : "incorrect";
}

export function resultTypeLabel(resultType, correct = null) {
  if (Object.hasOwn(RESULT_TYPE_LABELS, resultType)) return RESULT_TYPE_LABELS[resultType];
  if (correct === true) return RESULT_TYPE_LABELS.correct;
  if (correct === false) return RESULT_TYPE_LABELS.incorrect;
  return "Not scored";
}

function compareTaskFamilies(left, right) {
  return (EXPLORER_TASK_ORDER.get(left) ?? Number.MAX_SAFE_INTEGER)
    - (EXPLORER_TASK_ORDER.get(right) ?? Number.MAX_SAFE_INTEGER)
    || left.localeCompare(right);
}

export function modelName(modelId, generationParameters) {
  const name = modelId.split("/").at(-1) ?? modelId;
  const displayName = {
    "claude-fable-5.1": "Claude Fable 5.1",
    "claude-opus-5": "Claude Opus 5",
    "deepseek-v4-flash-0731": "DeepSeek V4 Flash 0731",
    "deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
    "gemini-3.8-flash": "Gemini 3.8 Flash",
    "glm-5.3": "GLM 5.3",
    "gpt-5.6-luna": "GPT 5.6 Luna",
    "gpt-5.6-sol": "GPT 5.6 Sol",
    "gpt-6-astra": "GPT 6 Astra",
    "muse-spark-1.3": "Muse Spark 1.3"
  }[name] ?? name;
  const effort = generationParameters?.reasoning?.effort;
  return effort ? `${displayName} (${effort})` : displayName;
}

export function modelFamilyScale(families, colors) {
  const domain = [...new Set(families)].sort();
  return {
    type: "categorical", domain,
    range: domain.map((family) => colors[family] ?? "#767676"),
    label: "Model family"
  };
}

function nonnegativeNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function displayScore(value) {
  const score = finiteNumber(value);
  return score === null ? null : Math.max(0, Math.min(1, score));
}

export function rankingOutcomeMetrics(outcome) {
  return {
    spearman_rho: finiteNumber(outcome?.spearman_rho) ?? finiteNumber(outcome?.value),
    pearson_r: finiteNumber(outcome?.pearson_r)
  };
}

export function assayCutoffRelation(assayFirstIndexed, knowledgeCutoff) {
  const assayDate = typeof assayFirstIndexed === "string"
    ? assayFirstIndexed
    : assayFirstIndexed?.date;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(assayDate ?? "")
    || !/^\d{4}-\d{2}(?:-\d{2})?$/.test(knowledgeCutoff ?? "")) {
    return "Unknown";
  }
  const normalizedCutoff = knowledgeCutoff.length === 7
    ? `${knowledgeCutoff}-01`
    : knowledgeCutoff;
  const parsedAssayDate = new Date(`${assayDate}T00:00:00Z`);
  const parsedCutoff = new Date(`${normalizedCutoff}T00:00:00Z`);
  if (Number.isNaN(parsedAssayDate.valueOf())
    || Number.isNaN(parsedCutoff.valueOf())
    || parsedAssayDate.toISOString().slice(0, 10) !== assayDate
    || parsedCutoff.toISOString().slice(0, 10) !== normalizedCutoff) {
    return "Unknown";
  }
  if (knowledgeCutoff.length === 7) {
    const assayMonth = assayDate.slice(0, 7);
    if (assayMonth === knowledgeCutoff) return "Unknown";
    return assayMonth < knowledgeCutoff ? "Before cutoff" : "After cutoff";
  }
  return assayDate <= knowledgeCutoff ? "Before cutoff" : "After cutoff";
}

function primaryScore(run) {
  return finiteNumber(run.metrics.mean_spearman_rho)
    ?? nonnegativeNumber(run.metrics.accuracy);
}

function leaderboardScore(run, scoreMetric) {
  if (scoreMetric === "spearman") {
    return finiteNumber(run.metrics.mean_spearman_rho);
  }
  if (scoreMetric === "pearson") {
    return finiteNumber(run.metrics.mean_pearson_r);
  }
  return primaryScore(run);
}

export function formatRunLabel(run) {
  const model = modelName(run?.model?.model_id ?? "unknown model", run?.generation_parameters);
  const provider = run?.model?.upstream_provider ?? "provider not reported";
  return `${model}${run?.retry_policy ? " · selective retries" : ""} · ${provider}`;
}

function latestCompleteRuns(runs) {
  const latestByConfiguration = new Map();
  for (const run of runs.filter((candidate) => candidate.coverage.complete)) {
    const previous = latestByConfiguration.get(run.configuration_key);
    if (!previous
      || Date.parse(run.completed_at) > Date.parse(previous.completed_at)
      || (run.completed_at === previous.completed_at && run.run_id > previous.run_id)) {
      latestByConfiguration.set(run.configuration_key, run);
    }
  }
  return [...latestByConfiguration.values()];
}

function rowForRun(run, scoreMetric = null) {
  const family = run.model.family ?? modelName(run.model.model_id);
  return {
    run,
    model_cell: {
      model: modelName(run.model.model_id, run.generation_parameters)
        + (run.retry_policy ? " · selective retries" : ""),
      provider: run.model.upstream_provider ?? "not reported"
    },
    family,
    family_id: family,
    retry_count: run.retry_count ?? 0,
    retry_policy: run.retry_policy ?? null,
    release_date: run.model.release_date ?? null,
    knowledge_cutoff: run.model.knowledge_cutoff ?? null,
    tokens: nonnegativeNumber(run.metrics.total_tokens),
    cost: nonnegativeNumber(run.metrics.total_cost_usd),
    score: leaderboardScore(run, scoreMetric),
    accuracy: nonnegativeNumber(run.metrics.accuracy),
    pearson: finiteNumber(run.metrics.mean_pearson_r),
    valid_output_rate: nonnegativeNumber(run.metrics.valid_output_rate),
    primary_metric: scoreMetric
      ?? (run.task_type === "ranking" ? "spearman" : "exact_match"),
    format_failures: run.metrics.format_failures
  };
}

function sortLeaderboardRows(rows) {
  return rows.sort((a, b) =>
    (b.score ?? -Infinity) - (a.score ?? -Infinity)
    || a.model_cell.model.localeCompare(b.model_cell.model)
    || a.model_cell.provider.localeCompare(b.model_cell.provider)
  );
}

export function leaderboardRows(runs, scoreMetric = null) {
  return sortLeaderboardRows(
    latestCompleteRuns(runs).map((run) => rowForRun(run, scoreMetric))
  );
}

export function highestEffortRows(rows) {
  const efforts = ["none", "minimal", "low", "medium", "high", "xhigh"];
  const selected = new Map();
  for (const row of rows) {
    const runs = row.runs ?? [row.run];
    const run = runs[0];
    const effort = run.generation_parameters?.reasoning?.effort;
    if (effort != null && !efforts.includes(effort)) {
      throw new Error(`Unrecognized effort: ${effort}`);
    }
    const key = JSON.stringify([
      run.model.gateway, run.model.model_id, run.model.model_revision ?? null
    ]);
    const rank = efforts.indexOf(effort);
    const latest = runs.reduce((left, right) =>
      Date.parse(right.completed_at) > Date.parse(left.completed_at)
        || (right.completed_at === left.completed_at && right.run_id > left.run_id)
        ? right : left
    );
    const previous = selected.get(key);
    // Effort and recency determine selection; scores only order the selected rows.
    if (!previous || rank > previous.rank || (rank === previous.rank
      && (Date.parse(latest.completed_at) > Date.parse(previous.latest.completed_at)
        || (latest.completed_at === previous.latest.completed_at
          && latest.run_id > previous.latest.run_id)))) {
      selected.set(key, {row, rank, latest});
    }
  }
  return sortLeaderboardRows([...selected.values()].map(({row}) => row));
}

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalValue(value[key])])
    );
  }
  return value;
}

function overallConfigurationKey(run) {
  return JSON.stringify(canonicalValue({
    model: {
      gateway: run.model.gateway,
      model_id: run.model.model_id,
      model_revision: run.model.model_revision ?? null
    },
    generation_parameters: run.generation_parameters,
    ...(run.retry_policy ? {retry_policy: run.retry_policy} : {})
  }));
}

function sumIfComplete(values) {
  return values.every((value) => value !== null)
    ? values.reduce((total, value) => total + value, 0)
    : null;
}

export function hasExecutionFailures(row) {
  return (row.runs ?? [row.run]).some((run) =>
    run.retry_count > 0
    || run.metrics.format_failures > 0
    || run.metrics.truncated_outputs > 0
    || ["refusal", "token_limit", "format_error"].some(
      (type) => run.metrics.result_counts?.[type] > 0
    )
  );
}

export function executionSummaryForRow(row) {
  const runs = row.runs ?? [row.run];
  const questions = runs.reduce((total, run) => total + run.question_set_size, 0);
  const validAnswers = sumIfComplete(runs.map((run) => {
    if (run.task_type === "ranking") return nonnegativeNumber(run.metrics.valid_outputs);
    const counts = run.metrics.result_counts;
    return counts ? sumIfComplete([
      nonnegativeNumber(counts.correct), nonnegativeNumber(counts.incorrect)
    ]) : null;
  }));
  const truncated = sumIfComplete(
    runs.map((run) => nonnegativeNumber(run.metrics.truncated_outputs))
  );
  const formatFailures = sumIfComplete(
    runs.map((run) => nonnegativeNumber(run.metrics.format_failures))
  );
  const maxima = runs.map((run) => nonnegativeNumber(run.metrics.max_output_tokens_used));
  const limits = runs.flatMap((run) => [
    nonnegativeNumber(
      run.generation_parameters.max_completion_tokens ?? run.generation_parameters.max_tokens
    ),
    ...(run.retry_policy && run.retry_count
      ? [nonnegativeNumber(run.retry_policy.retry_max_tokens)] : [])
  ]);
  return {
    model: row.model_cell.model,
    organization: runs[0].model.model_id.split("/")[0],
    questions,
    retries: row.retry_count ?? 0,
    valid_answers: validAnswers,
    valid_rate: validAnswers !== null && questions ? validAnswers / questions : null,
    format_failures: formatFailures,
    format_failure_rate: formatFailures !== null && questions ? formatFailures / questions : null,
    truncated_outputs: truncated,
    truncation_rate: truncated !== null && questions ? truncated / questions : null,
    output_limits: limits.every((limit) => limit !== null)
      ? [...new Set(limits)].sort((a, b) => a - b) : null,
    output_tokens: sumIfComplete(
      runs.map((run) => nonnegativeNumber(run.metrics.total_output_tokens))
    ),
    max_output_tokens: maxima.every((value) => value !== null) ? Math.max(...maxima) : null,
    total_tokens: row.tokens,
    cost: row.cost
  };
}

function overallProfiles(leaderboard) {
  const profiles = leaderboard?.evaluation_profiles;
  if (!Array.isArray(profiles) || profiles.length === 0) return [];
  return leaderboard.aggregation_method === "task_score_macro_average_v1" ? profiles : [];
}

export function overallLeaderboardRows(runs, leaderboard, scoreMetric = null) {
  const profiles = overallProfiles(leaderboard);
  if (profiles.length === 0) return [];
  const profileKeys = new Set();
  for (const profile of profiles) {
    if (typeof profile?.task_family !== "string"
      || typeof profile?.evaluation_profile !== "string"
      || profileKeys.has(profile.evaluation_profile)) return [];
    profileKeys.add(profile.evaluation_profile);
  }

  const groups = new Map();
  for (const run of latestCompleteRuns(runs)) {
    const profile = profiles.find((candidate) =>
      candidate.evaluation_profile === run.evaluation_profile
    );
    if (!profile) continue;
    const key = overallConfigurationKey(run);
    let group = groups.get(key);
    if (!group) {
      group = new Map();
      groups.set(key, group);
    }
    const previous = group.get(profile.evaluation_profile);
    if (!previous
      || Date.parse(run.completed_at) > Date.parse(previous.completed_at)
      || (run.completed_at === previous.completed_at && run.run_id > previous.run_id)) {
      group.set(profile.evaluation_profile, run);
    }
  }

  const rows = [];
  for (const group of groups.values()) {
    const taskRuns = profiles.map((profile) => group.get(profile.evaluation_profile));
    if (taskRuns.some((run) => !run)) continue;
    const taskRows = taskRuns.map((run) => rowForRun(run, scoreMetric));
    const taskScores = taskRows.map((taskRow) => taskRow.score);
    const taskPrimaryMetrics = taskRows.map((taskRow) => taskRow.primary_metric);
    if (taskScores.some((score) => score === null)) continue;
    const row = taskRows[0];
    const providers = new Set(
      taskRuns.map((run) => run.model.upstream_provider ?? "not reported")
    );
    if (providers.size > 1) row.model_cell.provider = AUTO_ROUTED_PROVIDER;
    row.runs = taskRuns;
    delete row.run;
    row.score = taskScores.reduce((total, score) => total + score, 0) / taskScores.length;
    row.accuracy = taskPrimaryMetrics.every((metric) => metric === "exact_match")
      ? row.score
      : null;
    row.primary_metric = "task_macro_average";
    row.tokens = sumIfComplete(
      taskRuns.map((run) => nonnegativeNumber(run.metrics.total_tokens))
    );
    row.cost = sumIfComplete(
      taskRuns.map((run) => nonnegativeNumber(run.metrics.total_cost_usd))
    );
    row.format_failures = taskRuns.reduce(
      (total, run) => total + (nonnegativeNumber(run.metrics.format_failures) ?? 0),
      0
    );
    row.retry_count = taskRuns.reduce((total, run) => total + (run.retry_count ?? 0), 0);
    row.task_scores = profiles.map((profile, index) => ({
      task_family: profile.task_family,
      evaluation_profile: profile.evaluation_profile,
      score: taskScores[index],
      accuracy: taskPrimaryMetrics[index] === "exact_match" ? taskScores[index] : null,
      primary_metric: taskPrimaryMetrics[index],
      run: taskRuns[index]
    }));
    rows.push(row);
  }
  return sortLeaderboardRows(rows);
}

export function supportsOverallLeaderboard(leaderboard) {
  return overallProfiles(leaderboard).length > 0;
}

export function leaderboardRowsForScope(
  runs,
  leaderboard,
  taskFamily = null,
  scoreMetric = null
) {
  if (taskFamily === null) {
    return leaderboard
      ? overallLeaderboardRows(runs, leaderboard, scoreMetric)
      : leaderboardRows(runs, scoreMetric);
  }
  const profile = leaderboard?.evaluation_profiles?.find(
    (candidate) => candidate.task_family === taskFamily
  );
  if (!profile) return [];
  return leaderboardRows(
    runs.filter((run) => run.evaluation_profile === profile.evaluation_profile),
    scoreMetric
  );
}

export function orderTaskFamilies(taskFamilies) {
  return taskFamilies.toSorted(compareTaskFamilies);
}

export function modelSelectionRows(runs, leaderboard) {
  if (leaderboard) {
    const rows = overallLeaderboardRows(runs, leaderboard);
    const currentRuns = latestCompleteRuns(runs);
    const profileByEvaluation = new Map(
      leaderboard.evaluation_profiles.map((profile) => [profile.evaluation_profile, profile])
    );
    const rowsByConfiguration = new Map(
      rows.map((row) => [overallConfigurationKey(row.runs[0]), row])
    );
    for (const candidate of currentRuns) {
      const profile = profileByEvaluation.get(candidate.evaluation_profile);
      if (!profile) continue;
      const key = overallConfigurationKey(candidate);
      let row = rowsByConfiguration.get(key);
      if (!row) {
        row = rowForRun(candidate);
        row.runs = [];
        row.task_scores = [];
        delete row.run;
        rows.push(row);
        rowsByConfiguration.set(key, row);
      }
      if (row.task_scores.some(
        (task) => task.evaluation_profile === candidate.evaluation_profile
      )) continue;
      row.task_scores.push({
        task_family: profile.task_family,
        evaluation_profile: profile.evaluation_profile,
        score: primaryScore(candidate),
        run: candidate
      });
      row.runs.push(candidate);
    }
    return sortLeaderboardRows(rows);
  }
  return leaderboardRows(runs).map((row) => ({...row, runs: [row.run]}));
}

export function runForTask(row, taskFamily) {
  const taskRun = row?.task_scores?.find((task) => task.task_family === taskFamily)?.run;
  return taskRun ?? (row?.runs?.length === 1 ? row.runs[0] : null);
}

export function orderQuestionsForExplorer(questions) {
  return questions.toSorted((left, right) => {
    const leftFamily = left?.metadata?.task_family ?? "";
    const rightFamily = right?.metadata?.task_family ?? "";
    return compareTaskFamilies(leftFamily, rightFamily)
      || (left?.question_id ?? "").localeCompare(right?.question_id ?? "");
  });
}

export function variantType(ref, alt) {
  if (typeof ref !== "string" || typeof alt !== "string" || ref === alt
    || !/^[ACGT]*$/.test(ref + alt)) return "Unknown";
  while (ref && alt && ref[0] === alt[0]) {
    ref = ref.slice(1);
    alt = alt.slice(1);
  }
  while (ref && alt && ref.at(-1) === alt.at(-1)) {
    ref = ref.slice(0, -1);
    alt = alt.slice(0, -1);
  }
  return ref.length !== alt.length ? "Indel"
    : ref.length === 1 ? "SNV" : "Multibase substitution";
}

export function predictionComparisonRows(question, result, displayMetadata = null) {
  const predictions = result?.scoring?.parsed_answer;
  if (question?.task_type !== "ranking"
    || result?.scoring?.metric !== "rank_correlation"
    || !predictions
    || typeof predictions !== "object"
    || Array.isArray(predictions)) return [];

  return (Array.isArray(question.candidates) ? question.candidates : []).flatMap(
    (candidate) => {
      const measured = finiteNumber(candidate?.reference_score);
      const predicted = Object.hasOwn(predictions, candidate?.candidate_id)
        ? finiteNumber(predictions[candidate.candidate_id])
        : null;
      const annotation = displayMetadata?.source_record_sha256
        && displayMetadata.source_record_sha256 === question.provenance?.source_record_sha256
        ? displayMetadata.variants?.[candidate.candidate_id]
        : null;
      return measured === null || predicted === null
        ? []
        : [{
            candidate_id: candidate.candidate_id,
            measured,
            predicted,
            variant_type: variantType(candidate.ref, candidate.alt),
            consequence: annotation?.most_severe_consequence ?? "Not annotated",
            genomic: annotation?.genomic ?? null
          }];
    }
  );
}

export function defaultQuestionForExplorer(
  visibleEntries,
  {
    currentQuestionId = null,
    knownQuestionIds = new Set(),
    evaluatedQuestionIds = new Set(),
    preferEvaluated = false
  } = {}
) {
  const current = visibleEntries.find((entry) => entry.question_id === currentQuestionId);
  if (current) return current;
  if (currentQuestionId && !knownQuestionIds.has(currentQuestionId)) return null;
  return (
    (preferEvaluated
      ? visibleEntries.find((entry) => evaluatedQuestionIds.has(entry.question_id))
      : null)
    ?? visibleEntries[0]
    ?? null
  );
}

export function groupCurrentRuns(runs) {
  return runs
    .filter((run) => run.coverage.complete)
    .toSorted((left, right) => left.run_id.localeCompare(right.run_id));
}

export function artifactUrl(baseUrl, path) {
  const base = new URL(baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  return new URL(path, base).href;
}

export async function fetchJson(url, fetcher = fetch) {
  const response = await fetcher(url);
  if (!response.ok) throw new Error(`Unable to load ${url}: HTTP ${response.status}`);
  return response.json();
}

export function answerPath(run, questionId) {
  if (!SAFE_ID.test(run.run_id) || !SAFE_ID.test(questionId)) {
    throw new Error("Unsafe run or question ID");
  }
  if (run.answer_prefix !== `answers/${run.run_id}/`) {
    throw new Error("Run answer prefix does not match its ID");
  }
  return `${run.answer_prefix}${encodeURIComponent(questionId)}.json.gz`;
}

export function outcomeIndexPath(run) {
  if (!SAFE_ID.test(run.run_id)) throw new Error("Unsafe run ID");
  const expected = `outcomes/${run.run_id}.json.gz`;
  if (run.outcome_index_path !== expected) {
    throw new Error("Run outcome index path does not match its ID");
  }
  return expected;
}

export async function fetchGzipJson(url, label, fetcher = fetch) {
  const response = await fetcher(url);
  if (!response.ok) throw new Error(`Unable to load ${label}: HTTP ${response.status}`);
  if (typeof DecompressionStream !== "function") {
    throw new Error("This browser does not support gzip decompression");
  }
  const decompressed = response.body.pipeThrough(new DecompressionStream("gzip"));
  return new Response(decompressed).json();
}

export async function fetchAnswer(baseUrl, run, questionId, fetcher = fetch) {
  return fetchGzipJson(
    artifactUrl(baseUrl, answerPath(run, questionId)),
    "answer",
    fetcher
  );
}

export async function fetchAnswerIfAvailable(
  baseUrl,
  run,
  questionId,
  outcomeIndex,
  fetcher = fetch
) {
  const available = outcomeIndex?.outcomes?.some(
    (outcome) => outcome?.question_id === questionId
  );
  return available ? fetchAnswer(baseUrl, run, questionId, fetcher) : null;
}

export async function fetchOutcomeIndex(baseUrl, run, fetcher = fetch) {
  const document = await fetchGzipJson(
    artifactUrl(baseUrl, outcomeIndexPath(run)),
    "outcome index",
    fetcher
  );
  if (
    document?.schema_version !== "1.0"
    || document.run_id !== run.run_id
    || document.question_set_sha256 !== run.question_set_sha256
    || document.question_set_size !== run.question_set_size
    || !Array.isArray(document.outcomes)
  ) {
    throw new Error("Outcome index does not match its run");
  }
  return document;
}
