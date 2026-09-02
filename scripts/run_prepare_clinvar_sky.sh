#!/usr/bin/env bash
set -euo pipefail

cluster="vepbench-prepare-clinvar"
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
  sky/prepare_clinvar.yaml
sky logs "${cluster}"
sky status "${cluster}" >/dev/null

scp \
  "${cluster}:~/sky_workdir/data/sources/clinvar-july-2026.jsonl" \
  data/sources/clinvar-july-2026.jsonl
scp \
  "${cluster}:~/sky_workdir/data/sources/clinvar-july-2026.manifest.json" \
  data/sources/clinvar-july-2026.manifest.json

uv run --locked python scripts/validate_clinvar_artifacts.py
uv run --locked vepbench build \
  --source data/sources/clinvar-july-2026.jsonl \
  --template templates/clinvar.json \
  --output .vepbench/clinvar-questions.jsonl

sky down --yes "${cluster}"
trap - EXIT
