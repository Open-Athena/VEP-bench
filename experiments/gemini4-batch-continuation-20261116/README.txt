Gemini 4 batch continuation experiment — archive handoff, 2026-11-16
Tracking issue: https://github.com/Open-Athena/VEP-bench/issues/116

CURRENT STATE
All submitted runs are terminal. The latest Argon F9 batch completed before cancellation was needed. It reproduced the failure: 32768 output tokens, native CONTINUATION, no continuation token, invalid/empty final answer, cost $0.164983. No cancellation was performed and no automatic monitoring worker remains.

Carbon high batch has the same missing-token failure. Hydrogen high batch completed F9 normally at 38470 output tokens; it did not test continuation replay. Argon direct continuation was verified with a valid answer at 94489 tokens. Full low and medium batch evaluations each produced 52/52 valid answers, costing $0.800135 and $0.913880. Task-level scores are in the corresponding summary.json files below.

CONTENTS
- evidence/: snapshot of session-local .vepbench artifacts, including full question snapshots, result records, raw provider receipts, launcher scripts, model metadata, diagnostics, and historical handoffs.
- export-manifest.json: original and exported SHA256 for every file, plus whether opaque continuation state was redacted.
- export_evidence.py: reproducible export/redaction procedure.
- Branch changes in src/vepbench/evaluation/batch.py and tests/test_batch.py retain full question-set identity when submitting selected question IDs.
- Branch model profiles and task-262k.yaml descriptors retain inference settings.

The evidence is an experimental archive, not a publication of official benchmark results. Preserve the failed/truncated attempts. Do not interpret parser-zero scores from incomplete responses as scientific capability scores. Some historical plans and handoffs describe earlier pending states; this file supersedes their operational status.

RESTORING
Clone this experimental branch. To reuse existing local commands, copy evidence/ into .vepbench/ in the clone. Some saved state files contain absolute result_output paths to the old worktree: adapt a working copy to the new checkout before collection. Do not resubmit completed batches. Existing results are already collected.

Redacted request/response files cannot be used to replay old continuation tokens. Their historical internal sha256 manifests describe the original files; use export-manifest.json to verify the exported copies. Exact unredacted originals are preserved privately on the VM at:
/home/exedev/.local/share/vepbench/archives/gemini4-session-20261116/raw-evidence.tar
The adjacent SHA256SUMS verifies that archive, and tar --compare passed before archiving. This directory is outside the worktree and must be retained. Credentials are not included. No external backup of this private VM archive is claimed.

VALIDATION
Before evidence export: 40 targeted batch/model-profile/task-profile tests passed; uv run --locked ruff check . passed. The archive exporter verifies credential-pattern absence and records original/export hashes. Changes were not merged into the default branch.

NEXT ACTIONS
- Upstream support ticket is NOT filed. Issue #116 holds the project blocker; the raw examples and batch IDs support an OpenRouter ticket.
- Determine whether stable /api/v1/batches submission differs from the current /api/beta/batches path; no fix is assumed.
- Carbon Flex is an untested possible route at advertised batch-equivalent prices; no Flex test or full Carbon/Hydrogen/high-direct campaign is authorized by this archival action.
- No new model calls are needed to restore or inspect these artifacts.
