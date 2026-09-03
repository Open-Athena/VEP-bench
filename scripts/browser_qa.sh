#!/usr/bin/env bash

set -euo pipefail

site_dir=${1:?usage: browser_qa.sh SITE_DIR OUTPUT_DIR}
output_dir=${2:?usage: browser_qa.sh SITE_DIR OUTPUT_DIR}
port=${VEPBENCH_BROWSER_QA_PORT:-4173}
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
qa_root=$(mktemp -d)
server_pid=
browser_pid=

# Invoked indirectly by the EXIT trap below.
# shellcheck disable=SC2329
cleanup() {
  if [[ -n "$browser_pid" ]]; then
    kill "$browser_pid" 2>/dev/null || true
    wait "$browser_pid" 2>/dev/null || true
  fi
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -rf -- "$qa_root"
}
trap cleanup EXIT

mkdir -p "$output_dir"
cp -a "$site_dir/." "$qa_root/"

questions="$qa_root/questions.jsonl"
clinvar_questions="$qa_root/clinvar-questions.jsonl"
satmut_questions="$qa_root/satmut-mpra-questions.jsonl"
publication="$qa_root/publication"
data_base_url="http://127.0.0.1:$port/publication/versions/main"
uv run --project "$project_root" --locked vepbench build --output "$questions"
uv run --project "$project_root" --locked vepbench build \
  --source "$project_root/data/sources/clinvar-july-2026.jsonl" \
  --template "$project_root/templates/clinvar.json" \
  --output "$clinvar_questions"
uv run --project "$project_root" --locked vepbench build \
  --source "$project_root/data/sources/satmut-mpra-cadd-v1.7.jsonl" \
  --template "$project_root/templates/satmut_mpra.json" \
  --output "$satmut_questions"
uv run --project "$project_root" --locked python \
  "$project_root/scripts/prepare_browser_qa_fixture.py" \
  --questions "$questions" \
  --questions "$clinvar_questions" \
  --questions "$satmut_questions" \
  --alternate-model \
  --output "$publication" \
  --site-root "$qa_root" \
  --data-base-url "$data_base_url"

uv run --project "$project_root" --locked python -m http.server "$port" \
  --bind 127.0.0.1 --directory "$qa_root" \
  >"$output_dir/server.log" 2>&1 &
server_pid=$!

curl --fail --retry 20 --retry-all-errors --retry-connrefused --retry-delay 1 \
  "http://127.0.0.1:$port/index.html" >/dev/null

chrome=$(
  command -v google-chrome \
    || command -v chromium \
    || command -v chromium-browser \
    || command -v chrome-headless-shell \
    || true
)
if [[ -z "$chrome" ]]; then
  echo "browser QA requires google-chrome, chromium, chromium-browser, or chrome-headless-shell on PATH" >&2
  exit 1
fi
common=(
  --headless
  --no-sandbox
  --disable-gpu
  --hide-scrollbars
  --virtual-time-budget=5000
)

"$chrome" "${common[@]}" --window-size=1440,1200 \
  --dump-dom "http://127.0.0.1:$port/index.html" \
  >"$output_dir/leaderboard.dom.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --dump-dom "http://127.0.0.1:$port/tasks.html" \
  >"$output_dir/tasks.dom.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --dump-dom "http://127.0.0.1:$port/tasks/consequence-classification.html" \
  >"$output_dir/task.dom.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --dump-dom "http://127.0.0.1:$port/tasks/clinvar.html" \
  >"$output_dir/clinvar-task.dom.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --dump-dom "http://127.0.0.1:$port/tasks/satmut-mpra.html" \
  >"$output_dir/satmut-task.dom.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --dump-dom "http://127.0.0.1:$port/questions.html?question=vep-most-severe-v1%3A17%3A38786886%3AA%3AT&run=browser-qa" \
  >"$output_dir/question.dom.html"
"$chrome" "${common[@]}" --window-size=1440,2400 \
  --dump-dom "http://127.0.0.1:$port/questions.html?question=satmut-mpra-ranking-v1%3AF9&run=browser-qa" \
  >"$output_dir/ranking-question.dom.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --dump-dom "http://127.0.0.1:$port/questions.html?run=missing-run" \
  >"$output_dir/question-neutral.dom.html"

