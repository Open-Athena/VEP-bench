"""Update or check fingerprints of all cache-shaping implementation dependencies."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = {
    "sge": "vepbench_sge",
    "opensplice-snv": "vepbench_opensplice_snv",
    "satmut-mpra": "vepbench_satmut_mpra",
}


def implementation_digest(task: str) -> str:
    paths = [
        f"tasks/{task}/src/{TASKS[task]}/{name}.py" for name in ("task", "prepare", "configuration")
    ]
    paths += ["src/vepbench/sampling.py", "src/vepbench/alleles.py"]
    checksums = "".join(
        f"{hashlib.sha256((ROOT / path).read_bytes()).hexdigest()}  {path}\n" for path in paths
    )
    return hashlib.sha256(checksums.encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for task in TASKS:
        path = ROOT / "tasks" / task / "config/preparation.yaml"
        text = path.read_text()
        digest = implementation_digest(task)
        updated = re.sub(
            r"(?m)^  implementation_sha256: [a-f0-9]{64}$",
            f"  implementation_sha256: {digest}",
            text,
        )
        if text != updated:
            if args.check:
                stale.append(task)
            else:
                path.write_text(updated)
    if stale:
        raise SystemExit(
            f"Stale preparation fingerprints: {', '.join(stale)}; "
            "run uv run scripts/update_preparation_digests.py"
        )


if __name__ == "__main__":
    main()
