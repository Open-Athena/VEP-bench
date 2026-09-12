// Offline extraction from downloaded public source files. Never runs evaluations.
// Usage: node projects/explorer/scripts/freeze_comparisons.mjs SOURCE_DIRECTORY
// Replay the committed analysis: node projects/explorer/scripts/freeze_comparisons.mjs --replay
import {createHash} from "node:crypto";
import {mkdirSync, readFileSync, statSync, writeFileSync} from "node:fs";
import {resolve} from "node:path";
import {compareScores, pairedScoresCsv} from "../web/blog/introducing-vep-bench/comparisons.js";

if (!process.argv[2]) throw new Error("Expected a source directory or --replay");
const input = resolve(process.argv[2]);
const output = new URL("../web/blog/introducing-vep-bench/comparisons-data/", import.meta.url);
mkdirSync(output, {recursive: true});
const read = (file) => readFileSync(resolve(input, file), "utf8");
const json = (file) => JSON.parse(read(file));
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");
const write = (file, value) => writeFileSync(new URL(file, output), JSON.stringify(value, null, 2) + "\n");
if (process.argv[2] === "--replay") {
  exportAnalysis(JSON.parse(readFileSync(new URL("vep-runs.json", output))),
    JSON.parse(readFileSync(new URL("external.json", output))));
  process.exit(0);
}
const sources = [], results = [], comparisons = [];
function source(id, file, url, locator, evidence) {
  const record = {id, url, retrieved_at: statSync(resolve(input, file)).mtime.toISOString(),
    downloaded_sha256: sha(readFileSync(resolve(input, file))), locator, evidence};
  sources.push(record);
  return record;
}
function comparison(id, label, group, metric, tier, details = {}) {
  comparisons.push({id, label, group, metric, tier, ...details});
}
function nextObjects(html) {
  const stream = [...html.matchAll(/self\.__next_f\.push\((.*?)\)<\/script>/gs)]
    .map((m) => JSON.parse(m[1])).filter((v) => v[0] === 1).map((v) => v[1]).join("");
  return stream.split("\n").flatMap((line) => {
    try { return [JSON.parse(line.slice(line.indexOf(":") + 1))]; } catch { return []; }
  });
}
function collect(value, predicate, output = []) {
  if (value && typeof value === "object") {
    if (predicate(value)) output.push(value);
    Object.values(value).forEach((v) => collect(v, predicate, output));
  }
  return output;
}
const vep = json("vep-runs.json"), manifest = json("vep-manifest.json");
if (manifest.artifacts.runs.artifact_sha256 !== sha(readFileSync(resolve(input, "vep-runs.json")))) {
  throw new Error("VEP runs do not match the downloaded publication manifest");
}
writeFileSync(new URL("vep-runs.json", output), readFileSync(resolve(input, "vep-runs.json")));
source("vep", "vep-manifest.json", "https://huggingface.co/buckets/open-athena/VEP-bench/resolve/versions/main/manifest.json",
  "Validated local publication candidate; URL identifies the intended publication destination", {runs: manifest.artifacts.runs,
    question_set_sha256: manifest.question_set_sha256, question_set_size: manifest.question_set_size});

const aaModels = collect(nextObjects(read("aa.html")), (v) => Array.isArray(v.initialModels))[0].initialModels;
const aaCatalog = [...new Map(collect(nextObjects(read("aa.html")),
  (v) => typeof v.slug === "string" && v.release && v.creator)
  .map((v) => [v.slug, {slug: v.slug, name: v.name, release: v.release, effort: v.effort ?? null}])).values()];
