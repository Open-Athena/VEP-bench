"""Download small public analysis inputs against an already pinned manifest."""

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen


def fetch(root: Path, base_url: str, descriptor: dict) -> None:
    path = root / descriptor["path"]
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("artifact path escapes download directory")
    size = descriptor["artifact_bytes"]
    if size > 8 * 1024 * 1024:
        raise ValueError("analysis input exceeds the 8 MiB limit")
    if (
        path.is_file()
        and hashlib.sha256(path.read_bytes()).hexdigest() == descriptor["artifact_sha256"]
    ):
        return
    url = base_url.rstrip("/") + "/" + quote(descriptor["path"], safe="/")
    for attempt in range(3):
        try:
            with urlopen(url, timeout=45) as response:
                data = response.read(size + 1)
            break
        except HTTPError, URLError, TimeoutError:
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    if len(data) != size or hashlib.sha256(data).hexdigest() != descriptor["artifact_sha256"]:
        raise ValueError(f"publication changed or input is corrupt: {descriptor['path']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--base-url", default="https://huggingface.co/buckets/open-athena/VEP-bench/resolve"
    )
    parser.add_argument(
        "--answers", action="store_true", help="Also fetch saved answers after freezing coverage"
    )
    args = parser.parse_args()
    if not args.base_url.startswith("https://"):
        raise ValueError("use a public HTTPS artifact URL")
    manifest = json.loads(args.manifest.read_text())
    artifacts = manifest["artifacts"]
    descriptors = [artifacts["question_index"], artifacts["runs"]]
    if args.answers:
        descriptors += artifacts["answers"]
    # Four small network transfers; no full raw archives or model requests.
    with ThreadPoolExecutor(max_workers=4) as executor:
        for _ in executor.map(lambda d: fetch(args.output, args.base_url, d), descriptors):
            pass
    print(f"Verified {len(descriptors)} pinned artifacts")


if __name__ == "__main__":
    main()
