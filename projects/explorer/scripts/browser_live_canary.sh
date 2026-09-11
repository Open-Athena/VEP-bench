#!/usr/bin/env bash

set -euo pipefail

site_url=${1:?usage: browser_live_canary.sh SITE_URL OUTPUT_DIR}
output_dir=${2:?usage: browser_live_canary.sh SITE_URL OUTPUT_DIR}
site_url=${site_url%/}

mkdir -p "$output_dir"
curl --fail --location --retry 3 --retry-delay 2 "$site_url/index.html" >/dev/null

chrome=$(command -v google-chrome || command -v chromium || command -v chromium-browser)
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
browser_profile=$(mktemp -d)
browser_pid=

# shellcheck disable=SC2329
cleanup() {
  if [[ -n "$browser_pid" ]]; then
    kill "$browser_pid" 2>/dev/null || true
    wait "$browser_pid" 2>/dev/null || true
  fi
  rm -rf -- "$browser_profile"
}
trap cleanup EXIT

# A virtual-time DOM dump can precede ResizeObserver callbacks. Capture through
# the existing interaction harness after actual chart elements are present.
"$chrome" --headless --no-sandbox --disable-gpu --hide-scrollbars \
  --remote-debugging-port=0 --remote-allow-origins='*' \
  --user-data-dir="$browser_profile" about:blank \
  >"$output_dir/browser.log" 2>&1 &
browser_pid=$!
for _attempt in {1..300}; do
  [[ -s "$browser_profile/DevToolsActivePort" ]] && break
  kill -0 "$browser_pid" 2>/dev/null || { echo "Canary browser exited before startup" >&2; exit 1; }
  sleep 0.1
done
if [[ ! -s "$browser_profile/DevToolsActivePort" ]]; then
  echo "Canary browser did not initialize remote debugging within 30 seconds" >&2
  tail -n 40 "$output_dir/browser.log" >&2
  exit 1
fi
read -r debug_port < "$browser_profile/DevToolsActivePort"
node "$script_dir/browser_interaction_qa.mjs" \
  "$site_url" "http://127.0.0.1:$debug_port" "$output_dir" --canary

status=0
for check in \
  'leaderboard.dom.html|>Leaderboard<' \
  'leaderboard.dom.html|vepbench-leaderboard-chart' \
  'leaderboard.dom.html|aria-label="bar"' \
  'leaderboard.dom.html|>Score by cost<' \
  'leaderboard.dom.html|>Score by token usage<' \
  'leaderboard.dom.html|aria-label="Model family legend"' \
  'task.dom.html|>Expression (satMutMPRA)</a></h1>' \
  'task.dom.html|>Questions<' \
  'task.dom.html|questions match the current filters' \
  'task.dom.html|>Prompt given to model<' \
  'task.dom.html|Reference panel: 50 candidate variants' \
  'task.dom.html|Spearman ρ:' \
  'task.dom.html|>Reasoning<'
do
  file=${check%%|*}
  pattern=${check#*|}
  if ! grep -q "$pattern" "$output_dir/$file"; then
    echo "missing live DOM pattern in $file: $pattern" >&2
    status=1
  fi
done

for file in leaderboard.dom.html task.dom.html; do
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

exit "$status"