const aaMap = {
  "gpt-6-astra": "openai/gpt-6-astra", "gpt-5-6-sol": "openai/gpt-5.6-sol",
  "gpt-5-6-luna": "openai/gpt-5.6-luna", "gemini-3-8-flash": "google/gemini-3.8-flash",
  "gpt-5-6-terra": "openai/gpt-5.6-terra",
  "muse-spark-1-3": "meta/muse-spark-1.3", "glm-5-3": "z-ai/glm-5.3",
  "deepseek-v4-1-flash": "deepseek/deepseek-v4.1-flash",
  "claude-opus-5": "anthropic/claude-opus-5", "claude-fable-5-1": "anthropic/claude-fable-5.1"
};
source("aa-catalog", "aa.html", "https://artificialanalysis.ai/models", "Public model selector; exact release and effort labels", aaCatalog);
const aaMetrics = [
  ["intelligence", "Artificial Analysis Intelligence Index v4.3", "intelligenceIndex", "Index points", null, null, "Mixed suite", "Overall", null],
  ["briefcase", "AA-Briefcase", "briefcaseBreakdown.overall.elo", "Elo", 91, 1, "File and code tools", "Agents", 0.15],
  ["gdpval", "GDPval-AA v2", "gdpval", "Elo", 220, 1, "File and code tools", "Agents", 0.10],
  ["automation", "AutomationBench-AA", "automationBenchPartialScore", "Partial completion (fraction)", 657, 1, "SaaS REST APIs", "Agents", 0.05],
  ["terminal", "Terminal-Bench v4.0 (AA)", "terminalbenchV40", "Pass@1 (fraction)", 66, 3, "Terminal", "Coding", 0.10],
  ["scicode", "SciCode (AA)", "scicode", "Subproblem pass@1 (fraction)", 288, 3, "No model tools; generated code is graded", "Coding", 0.10],
  ["omniscience", "AA-Omniscience", "omniscience", "Omniscience index points", 6000, 1, "No tools", "General", 0.15],
  ["gdp-pdf", "GDP.pdf", "gdpPdfAllPass", "All-pass (fraction)", 100, 5, "No tools", "General", 0.10],
  ["lcr", "AA-LCR v1.1", "lcr", "Pass@1 (fraction)", 100, 3, "No tools", "General", 0.05],
  ["hle", "Humanity’s Last Exam (AA)", "hle", "Pass@1 (fraction)", 2158, 1, "No tools", "Scientific reasoning", 0.10],
  ["critpt", "CritPt (AA)", "critpt", "Pass@1 (fraction)", 70, 5, "No model tools; official grader", "Scientific reasoning", 0.10]
];
const aaVersion = [...new Set(read("aa.html").match(/Intelligence Index v[\d.]+/g))];
const aaSetups = {
  briefcase: {harness: "Stirrup", budget: "500 turns/task", tools: "Shell/code execution, finish tools; image tool when supported; no internet"},
  gdpval: {harness: "Stirrup", budget: "250 turns/task"},
  automation: {harness: "AutomationBench multi-turn environment, dataset 1.0.6", budget: "50 turns/task"},
  terminal: {harness: "mini-SWE-agent v2.4.6", budget: "500 steps; 30 seconds/command; task limit up to 8 hours; no compaction"},
  scicode: {harness: "AA scientist-background prompting; SciCode dataset 1.0.1", budget: "300-second grading execution limit"},
  critpt: {harness: "AA two-step reasoning/formatting; official CritPt grader"}
};
if (aaVersion.length !== 1 || aaVersion[0] !== "Intelligence Index v4.3") throw new Error("Review AA methodology version before refreshing");
source("aa-method", "aa-method.html", "https://artificialanalysis.ai/methodology/intelligence-benchmarking",
  "Intelligence Index v4.3 methodology table and general testing parameters", {
    version: "4.3", metrics: aaMetrics, setups: aaSetups,
    temperature: "0.6 for reasoning models unless model lab recommends otherwise",
    output_budget: "Model-specific maximum output tokens; per-run limits not disclosed here",
    tools: "Benchmark-specific; e2b is the primary sandbox for agentic evaluations",
    note: "Component scores are reported separately; no new category composite is constructed. AA-Omniscience index is the published score, not the two separately weighted AAII terms."
  });
