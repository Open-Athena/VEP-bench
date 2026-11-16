"""One authorized Vertex diagnostic; no retries or automatic continuation."""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.umask(0o077)
OUT = ROOT / "vertex-probe"
OUT.mkdir(mode=0o700, exist_ok=False)
key = os.environ["OPENROUTER_API_KEY"]

def save(name, value):
    with (OUT / name).open("x") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())

def get(url, auth=False):
    headers = {"Authorization": "Bearer " + key} if auth else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return json.load(r)

account = get("https://openrouter.ai/api/v1/key", True)["data"]
assert account.get("organization_id") == "org_3K0wHyi4NHzEMXug2Tdx8K77aBB"
metadata = get("https://openrouter.ai/api/v1/models/google/gemini-4-argon/endpoints")
endpoint = next(e for e in metadata["data"]["endpoints"] if e["tag"] == "google-vertex/global")
assert float(endpoint["pricing"]["prompt"]) <= 0.000002
assert float(endpoint["pricing"]["completion"]) <= 0.00001
assert endpoint["context_length"] <= 1048576
assert endpoint["max_completion_tokens"] >= 65536
request = json.loads((ROOT / "direct-probe-request.json").read_text())
request["max_tokens"] = 65536
request["provider"]["only"] = ["google-vertex/global"]
save("preflight.json", {"at": datetime.now(UTC).isoformat(), "endpoint": endpoint,
                        "max_requests": 1, "conservative_cost_bound_usd": 2.752512})
save("request.json", request)
finished = threading.Event()
started = time.monotonic()

def monitor():
    while not finished.wait(10 if time.monotonic() - started < 60 else 300):
        available = next(int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
                         if line.startswith("MemAvailable:"))
        load = os.getloadavg()[0]
        if available < 2097152 or load > 2.5:
            save("resource-stop.json", {"available_kib": available, "load": load})
            print("Resource threshold crossed; stopping local request. Upstream state unknown.", flush=True)
            os._exit(75)

threading.Thread(target=monitor, daemon=True).start()
print(json.dumps({"status": "request_started", "at": datetime.now(UTC).isoformat()}), flush=True)
req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
    data=json.dumps(request).encode(), method="POST",
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
             "X-OpenRouter-Metadata": "enabled", "User-Agent": "VEP-bench/0.1"})
try:
    with urllib.request.urlopen(req, timeout=900) as r:
        payload = r.read(16 * 1024 * 1024 + 1)
    assert len(payload) <= 16 * 1024 * 1024
    raw = json.loads(payload)
    save("response.json", raw)
    if raw.get("error") or not raw.get("choices"):
        summary = {"status": "provider_error", "response_saved": True}
    else:
        choice = raw["choices"][0]
        message = choice["message"]
        usage = raw.get("usage") or {}
        details = message.get("reasoning_details") or []
        tokens = [d for d in details if d.get("id") == "gemini-continuation" and d.get("data")]
        summary = {"status": "response_received", "provider": raw.get("provider"),
            "finish_reason": choice.get("finish_reason"),
            "native_finish_reason": choice.get("native_finish_reason"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "cost_usd": usage.get("cost"), "continuation_items": len(tokens),
            "content_characters": len(message.get("content") or "")}
except urllib.error.HTTPError as exc:
    payload = exc.read(1024 * 1024)
    (OUT / "http-error.bin").write_bytes(payload)
    summary = {"status": "http_error", "code": exc.code}
except Exception as exc:
    summary = {"status": "transport_error", "type": type(exc).__name__, "upstream_billing_unknown": True}
finally:
    finished.set()
save("summary.json", summary)
print(json.dumps(summary), flush=True)
