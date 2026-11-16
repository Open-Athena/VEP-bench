"""Submit the two remaining tasks together with verified common settings."""
import json
import os
import urllib.request
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from vepbench.evaluation.batch import submit_batch_file
from vepbench.resources import QUESTION_SCHEMA

root = Path(__file__).resolve().parent
plan = json.loads((root / "plan.json").read_text())
key = os.environ["OPENROUTER_API_KEY"]

def get(url, authenticated=False):
    headers = {"Authorization": "Bearer " + key} if authenticated else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return json.load(response)

account = get("https://openrouter.ai/api/v1/key", True)["data"]
assert account.get("organization_id") == "org_3K0wHyi4NHzEMXug2Tdx8K77aBB"
models = get("https://openrouter.ai/api/v1/models")["data"]
batch = next(model for model in models if model["id"] == plan["model"] + ":batch")
assert float(batch["pricing"]["prompt"]) <= 0.000001
assert float(batch["pricing"]["completion"]) <= 0.000005
assert batch["top_provider"]["max_completion_tokens"] >= 128000
assert "medium" in batch["reasoning"]["supported_efforts"]
(root / "preflight.json").write_text(json.dumps({
    "checked_at": datetime.now(UTC).isoformat(),
    "organization_association_verified": True,
    "batch_model": batch,
    "plan": plan,
}, indent=2) + "\n")

summary = submit_batch_file(
    questions_path=plan["questions_path"],
    question_schema_path=QUESTION_SCHEMA,
    state_path=root / "batch-state.json",
    result_output=root / "results.jsonl",
    run_id=plan["run_id"],
    model_id=plan["model"],
    api_key=key,
    generation_parameters=plan["generation_parameters"],
)
print(json.dumps(asdict(summary), default=str, indent=2))