for (const [id, label, , metric_label, task_count, repeats, tools, category, weight] of aaMetrics) {
  comparison(`aa-${id}`, label, "aa", id, "secondary", {benchmark: "Artificial Analysis", version: "4.3",
    subset: id === "intelligence" ? "Overall" : category, metric_label, task_count, repeats, tools,
    harness: "Artificial Analysis evaluation suite (benchmark-specific harnesses)", source_ids: ["aa-method"],
    budget: "Model-specific maximum output tokens; see source methodology", aa_index_weight: weight,
    ...aaSetups[id]});
}
const aaHarnesses = Object.fromEntries(comparisons.filter((c) => c.group === "aa")
  .map((c) => [c.metric, c.harness]));
function addAa(model, file, url) {
  if (!read(file).includes("Intelligence Index v4.3")) throw new Error(`Wrong AAII version: ${file}`);
  const scores = Object.fromEntries(aaMetrics.map(([id, , path]) => [id,
    path.split(".").reduce((v, k) => v?.[k], model) ?? null]));
  const id = `aa-${model.slug}`;
  source(id, file, url, `Public page data object with slug=${model.slug}`, {
    slug: model.slug, name: model.name, release: model.release, effort: model.effort,
    intelligenceIndexIsEstimated: model.intelligenceIndexIsEstimated,
    ...Object.fromEntries(aaMetrics.map(([, , path]) => [path,
      path.split(".").reduce((v, k) => v?.[k], model) ?? null]))
  });
  results.push({id, group: "aa", model_label: model.name, model_id: aaMap[model.release.slug] ?? null,
    model_revision: null,
    // Some entries put an explicit effort only in the published model label.
    effort: model.effort?.slug ?? model.name.match(/\b(none|minimal|low|medium|high|xhigh|max) effort\b/i)?.[1].toLowerCase() ?? null,
    scores, source_id: id,
    reported_at: sources.at(-1).retrieved_at,
    harness: "Artificial Analysis evaluation suite (benchmark-specific harnesses)",
    harness_by_metric: aaHarnesses, submitter: "Artificial Analysis",
    exclusion: /fallback/i.test(model.name) ? "Fallback system may use another model; excluded from model-level comparisons"
      : model.intelligenceIndexIsEstimated ? "Estimated AAII score; independent measurement unavailable" : null});
}
for (const m of aaModels.filter((m) => aaMap[m.release.slug])) addAa(m, "aa.html", "https://artificialanalysis.ai/models");
for (const [file, slug] of [
  ["astra-high", "gpt-6-astra-high"], ["sol-high", "gpt-5-6-sol-high"], ["luna-high", "gpt-5-6-luna-high"],
  ["astra-medium", "gpt-6-astra-medium"], ["sol-medium", "gpt-5-6-sol-medium"], ["luna-medium", "gpt-5-6-luna-medium"],
  ["astra-low", "gpt-6-astra-low"], ["sol-low", "gpt-5-6-sol-low"], ["luna-low", "gpt-5-6-luna-low"],
  ["gemini-medium", "gemini-3-8-flash-medium"], ["gemini-low", "gemini-3-8-flash-low"]
]) {
  const name = `aa-${file}.html`;
  const models = collect(nextObjects(read(name)), (v) => v.slug === slug && "scicode" in v);
  if (models.length !== 1) throw new Error(`Expected one exact model in ${name}`);
  addAa(models[0], name, `https://artificialanalysis.ai/models/${slug}`);
}

const tbs = json("tbs-results.json");
const tbsMap = {"Opus 5": "anthropic/claude-opus-5", "GPT-5.6 Sol": "openai/gpt-5.6-sol",
  "GPT-5.6 Terra": "openai/gpt-5.6-terra",
  "GPT-5.6 Luna": "openai/gpt-5.6-luna", "Gemini 3.8 Flash": "google/gemini-3.8-flash", "GLM 5.3": "z-ai/glm-5.3",
  "DeepSeek V4.1 Flash": "deepseek/deepseek-v4.1-flash"};
