#!/usr/bin/env bash
set -euo pipefail

cluster="vepbench-prepare-chr17"
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
  sky/prepare_vep_consequence.yaml
sky logs "${cluster}"
sky status "${cluster}" >/dev/null

scp \
  "${cluster}:~/sky_workdir/data/sources/chr17-vep-consequences.jsonl" \
  data/sources/chr17-vep-consequences.jsonl
scp \
  "${cluster}:~/sky_workdir/data/sources/chr17-vep-consequences.manifest.json" \
  data/sources/chr17-vep-consequences.manifest.json

uv run --locked python scripts/validate_vep_consequence_artifacts.py
uv run --locked vepbench build --output /tmp/vepbench-questions.jsonl
cmp benchmark/questions.jsonl /tmp/vepbench-questions.jsonl 2>/dev/null || \
  cp /tmp/vepbench-questions.jsonl benchmark/questions.jsonl

sky down --yes "${cluster}"
trap - EXIT