status=0
for check in \
  'leaderboard.dom.html|>Leaderboard<' \
  'leaderboard.dom.html|>Model<' \
  'leaderboard.dom.html|>Release date<' \
  'leaderboard.dom.html|>Tokens<' \
  'leaderboard.dom.html|>Cost<' \
  'leaderboard.dom.html|>Score<' \
  'leaderboard.dom.html|class="vepbench-score-cell"' \
  'leaderboard.dom.html|class="vepbench-score-bar" aria-hidden="true"' \
  'leaderboard.dom.html|>Task</label>' \
  'leaderboard.dom.html|>All classification tasks</option>' \
  'leaderboard.dom.html|>Consequence classification</option>' \
  'leaderboard.dom.html|>ClinVar</option>' \
  'leaderboard.dom.html|>satMutMPRA ranking</option>' \
  'leaderboard.dom.html|Score by cost and token usage' \
  'leaderboard.dom.html|>Compare score against</label>' \
  'leaderboard.dom.html|>Total cost</option>' \
  'leaderboard.dom.html|https://github.com/Open-Athena/VEPBench' \
  'leaderboard.dom.html|View source' \
  'tasks.dom.html|Browse benchmark tasks' \
  'tasks.dom.html|Open task' \
  'task.dom.html|Leaderboard' \
  'task.dom.html|Task version' \
  'task.dom.html|questions match the current filters' \
  'clinvar-task.dom.html|>Q052<' \
  'satmut-task.dom.html|>Q094<' \
  'satmut-task.dom.html|published element panels' \
  'question.dom.html|>Questions<' \
  'question.dom.html|browser-qa' \
  'question.dom.html|>Result<' \
  'question.dom.html|>Consequence / element</label>' \
  'question.dom.html|>All consequences</option>' \
  'question.dom.html|<th title="consequence"><span></span>Consequence / element</th><th title="outcome"><span></span>Result</th>' \
  'question.dom.html|>All results</option>' \
  'question.dom.html|>Correct</option>' \
  'question.dom.html|>Incorrect</option>' \
  'question.dom.html|>Refusal</option>' \
  'question.dom.html|>Token limit</option>' \
  'question.dom.html|>Format error</option>' \
  'question.dom.html|Reference answer: C13' \
  'question.dom.html|VEP consequence: start_lost' \
  'question.dom.html|Parsed prediction: C17' \
  'question.dom.html|<td><span class="vepbench-outcome-badge vepbench-outcome-correct">Correct</span></td>' \
  'question.dom.html|<td><span class="vepbench-outcome-badge vepbench-outcome-incorrect">Incorrect</span></td>' \
  'question.dom.html|>Reasoning<' \
  'ranking-question.dom.html|Reference panel: 50 measured effects' \
  'ranking-question.dom.html|>Reference effects<' \
  'ranking-question.dom.html|>Measured effect<' \
  'ranking-question.dom.html|>Predicted effect<' \
  'ranking-question.dom.html|Spearman ρ:' \
  'ranking-question.dom.html|50 numeric predictions' \
  'question-neutral.dom.html|Unavailable model for run · missing-run' \
  'question-neutral.dom.html|<td><span>Not evaluated</span></td>' \
  "question.dom.html|href=\"http://127.0.0.1:$port/publication/versions/main/raw/browser-qa.jsonl.zst\""