source("tbs", "tbs-results.json", "https://www.terminal-bench-science.ai/?view=domains",
  "Official leaderboard payload: domain_metrics.life and task_matrix.tasks", {
    api_url: "https://www.terminal-bench-science.ai/api/leaderboard?package=terminal-bench-science%2Fterminal-bench-science&name=v0-1-eval",
    tasks: tbs.task_matrix.tasks, rows: tbs.rows.map((r) => ({id: r.id, metadata: r.metadata,
      life: r.metrics.domain_metrics.life, updated_at: r.updated_at, n_trials: r.n_trials}))});
for (const row of tbs.rows) {
  const group = "tbs";
  results.push({id: `tbs-${row.id}`, group, model_label: row.metadata.model_display.label,
    model_id: tbsMap[row.metadata.model_display.label] ?? null, effort: row.metadata.reasoning_effort,
    scores: {life: row.metrics.domain_metrics.life.accuracy / 100}, source_id: "tbs",
    reported_at: row.updated_at, harness: row.metadata.agent_display.label,
    budget: {total_cost_usd: row.metrics.domain_metrics.life.total_cost_usd,
      total_tokens: row.metrics.domain_metrics.life.total_tokens, time_limit: null}});
  if (!comparisons.some((c) => c.group === group)) comparison(group, "Terminal-Bench-Science",
    group, "life", "primary", {benchmark: "Terminal-Bench-Science", version: "0.1", subset: "Life Sciences (broader than biology)",
      metric_label: "Resolution rate (fraction)", task_count: 19, repeats: 3, tools: "Terminal and task environment",
      harness: "Codex where available, otherwise mini-SWE-agent", harness_preference: ["Codex", "mini-SWE-agent"],
      budget: "Task-specific; recorded life-domain cost and tokens retained per result",
      source_ids: ["tbs"]});
}

const geneLines = read("genebench-table.txt").split("\n");
const geneRows = geneLines.flatMap((line, i) => / \(.+\)$/.test(line) && /^\d+\.\d+%$/.test(geneLines[i + 1] ?? "")
  ? [{model: line, mean_percent: Number.parseFloat(geneLines[i + 1]), mean_valid_attempts: Number(geneLines[i + 8]),
    min_valid_attempts: Number(geneLines[i + 9]), max_valid_attempts: Number(geneLines[i + 10])}] : []);
if (geneRows.length !== 60) throw new Error("GeneBench-Pro table extraction is incomplete");
source("gene", "genebench-pro.pdf", "https://cdn.openai.com/pdf/21938268-21af-442f-af93-3b2249afb241/genebench-pro.pdf",
  "Supplementary Table 1, PDF page 21 (visually verified); Methods, page 15", geneRows);
comparison("gene", "GeneBench-Pro", "gene", "pass_rate", "primary", {benchmark: "GeneBench-Pro", version: "2026-06-30 paper",
  subset: "Full 129 problems", metric_label: "Mean per-problem pass rate (fraction)", task_count: 129,
  repeats: "10 standard attempts; 5 for Claude Opus and Pro; errors excluded", harness: "GeneBench Docker analysis harness",
  tools: "Python, R, scientific and genomics software; no internet", budget: "No additional uniform wall-clock limit",
  source_ids: ["gene"]});
