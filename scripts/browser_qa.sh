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

questions="$qa_root/satmut-mpra-questions.jsonl"
publication="$qa_root/publication"
data_base_url="http://127.0.0.1:$port/publication/versions/main"
uv run --project "$project_root" --locked vepbench build --output "$questions"
uv run --project "$project_root" --locked python \
  "$project_root/scripts/prepare_browser_qa_fixture.py" \
  --questions "$questions" \
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
  --dump-dom "http://127.0.0.1:$port/tasks/satmut-mpra.html" \
  >"$output_dir/task.dom.html"
"$chrome" "${common[@]}" --window-size=1440,2400 \
  --dump-dom "http://127.0.0.1:$port/tasks/satmut-mpra.html?question=satmut-mpra-ranking-v1%3AF9&run=browser-qa" \
  >"$output_dir/question.dom.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --dump-dom "http://127.0.0.1:$port/tasks/satmut-mpra.html?run=missing-run" \
  >"$output_dir/question-neutral.dom.html"

status=0
for check in \
  'leaderboard.dom.html|>Leaderboard<' \
  'leaderboard.dom.html|>Model<' \
  'leaderboard.dom.html|>Task</label>' \
  'leaderboard.dom.html|class="vepbench-score-cell"' \
  'leaderboard.dom.html|Score by cost and token usage' \
  'leaderboard.dom.html|>Compare score against</label>' \
  'leaderboard.dom.html|https://github.com/Open-Athena/VEP-bench' \
  'tasks.dom.html|>satMutMPRA<' \
  'tasks.dom.html|Open task' \
  'task.dom.html|>satMutMPRA</a></h1>' \
  'task.dom.html|>Task version<' \
  'task.dom.html|published element panels' \
  'question.dom.html|>Prompt given to model<' \
  'question.dom.html|Complete mutagenized insert (reporter-construct orientation)' \
  'question.dom.html|Sequence lines contain 80 bases except the final line.' \
  'question.dom.html|#CHROM' \
  'question.dom.html|Reference panel: 50 candidate variants' \
  'question.dom.html|Spearman ρ:' \
  'question.dom.html|50 numeric predictions' \
  'question.dom.html|>Reasoning<' \
  'question.dom.html|>Element</label>' \
  'question.dom.html|>Valid prediction</option>' \
  'question.dom.html|>Format failure</option>' \
  'question-neutral.dom.html|Unavailable model for run · missing-run' \
  "question.dom.html|href=\"http://127.0.0.1:$port/publication/versions/main/raw/browser-qa.jsonl.zst\""
do
  file=${check%%|*}
  pattern=${check#*|}
  if ! grep -q "$pattern" "$output_dir/$file"; then
    echo "missing rendered DOM pattern in $file: $pattern" >&2
    status=1
  fi
done

for pattern in 'Reference effects' 'Measured effect' 'Predicted effect' \
  'Consequence classification' 'ClinVar' 'satMutMPRA ranking'; do
  if grep -q "$pattern" "$output_dir/question.dom.html" \
    || grep -q "$pattern" "$output_dir/leaderboard.dom.html" \
    || grep -q "$pattern" "$output_dir/tasks.dom.html"; then
    echo "unexpected removed task or reference-effect UI: $pattern" >&2
    status=1
  fi
done

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

for file in leaderboard.dom.html tasks.dom.html task.dom.html question.dom.html; do
  if grep -q 'observablehq--error' "$output_dir/$file"; then
    echo "rendered Observable error in $file" >&2
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
  --screenshot="$output_dir/question-desktop.png" \
  "http://127.0.0.1:$port/tasks/satmut-mpra.html?question=satmut-mpra-ranking-v1%3AF9&run=browser-qa"
"$chrome" "${common[@]}" --window-size=390,844 \
  --screenshot="$output_dir/task-mobile.png" \
  "http://127.0.0.1:$port/tasks/satmut-mpra.html"

exit "$status"
