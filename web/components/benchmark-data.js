const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;

function modelName(modelId, generationParameters) {
  const name = modelId.split("/").at(-1) ?? modelId;
  const displayName = {
    "gpt-5.6-luna": "GPT 5.6 Luna",
    "gpt-5.6-sol": "GPT 5.6 Sol"
  }[name] ?? name;
  const effort = generationParameters?.reasoning?.effort;
  return effort ? `${displayName} (${effort})` : displayName;
}

function nonnegativeNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function formatRunLabel(run) {
  const model = modelName(run?.model?.model_id ?? "unknown model", run?.generation_parameters);
  const provider = run?.model?.upstream_provider ?? "provider not reported";
  return `${model} · ${provider}`;
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

function rowForRun(run) {
  const family = run.model.family ?? modelName(run.model.model_id);
  return {
    run,
    model_cell: {
      model: modelName(run.model.model_id, run.generation_parameters),
      provider: run.model.upstream_provider ?? "not reported"
    },
    family,
    family_id: family,
    release_date: run.model.release_date ?? null,
    tokens: nonnegativeNumber(run.metrics.total_tokens),
    cost: nonnegativeNumber(run.metrics.total_cost_usd),
    accuracy: nonnegativeNumber(run.metrics.accuracy),
    format_failures: run.metrics.format_failures
  };
}

function sortLeaderboardRows(rows) {
  return rows.sort((a, b) =>
    (b.accuracy ?? -1) - (a.accuracy ?? -1)
    || a.model_cell.model.localeCompare(b.model_cell.model)
    || a.model_cell.provider.localeCompare(b.model_cell.provider)
  );
}

export function leaderboardRows(runs) {
  return sortLeaderboardRows(latestCompleteRuns(runs).map(rowForRun));
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
      model_revision: run.model.model_revision ?? null,
      upstream_provider: run.model.upstream_provider ?? null
    },
    generation_parameters: run.generation_parameters
  }));
}

function sumIfComplete(values) {
  return values.every((value) => value !== null)
    ? values.reduce((total, value) => total + value, 0)
    : null;
}

export function overallLeaderboardRows(runs, leaderboard) {
  if (leaderboard?.aggregation_method !== "task_macro_average_v0") return [];
  const profiles = leaderboard.evaluation_profiles;
  if (!Array.isArray(profiles) || profiles.length === 0) return [];
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
    const taskAccuracies = taskRuns.map((run) => nonnegativeNumber(run.metrics.accuracy));
    if (taskAccuracies.some((accuracy) => accuracy === null)) continue;
    const representative = taskRuns[0];
    const row = rowForRun(representative);
    row.runs = taskRuns;
    delete row.run;
    row.accuracy = taskAccuracies.reduce((total, accuracy) => total + accuracy, 0)
      / taskAccuracies.length;
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
    row.task_scores = profiles.map((profile, index) => ({
      task_family: profile.task_family,
      evaluation_profile: profile.evaluation_profile,
      accuracy: taskAccuracies[index],
      run: taskRuns[index]
    }));
    rows.push(row);
  }
  return sortLeaderboardRows(rows);
}

export function leaderboardLineSeries(rows, metric) {
  if (metric !== "cost" && metric !== "tokens") {
    throw new Error(`Unknown leaderboard line metric ${metric}`);
  }
  const byFamily = new Map();
  for (const row of rows) {
    const x = nonnegativeNumber(row[metric]);
    const accuracy = nonnegativeNumber(row.accuracy);
    if (x === null || accuracy === null) continue;
    let series = byFamily.get(row.family_id);
    if (!series) {
      series = {family_id: row.family_id, family: row.family, points: []};
      byFamily.set(row.family_id, series);
    }
    series.points.push({x, accuracy, row});
  }
  return [...byFamily.values()]
    .map((series) => ({
      ...series,
      points: series.points.toSorted((a, b) =>
        a.x - b.x
        || a.accuracy - b.accuracy
        || a.row.model_cell.model.localeCompare(b.row.model_cell.model)
      )
    }))
    .sort((a, b) => a.family.localeCompare(b.family));
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

async function fetchGzipJson(url, label, fetcher) {
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