for (const row of geneRows) {
  const [, model, effort] = row.model.match(/^(.*) \((.*)\)$/);
  const id = `gene-${row.model}`;
  results.push({id, group: "gene", model_label: row.model,
    model_id: {"GPT-5.6 Sol": "openai/gpt-5.6-sol", "GPT-5.6 Luna": "openai/gpt-5.6-luna",
      "GPT-5.6 Terra": "openai/gpt-5.6-terra"}[model] ?? null,
    effort, scores: {pass_rate: row.mean_percent / 100}, source_id: "gene", reported_at: "2026-06-30",
    harness: "GeneBench Docker analysis harness", valid_attempts: row,
    exclusion: model.includes(" Pro") ? "Pro system is not the corresponding standard model" : null});
}

const bixRows = JSON.parse(read("bix3.html").match(/const clientRows = (.*?);/s)[1]);
source("bix3", "bix3.html", "https://advances.edisonscientific.com/benchmarks/bixbench3", "Published clientRows", bixRows);
source("bix3-harness", "bix3-models.yaml", "https://github.com/EdisonScientific/BixBench3/blob/main/src/bixbench3/reference_models.yaml",
  "Official reference model settings", json("bix3-models.yaml"));
comparison("bix3", "BixBench3", "bix3", "paper_score", "primary", {benchmark: "BixBench3", version: "v1.0.0",
  subset: "20 research workflows", metric_label: "Mean artifact paper score (fraction)", task_count: 20,
  repeats: null, harness: "BixBench3 public runner", tools: "GCP VM, Docker, scientific software, controlled network",
  budget: "Up to 24 hours per task; observed costs retained", source_ids: ["bix3", "bix3-harness"]});
for (const row of bixRows) results.push({id: `bix3-${row.model}`, group: "bix3", model_label: row.model,
  model_id: {"gpt-5.6-sol": "openai/gpt-5.6-sol", "claude-opus-5": "anthropic/claude-opus-5"}[row.model] ?? null,
  effort: row.mode, scores: row.scores, source_id: "bix3", reported_at: "2026-08-26", harness: "BixBench3 public runner"});

for (const [id, file, url, evidence] of [
  ["bioagent", "bioagent.html", "https://omicsagent.ai/evals", "10 bioinformatics pipelines; January 2026 results name GPT 5.2, GPT 5.1 Codex-Max, Gemini 3 Pro Preview, MiniMax M2.1, GLM 4.7, Kimi K2 Thinking, Qwen3 Coder and Devstral 2. No catalog release matches."],
  ["scienceagent", "scienceagent.md", "https://github.com/OSU-NLP-Group/ScienceAgentBench", "102 scientific code-execution tasks, including bioinformatics; original evaluation targets older model releases. No exact current VEP release match established in this report."],
  ["asta", "asta.html", "https://allenai.org/blog/astabench-update-spring-2026", "April 30 ReAct report: Opus 4.7/4.6, Sonnet 4.6, GPT-5.5/5.4, Gemini 3.1 Pro Preview. No exact VEP release overlap; multi-model Asta v0 is ineligible."],
  ["bix", "bix.md", "https://github.com/Future-House/BixBench", "Original agentic report uses GPT-4o and Claude 3.5 Sonnet; no exact VEP release overlap. Keep revisions and zero-shot results separate."]
]) source(id, file, url, "Survey evidence from official documentation or evaluation report", evidence);

