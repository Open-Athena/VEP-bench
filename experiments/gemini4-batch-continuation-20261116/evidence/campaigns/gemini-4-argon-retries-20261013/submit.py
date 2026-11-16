"""Preserve full question identity while submitting authorized selective retries."""
import hashlib
import json
import os
import sys
import urllib.request
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from vepbench.config import load_task_profile
from vepbench.evaluation.batch import submit_batch_file
from vepbench.resources import QUESTION_SCHEMA

kind = sys.argv[1]
assert kind in {"truncation", "safety"}
root = Path(__file__).resolve().parent / kind
root.mkdir(parents=True, exist_ok=True)
assert not (root / "batch-state.json").exists(), "Inspect prior state before resubmission"
original = Path(".vepbench/campaigns/gemini-4-argon-efforts-20261013/high/results.jsonl")
rows = [json.loads(line) for line in original.open()]
assert len(rows) == 52
parameters = rows[0]["generation_parameters"]
assert all(row["generation_parameters"] == parameters for row in rows)
assert parameters == {"max_tokens": 128000, "reasoning": {"effort": "high", "exclude": False}}
if kind == "truncation":
    selected = [r for r in rows if r["response"]["status"] == "completed"
                and r["response"]["finish_reason"] == "length" and not r["scoring"]["valid"]]
    assert len(selected) == 27
    tasks = [load_task_profile(f"configs/tasks/{slug}/task-262k.yaml")
             for slug in ["satmut-mpra", "opensplice-snv", "sge"]]
    assert all(task.generation_parameters == {"max_tokens": 262144} for task in tasks)
    parameters = {**parameters, **tasks[0].generation_parameters}
else:
    selected = [r for r in rows if r["response"]["status"] == "api_error"]
    assert len(selected) == 2
    assert all(r["error"]["message"] == "Gemini blocked the request: SAFETY" for r in selected)
questions = Path(".vepbench/questions/main-be62fd7b5f06.jsonl")
assert hashlib.sha256(questions.read_bytes()).hexdigest() == rows[0]["question_set_sha256"]
key = os.environ["OPENROUTER_API_KEY"]

def get(url, authenticated=False):
    headers = {"Authorization": "Bearer " + key} if authenticated else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return json.load(response)

account = get("https://openrouter.ai/api/v1/key", True)["data"]
assert account.get("organization_id") == "org_3K0wHyi4NHzEMXug2Tdx8K77aBB"
batch = next(m for m in get("https://openrouter.ai/api/v1/models")["data"]
             if m["id"] == "google/gemini-4-argon:batch")
assert float(batch["pricing"]["prompt"]) <= 0.000001
assert float(batch["pricing"]["completion"]) <= 0.000005
assert batch["top_provider"]["max_completion_tokens"] >= parameters["max_tokens"]
bound = len(selected) * parameters["max_tokens"] * 0.000005
bound += sum(len(r["question"]["prompt"].encode()) + 1024 for r in selected) * 0.000001
plan = {
    "checked_at": datetime.now(UTC).isoformat(), "kind": kind,
    "original_path": str(original), "original_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
    "selected_question_ids": [r["question_id"] for r in selected],
    "generation_parameters": parameters, "batch_model": batch,
    "conservative_request_cost_ceiling_usd": bound,
    "organization_association_verified": True,
}
(root / "preflight.json").write_text(json.dumps(plan, indent=2) + "\n")
summary = submit_batch_file(
    questions_path=questions, question_schema_path=QUESTION_SCHEMA,
    state_path=root / "batch-state.json", result_output=root / "results.jsonl",
    run_id=f"gemini-4-argon-high-{kind}-retry-20261013",
    model_id="google/gemini-4-argon", api_key=key,
    generation_parameters=parameters, selected_question_ids=plan["selected_question_ids"],
)
print(json.dumps({**asdict(summary), "cost_ceiling_usd": bound}, default=str, indent=2))
