"""Submit each requested effort once, with all three tasks in each batch."""
import hashlib
import json
import os
import sys
import urllib.request
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from vepbench.config import load_model_profile, load_task_profile
from vepbench.evaluation.batch import submit_batch_file
from vepbench.resources import QUESTION_SCHEMA

effort = sys.argv[1]
assert effort in {"low", "high"}
root = Path(__file__).resolve().parent / effort
root.mkdir(parents=True, exist_ok=True)
assert not (root / "batch-state.json").exists(), "Inspect existing state before any resubmission"
questions = Path(".vepbench/questions/main-be62fd7b5f06.jsonl")
payload = questions.read_bytes()
digest = hashlib.sha256(payload).hexdigest()
assert digest == "be62fd7b5f06656572a5150904758f04bb998590f63ce31ebbca13852a0bfab7"
records = [json.loads(line) for line in payload.splitlines()]
assert len(records) == 52
groups = {}
for record in records:
    groups.setdefault(record["metadata"]["task_family"], []).append(record)
assert {key: len(value) for key, value in groups.items()} == {
    "satmut_mpra": 16, "opensplice_snv": 20, "sge": 16,
}
task_parameters = [load_task_profile(f"configs/tasks/{slug}/task.yaml").generation_parameters
                   for slug in ["satmut-mpra", "opensplice-snv", "sge"]]
assert all(value == task_parameters[0] for value in task_parameters)
profile = load_model_profile(f"configs/models/google-gemini-4-argon-{effort}.yaml")
assert not (profile.generation_parameters.keys() & task_parameters[0].keys())
parameters = {**task_parameters[0], **profile.generation_parameters}
key = os.environ["OPENROUTER_API_KEY"]

def get(url, authenticated=False):
    headers = {"Authorization": "Bearer " + key} if authenticated else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return json.load(response)

account = get("https://openrouter.ai/api/v1/key", True)["data"]
assert account.get("organization_id") == "org_3K0wHyi4NHzEMXug2Tdx8K77aBB"
models = get("https://openrouter.ai/api/v1/models")["data"]
batch = next(model for model in models if model["id"] == profile.model_id + ":batch")
assert float(batch["pricing"]["prompt"]) <= 0.000001
assert float(batch["pricing"]["completion"]) <= 0.000005
assert batch["top_provider"]["max_completion_tokens"] >= parameters["max_tokens"]
assert effort in batch["reasoning"]["supported_efforts"]
input_bound = sum(len(record["prompt"].encode()) + 1024 for record in records)
cost_bound = 52 * parameters["max_tokens"] * 0.000005 + input_bound * 0.000001
assert cost_bound < 34
plan = {
    "checked_at": datetime.now(UTC).isoformat(),
    "organization_association_verified": True,
    "batch_model": batch,
    "generation_parameters": parameters,
    "question_set_sha256": digest,
    "counts": {key: len(value) for key, value in groups.items()},
    "conservative_request_cost_ceiling_usd": cost_bound,
    "budget_usd": 34,
    "model_profile_sha256": profile.content_sha256,
}
(root / "preflight.json").write_text(json.dumps(plan, indent=2) + "\n")
summary = submit_batch_file(
    questions_path=questions,
    question_schema_path=QUESTION_SCHEMA,
    state_path=root / "batch-state.json",
    result_output=root / "results.jsonl",
    run_id=f"gemini-4-argon-{effort}-all-tasks-20261013",
    model_id=profile.model_id,
    api_key=key,
    generation_parameters=parameters,
)
print(json.dumps({**asdict(summary), "cost_ceiling_usd": cost_bound}, default=str, indent=2))