const survey = [
  ["Terminal-Bench-Science", "tbs", "Primary, descriptive", "Agentic scientific workflows. The official result taxonomy exposes Life Sciences, including medical imaging; no narrower biology label is used. Choose Codex where available, otherwise mini-SWE-agent, for each exact model and effort; document the harness in each paired score."],
  ["GeneBench-Pro", "gene", "Primary, descriptive", "Multistep quantitative biology in the published GeneBench Docker harness. Exact model and effort matches only; Pro systems excluded. Original GeneBench scores are not substituted."],
  ["BixBench3", "bix3", "Primary when exactly matched", "Agentic research-scale computational biology using the published BixBench3 runner. Include only complete VEP configurations with an exact model and effort match."],
  ["BixBench (original)", "bix", "No exact version overlap in inspected report", "Agentic data analysis is relevant; original evaluated releases differ from VEP. Zero-shot and revised benchmarks are separate."],
  ["BioAgent Bench", "bioagent", "No exact version overlap in inspected report", "Relevant end-to-end bioinformatics execution. Public January results evaluate older releases."],
  ["ScienceAgentBench", "scienceagent", "No exact version overlap established", "Relevant bioinformatics subset of a broader execution benchmark; inspected original report provides no current VEP release match. All-science scores would not be a biology score."],
  ["AstaBench (April 2026 report)", "asta", "No exact version overlap in inspected report", "Relevant data-analysis and execution tasks within broader science. All named base models in this report differ from VEP; composite Asta systems are not single models."],
  ["Artificial Analysis Intelligence Index and components", "aa-method", "Secondary, descriptive", "General intelligence comparison using latest v4.3 at retrieval and Artificial Analysis's published harnesses. All exact model and effort matches and all ten published component metrics are retained independently of their association; none is labeled an agentic biology score."],
  ["Standalone biology Q&A / BixBench zero-shot", "bix", "Outside primary agentic scope", "Knowledge-only or multiple-choice answers without an executed analysis do not meet the primary inclusion criterion; this does not exclude multiple-choice endpoints reached through genuine tool use."]
].map(([benchmark, source_id, decision, rationale]) => ({benchmark, source_id, decision, rationale}));
const external = {schema_version: "1.0", snapshot_date: "2026-09-12", aa_intelligence_index_version: "4.3",
  rules: [
    "Choose benchmarks for biological relevance, agentic execution, attributable public results and overlap before computing associations.",
    "Exact named model release and exact explicit effort only. No family substitution, inferred defaults, max-to-high conversion, imputation, or fallback efforts. Hidden checkpoint revisions are not disclosed by these sources.",
    "Use only complete VEP configurations spanning all three tasks. Overall is the unweighted mean of task mean Spearman scores, reusing explorer aggregation without display clipping.",
    "For each benchmark and metric, include every exact shared effort as a separate point. Choose one harness per model and effort by the comparison's fixed harness preference, falling back to alphabetical harness name. Use the latest published submission within that harness; timestamp ties require review. Never select by score.",
    "Use one comparison per benchmark metric and record the chosen harness for each paired score. These are model-plus-harness results, not a controlled model-only comparison. Other submissions remain in the source audit, never extra comparison points.",
    "Use only the Overall VEP score. Count matched configurations and distinct model versions separately; multiple efforts from one model are dependent observations.",
    "The all-effort plots are descriptive. Never pool repeated efforts into a cross-model correlation. Correlation summaries require at least five distinct models with one observation each and nonconstant scores; no p-values or imputation.",
    "Use AAII v4.3, the latest methodology at retrieval. Components retain their published metrics. Do not combine index versions or invent unreported subset indices."
  ], sources, survey, comparisons, results};
write("external.json", external);
exportAnalysis(vep, external);

function exportAnalysis(vep, external) {
  const primary = compareScores(vep, external);
  write("analysis.json", {snapshot_date: external.snapshot_date, primary: primary.comparisons});
  write("model-matches.json", primary.matches.map((r) => ({result_id: r.id, model_label: r.model_label,
    model_id: r.model_id, effort: r.effort, harness: r.harness, submitter: r.submitter ?? null,
    source_id: r.source_id, match_status: r.match_status,
    vep_efforts: primary.configurations.filter((c) => c.model_id === r.model_id).map((c) => c.effort),
    selected_in: primary.comparisons.filter((c) => c.scope === "overall" && c.pairs.some((p) => p.external_result_id === r.id)).map((c) => c.id)})));
  writeFileSync(new URL("paired-scores.csv", output), pairedScoresCsv(primary.comparisons));
  console.log(JSON.stringify(primary.comparisons.map((c) => ({comparison: c.id,
    points: c.summary.points, models: c.summary.n})), null, 2));
}
