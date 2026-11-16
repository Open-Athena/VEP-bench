"""Bounded, explicitly authorized diagnostic; no benchmark result rewriting."""
import copy
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "vertex-262k-20261030"
OUT.mkdir(mode=0o700, exist_ok=False)
os.umask(0o077)
KEY = os.environ["OPENROUTER_API_KEY"]
plan = json.loads((ROOT / "direct-probe-plan.json").read_text())
initial = json.loads((ROOT / "direct-probe-request.json").read_text())
initial["max_tokens"] = 262144
initial["provider"]["only"] = ["google-vertex/global"]
plan.update(total_output_budget=262144, max_hops=8, cost_ceiling_usd=4.830708, prior_diagnostic_cost_usd=1.169292)

def save(name, value):
    with (OUT / name).open("x") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

def emit(value):
    print(json.dumps(value), flush=True)

def get(url, authenticated=False):
    headers = {"Authorization": "Bearer " + KEY} if authenticated else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return json.load(r)

account = get("https://openrouter.ai/api/v1/key", True)["data"]
assert account.get("organization_id") == "org_3K0wHyi4NHzEMXug2Tdx8K77aBB"
metadata = get("https://openrouter.ai/api/v1/models/google/gemini-4-argon/endpoints")
endpoint = next(e for e in metadata["data"]["endpoints"] if e["tag"] == "google-vertex/global")
assert float(endpoint["pricing"]["prompt"]) <= 0.000002
assert float(endpoint["pricing"]["completion"]) <= 0.00001
assert endpoint["context_length"] <= 1048576
assert endpoint["max_completion_tokens"] >= initial["max_tokens"]
save("preflight.json", {"checked_at": datetime.now(UTC).isoformat(), "endpoint": endpoint,
                        "organization_verified": True, "plan": plan})

# Monitor only this diagnostic. A stop closes its active connection; never resubmit
# automatically after a disconnect, because its upstream billing state is unknown.
finished = threading.Event()
started = time.monotonic()
def monitor():
    while not finished.wait(10 if time.monotonic() - started < 60 else 300):
        available = next(int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
                         if line.startswith("MemAvailable:"))
        load = os.getloadavg()[0]
        if available < 2097152 or load > 2.5:
            save("resource-stop.json", {"available_kib": available, "load": load})
            emit({"status": "resource_stop", "upstream_request_may_still_be_running": True})
            os._exit(75)

threading.Thread(target=monitor, daemon=True).start()
request_body = copy.deepcopy(initial)
total_output = 0
total_cost = 0.0
summaries = []
try:
    for hop in range(1, 9):
        # Reserve a full provider context window per input, without assuming cache
        # discounts, plus all remaining output; this also covers hidden state.
        remaining_output = 262144 - total_output
        reservation = 1048576 * 0.000002 + remaining_output * 0.00001
        if total_cost + reservation > 4.830708:
            emit({"status": "budget_stop", "spent": total_cost})
            break
        save(f"request-{hop}.json", request_body)
        emit({"hop": hop, "status": "request_started", "at": datetime.now(UTC).isoformat()})
        req = urllib.request.Request(
            plan["endpoint"], data=json.dumps(request_body).encode(), method="POST",
            headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json",
                     "X-OpenRouter-Metadata": "enabled", "User-Agent": "VEP-bench/0.1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=900) as response:
                payload = response.read(16 * 1024 * 1024 + 1)
            assert len(payload) <= 16 * 1024 * 1024, "response size limit"
            raw = json.loads(payload)
        except urllib.error.HTTPError as exc:
            body = exc.read(1024 * 1024)
            (OUT / f"http-error-{hop}.bin").write_bytes(body)
            emit({"hop": hop, "status": "http_error", "code": exc.code})
            break
        except Exception as exc:
            emit({"hop": hop, "status": "transport_error", "type": type(exc).__name__,
                  "upstream_billing_unknown": True})
            break
        save(f"response-{hop}.json", raw)
        if raw.get("error") or not raw.get("choices"):
            emit({"hop": hop, "status": "provider_error"})
            break
        choice = raw["choices"][0]
        message = choice["message"]
        details = message.get("reasoning_details") or []
        tokens = [d for d in details if d.get("id") == "gemini-continuation"
                  and d.get("type") == "reasoning.encrypted" and d.get("data")]
        usage = raw.get("usage") or {}
        output = usage.get("completion_tokens")
        cost = usage.get("cost")
        info = {"hop": hop, "finish_reason": choice.get("finish_reason"),
                "native_finish_reason": choice.get("native_finish_reason"),
                "provider": raw.get("provider"), "completion_tokens": output,
                "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                "cost_usd": cost, "continuation_items": len(tokens),
                "content_characters": len(message.get("content") or "")}
        summaries.append(info)
        emit(info)
        if (not isinstance(output, int) or isinstance(output, bool) or output < 0
                or not isinstance(cost, (int, float)) or isinstance(cost, bool) or not 0 <= cost <= 6):
            break
        total_output += output
        total_cost += cost
        if (raw.get("provider") != "Google" or message.get("refusal")
                or total_output >= 262144 or total_cost >= 4.830708):
            break
        if choice.get("finish_reason") != "length" or choice.get("native_finish_reason") != "CONTINUATION":
            break
        if not tokens:
            emit({"status": "missing_continuation_token"})
            break
        request_body = copy.deepcopy(initial)
        request_body["messages"].append({"role": "assistant", "content": "",
                                         "reasoning_details": [tokens[-1]]})
finally:
    finished.set()
    save("summary.json", {"hops": summaries, "known_cost_usd": total_cost,
                          "known_completion_tokens": total_output,
                          "at": datetime.now(UTC).isoformat()})
