#!/usr/bin/env bash

set -euo pipefail

site_url=${1:?usage: browser_live_canary.sh SITE_URL OUTPUT_DIR}
output_dir=${2:?usage: browser_live_canary.sh SITE_URL OUTPUT_DIR}
site_url=${site_url%/}

mkdir -p "$output_dir"
curl --fail --location --retry 3 --retry-delay 2 "$site_url/index.html" >/dev/null

chrome=$(command -v google-chrome || command -v chromium || command -v chromium-browser)
common=(
  --headless
  --no-sandbox
  --disable-gpu
  --hide-scrollbars
  --virtual-time-budget=8000
)

"$chrome" "${common[@]}" --window-size=1440,1200 \
  --dump-dom "$site_url/index.html" \
  >"$output_dir/leaderboard.dom.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --dump-dom "$site_url/tasks/satmut-mpra.html" \
  >"$output_dir/task.dom.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --dump-dom "$site_url/questions.html" \
  >"$output_dir/question.dom.html"

status=0
for check in \
  'leaderboard.dom.html|>Leaderboard<' \
  'leaderboard.dom.html|>Model<' \
  'leaderboard.dom.html|>Score<' \
  'task.dom.html|>satMutMPRA</a></h1>' \
  'task.dom.html|>Task version<' \
  'task.dom.html|element panels match the current filter' \
  'question.dom.html|>Questions<' \
  'question.dom.html|>Prompt given to model<' \
  'question.dom.html|Reference panel: 50 candidate variants' \
  'question.dom.html|Spearman ρ:' \
  'question.dom.html|>Reasoning<'
do
  file=${check%%|*}
  pattern=${check#*|}
  if ! grep -q "$pattern" "$output_dir/$file"; then
    echo "missing live DOM pattern in $file: $pattern" >&2
    status=1
  fi
done

for file in leaderboard.dom.html task.dom.html question.dom.html; do
  for pattern in \
    'observablehq--error' \
    'observablehq--block"><div class="note" label="Published data unavailable"' \
    'observablehq--block"><div class="note" label="Response unavailable"' \
    '>No complete evaluation runs available</option>'
  do
    if grep -q "$pattern" "$output_dir/$file"; then
      echo "unexpected live explorer state in $file: $pattern" >&2
      status=1
    fi
  done
done

"$chrome" "${common[@]}" --window-size=1440,1200 \
  --screenshot="$output_dir/leaderboard.png" \
  "$site_url/index.html"
"$chrome" "${common[@]}" --window-size=1440,1600 \
  --screenshot="$output_dir/question.png" \
  "$site_url/questions.html"

exit "$status"
