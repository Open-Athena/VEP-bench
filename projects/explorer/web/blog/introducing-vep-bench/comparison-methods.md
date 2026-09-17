---
title: "External benchmark comparison: methodology and sources"
---

# External benchmark comparison: methodology and sources

The [introduction post](../introducing-vep-bench.html#does-variant-effect-prediction-track-other-capabilities)
shows one visual comparison with the Artificial Analysis Intelligence Index.
The limited number of model releases and repeated reasoning efforts make this
an exploratory view. We do not compute between-benchmark Pearson or Spearman
coefficients, significance tests, or fitted trends.

## Scores and matching

VEP-bench's Overall score is the unweighted mean of its three task scores.
Each task score is the mean within-panel Spearman correlation between predicted
and measured variant effects. This remains the benchmark's scoring metric;
the external comparison uses these saved scores without display clipping.

Each paired point requires the same named model release and an explicitly
reported, identical reasoning effort. Missing or ambiguous efforts are excluded;
“max” never substitutes for “high”. The sources do not consistently disclose
immutable checkpoint revisions, so identity cannot be established beyond named
releases. Matching effort labels does not equalize tokens, tools, prompts, or
execution budgets across evaluations.

All exactly matched efforts are retained. A fixed harness preference selects
one harness per model and effort, followed by its latest reported submission,
independently of score. Terminal-Bench-Science prefers Codex, then mini-SWE-agent;
other harness names fall back to alphabetical order. Artificial Analysis,
GeneBench-Pro, and BixBench3 use their respective published evaluation harnesses.
Tooltips and paired-score downloads record the selected harness.

The VEP snapshot is frozen on September 15, 2026, with complete scores on all
three tasks for eight model releases. Artificial Analysis results come from the
September 11 source snapshot, using [Intelligence Index v4.3](https://artificialanalysis.ai/methodology/intelligence-benchmarking).
There are 15 matched configurations across five model releases: GPT-6 Astra and
Gemini 3.8 Flash at low, medium, and high; GPT-5.6 Sol and Luna at low, medium,
high, and max; and GPT-5.6 Terra at max. Efforts from the same model are related
observations and do not increase the number of distinct models.

The remaining complete VEP releases have no exact AA effort match:

| Model | Completed VEP effort | Published AA efforts |
| --- | --- | --- |
| [Muse Spark 1.3](https://artificialanalysis.ai/models/muse-spark-1-3) | medium | max, xhigh |
| [GLM-5.3](https://artificialanalysis.ai/models/glm-5-3) | low | max |
| [DeepSeek V4.1 Flash](https://artificialanalysis.ai/models/deepseek-v4-1-flash) | low | max |

The ten AA component evaluations remain in the source data. The post displays
only the overall index to keep the comparison focused. The index and its
components are related measurements, and general intelligence scores do not
directly measure agentic biology capability.

## Biology benchmark coverage

We surveyed evaluations involving code execution, tool use, or multistep
biological data analysis. Their matched model coverage is too sparse for the
post's visual comparison:

| Benchmark | Matched configurations | Distinct models |
| --- | ---: | ---: |
| GeneBench-Pro | 9 | 3 |
| Terminal-Bench-Science, Life Sciences | 4 | 4 |
| BixBench3 | 1 | 1 |

[GeneBench-Pro](https://cdn.openai.com/pdf/21938268-21af-442f-af93-3b2249afb241/genebench-pro.pdf)
reports exact efforts in Supplementary Table 1. The saved pairs use the full
129-problem suite, with Sol and Luna at low, medium, high, and max, and Terra
at max. Pro systems and the original GeneBench are kept separate. Its pass
rates exclude execution and format errors and average success over valid
attempts, differing from VEP-bench's treatment of completed invalid answers.

The [Terminal-Bench-Science leaderboard](https://www.terminal-bench-science.ai/?view=domains)
reports a Life Sciences domain with 19 tasks and three trials per task. This
includes medical imaging. The saved pairs match Luna, Terra, and Sol at max
using Codex, and Gemini 3.8 Flash at high using mini-SWE-agent.
[BixBench3](https://github.com/EdisonScientific/BixBench3) matches only Sol at
max; its artifact-paper score covers 20 research workflows.

The [Astra release](https://openai.com/index/gpt-6-astra/), checked on September 17,
reports GeneBench Pro scores of 37.1% for Astra and 32.3% for Sol. The table gives
the best score across efforts without identifying the effort; a footnote calls
the evaluation GeneBench Pro v13. The exact efforts and compatibility with the
paper's evaluation have not been established. The release also reports 64.6%
for Astra on Terminal-Bench Science 0.1, an aggregate science score with no
Life Sciences breakdown or exact effort in the table. These release results
are not added to the saved exact-effort pairs.

## Frozen data and provenance

The plot reads bundled data, so changes to live leaderboards do not alter it.
Source extracts retain retrieval times, original download digests, reported
model settings, and scores. VEP metadata is verified against its publication
manifest and pins the complete 52-panel question set. Missing metadata remains
unknown. The saved survey records the specific reports inspected, without
claiming an exhaustive inventory of evaluations.

```js
display(html`<p>
  <a href=${await FileAttachment("./comparisons-data/paired-scores.csv").url()} download>Paired scores (CSV)</a> ·
  <a href=${await FileAttachment("./comparisons-data/model-matches.json").url()} download>Matching audit (JSON)</a> ·
  <a href=${await FileAttachment("./comparisons-data/external.json").url()} download>Sources, settings, selection rules and survey (JSON)</a> ·
  <a href=${await FileAttachment("./comparisons-data/analysis.json").url()} download>Matched pairs and coverage (JSON)</a> ·
  <a href=${await FileAttachment("./comparisons-data/vep-runs.json").url()} download>VEP run snapshot (JSON)</a>
</p>`);
```

The [matching code](https://github.com/Open-Athena/VEP-bench/blob/main/projects/explorer/web/blog/introducing-vep-bench/comparisons.js)
and [source extraction script](https://github.com/Open-Athena/VEP-bench/blob/main/projects/explorer/scripts/freeze_comparisons.mjs)
are versioned with the post. Raw paired scores for unplotted benchmarks remain
available for inspection, with coverage counts and no between-benchmark
correlation summaries.
