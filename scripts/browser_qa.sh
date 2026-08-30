#!/usr/bin/env bash

set -euo pipefail

site_dir=${1:?usage: browser_qa.sh SITE_DIR OUTPUT_DIR}
output_dir=${2:?usage: browser_qa.sh SITE_DIR OUTPUT_DIR}
port=4173

mkdir -p "$output_dir"
python3 -m http.server "$port" --bind 127.0.0.1 --directory "$site_dir" \
  >"$output_dir/server.log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT

curl --fail --retry 10 --retry-connrefused --retry-delay 1 \
  "http://127.0.0.1:$port/index.html" >/dev/null

chrome=$(command -v google-chrome || command -v chromium || command -v chromium-browser)
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

status=0
for check in \
  'leaderboard.dom.html|14.7%' \
  'leaderboard.dom.html|Consequence classification' \
  'leaderboard.dom.html|consequence-classification.html?run=gpt-5.6-luna-medium-prompt-v1.1-20260830' \
  'tasks.dom.html|Browse benchmark tasks' \
  'tasks.dom.html|Open task' \
  'task.dom.html|Task version' \
  'task.dom.html|records match the current filters' \
  'task.dom.html|Model-visible prompt' \
  'task.dom.html|&gt;window'
do
  file=${check%%|*}
  pattern=${check#*|}
  if ! grep -q "$pattern" "$output_dir/$file"; then
    echo "missing rendered DOM pattern in $file: $pattern" >&2
    status=1
  fi
done

explorer_data=$(find "$site_dir/_file/data" -maxdepth 1 -name 'explorer.*.json' -print -quit)
if [[ -z "$explorer_data" ]]; then
  echo "could not locate built explorer attachment" >&2
  status=1
else
  cp "$explorer_data" "$output_dir/explorer-with-results.json"
  python3 - "$explorer_data" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
explorer = json.loads(path.read_text(encoding="utf-8"))
explorer["task_runs"] = []
path.write_text(json.dumps(explorer, separators=(",", ":")) + "\n", encoding="utf-8")
PY
  "$chrome" "${common[@]}" --window-size=1440,1600 \
    --dump-dom "http://127.0.0.1:$port/tasks/consequence-classification.html" \
    >"$output_dir/task-no-results.dom.html"
  for pattern in 'No complete current evaluations' 'Not evaluated' 'Model-visible prompt'; do
    if ! grep -q "$pattern" "$output_dir/task-no-results.dom.html"; then
      echo "missing no-results DOM pattern: $pattern" >&2
      status=1
    fi
  done
  if grep -q 'observablehq--error' "$output_dir/task-no-results.dom.html"; then
    echo "rendered Observable error in task-no-results.dom.html" >&2
    status=1
  fi
  cp "$output_dir/explorer-with-results.json" "$explorer_data"
fi

for file in leaderboard.dom.html tasks.dom.html task.dom.html; do
  if grep -q 'observablehq--error' "$output_dir/$file"; then
    echo "rendered Observable error in $file" >&2
    status=1
  fi
done

"$chrome" "${common[@]}" --window-size=1440,1200 \
  --screenshot="$output_dir/leaderboard-desktop.png" \
  "http://127.0.0.1:$port/index.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --screenshot="$output_dir/tasks-desktop.png" \
  "http://127.0.0.1:$port/tasks.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --screenshot="$output_dir/task-desktop.png" \
  "http://127.0.0.1:$port/tasks/consequence-classification.html"
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
