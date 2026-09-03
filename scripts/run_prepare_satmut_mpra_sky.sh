#!/usr/bin/env bash
set -euo pipefail

cluster="vepbench-prepare-satmut-mpra"
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
hf_token_file="${HF_HOME:-${HOME}/.cache/huggingface}/token"

if [[ -z "${HF_TOKEN:-}" && -r "${hf_token_file}" ]]; then
  HF_TOKEN="$(<"${hf_token_file}")"
  export HF_TOKEN
fi

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "HF_TOKEN is not set and no cached Hugging Face token was found" >&2
  exit 1
fi

cleanup() {
  sky down --yes "${cluster}" || true
}
trap cleanup EXIT

cd "${repository_root}"
sky launch \
  --cluster "${cluster}" \
  --detach-run \
  --idle-minutes-to-autostop 10 \
  --down \
  --yes \
  --secret HF_TOKEN \
  sky/prepare_satmut_mpra.yaml
sky logs "${cluster}"
sky status "${cluster}" >/dev/null

scp \
  "${cluster}:~/sky_workdir/data/sources/satmut-mpra-cadd-v1.7.jsonl" \
  data/sources/satmut-mpra-cadd-v1.7.jsonl
scp \
  "${cluster}:~/sky_workdir/data/sources/satmut-mpra-cadd-v1.7.manifest.json" \
  data/sources/satmut-mpra-cadd-v1.7.manifest.json

uv run --locked --package vepbench-task-satmut-mpra \
  vepbench-satmut-mpra validate
uv run --locked --package vepbench vepbench questions build \
  --task configs/tasks/satmut-mpra/task.yaml \
  --output .vepbench/satmut-mpra-questions.jsonl

sky down --yes "${cluster}"
trap - EXIT