do
  file=${check%%|*}
  pattern=${check#*|}
  if ! grep -q "$pattern" "$output_dir/$file"; then
    echo "missing rendered DOM pattern in $file: $pattern" >&2
    status=1
  fi
done

if grep -q '<th title="answer"' "$output_dir/question.dom.html"; then
  echo "unexpected Reference answer column in question.dom.html" >&2
  status=1
fi

if grep -q '>Q001<' "$output_dir/clinvar-task.dom.html"; then
  echo "unexpected task-local numbering in clinvar-task.dom.html" >&2
  status=1
fi

if grep -Pzoq '<div class="card">(?:\s|<!--[^>]*-->)*</div>' \
  "$output_dir/question.dom.html"; then
  echo "unexpected empty card in question.dom.html" >&2
  status=1
fi

header_order=$(
  { grep -o '<th title="[^"]*"><span>[^<]*</span>[^<]*</th>' "$output_dir/leaderboard.dom.html" || true; } \
    | head -5 \
    | sed -E 's/<span>[^<]*<\/span>//; s/<[^>]+>//g' \
    | paste -sd '|' -
)
if [[ "$header_order" != 'Model|Score|Release date|Tokens|Cost' ]]; then
  echo "unexpected leaderboard column order: $header_order" >&2
  status=1
fi

if grep -q '<th title="provider"' "$output_dir/leaderboard.dom.html"; then
  echo "unexpected Provider column in leaderboard.dom.html" >&2
  status=1
fi

if grep -q 'Overall score' "$output_dir/leaderboard.dom.html"; then
  echo "unexpected Overall score label in leaderboard.dom.html" >&2
  status=1
fi

if grep -q '>Correct<' "$output_dir/leaderboard.dom.html"; then
  echo "unexpected Correct column in leaderboard.dom.html" >&2
  status=1
fi

if grep -q '<th title="task"' "$output_dir/leaderboard.dom.html"; then
  echo "unexpected Task column in leaderboard.dom.html" >&2
  status=1
fi

if grep -q '>Rank<' "$output_dir/leaderboard.dom.html"; then
  echo "unexpected Rank column in leaderboard.dom.html" >&2
  status=1
fi

if grep -q 'Model performance across all VEPBench tasks' "$output_dir/leaderboard.dom.html"; then
  echo "unexpected leaderboard explainer in leaderboard.dom.html" >&2
  status=1
fi

for pattern in 'Evaluation run' 'Model prediction' '>Outcome<'; do
  if grep -q "$pattern" "$output_dir/task.dom.html"; then
    echo "unexpected response-oriented task detail in task.dom.html: $pattern" >&2
    status=1
  fi
done

for file in leaderboard.dom.html tasks.dom.html task.dom.html clinvar-task.dom.html satmut-task.dom.html question.dom.html ranking-question.dom.html; do
  if grep -q 'observablehq--error' "$output_dir/$file"; then
    echo "rendered Observable error in $file" >&2
    status=1
  fi
done

for file in leaderboard.dom.html task.dom.html question.dom.html; do
  for pattern in 'API errors' 'errors remain unscored'; do
    if grep -q "$pattern" "$output_dir/$file"; then
      echo "unexpected operational detail in $file: $pattern" >&2
      status=1
    fi
  done
done

if grep -q '>null<' "$output_dir/question.dom.html"; then
  echo "unexpected null placeholder in question.dom.html" >&2
  status=1
fi

if grep -q 'Question only' "$output_dir/question.dom.html"; then
  echo "unexpected Question only mode in question.dom.html" >&2
  status=1
fi

for pattern in \
  'observablehq--block"><div class="note" label="Published data unavailable"' \
  'observablehq--block"><div class="note" label="Response unavailable"' \
  '>No complete evaluation runs available</option>'
do
  if grep -q "$pattern" "$output_dir/question.dom.html"; then
    echo "unexpected missing-data state in question.dom.html: $pattern" >&2
    status=1
  fi
done

for pattern in 'Raw provider response' 'Request and usage metadata'; do
  if grep -q "$pattern" "$output_dir/question.dom.html"; then
    echo "unexpected technical disclosure in question.dom.html: $pattern" >&2
    status=1
  fi
done

debug_port=${VEPBENCH_BROWSER_QA_DEBUG_PORT:-$((port + 1))}
"$chrome" \
  --headless \
  --no-sandbox \
  --disable-gpu \
  --hide-scrollbars \
  --remote-debugging-port="$debug_port" \
  --remote-allow-origins='*' \
  --user-data-dir="$qa_root/chrome-profile" \
  about:blank \
  >"$output_dir/interaction-browser.log" 2>&1 &
browser_pid=$!
curl --fail --retry 20 --retry-all-errors --retry-connrefused --retry-delay 1 \
  "http://127.0.0.1:$debug_port/json/version" >/dev/null
node "$project_root/scripts/browser_interaction_qa.mjs" \
  "http://127.0.0.1:$port" \
  "http://127.0.0.1:$debug_port"
kill "$browser_pid" 2>/dev/null || true
wait "$browser_pid" 2>/dev/null || true
browser_pid=

"$chrome" "${common[@]}" --window-size=1440,1200 \
  --screenshot="$output_dir/leaderboard-desktop.png" \
  "http://127.0.0.1:$port/index.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --screenshot="$output_dir/tasks-desktop.png" \
  "http://127.0.0.1:$port/tasks.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --screenshot="$output_dir/task-desktop.png" \
  "http://127.0.0.1:$port/tasks/consequence-classification.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --screenshot="$output_dir/question-desktop.png" \
  "http://127.0.0.1:$port/questions.html?question=vep-most-severe-v1%3A17%3A38786886%3AA%3AT&run=browser-qa"
"$chrome" "${common[@]}" --window-size=390,844 \
  --screenshot="$output_dir/task-mobile.png" \
  "http://127.0.0.1:$port/tasks/consequence-classification.html"
"$chrome" "${common[@]}" --force-dark-mode --window-size=1440,1200 \
  --screenshot="$output_dir/leaderboard-dark.png" \
  "http://127.0.0.1:$port/index.html"
"$chrome" "${common[@]}" --force-dark-mode --window-size=1440,1600 \
  --screenshot="$output_dir/task-dark.png" \
  "http://127.0.0.1:$port/tasks/consequence-classification.html"

exit "$status"
