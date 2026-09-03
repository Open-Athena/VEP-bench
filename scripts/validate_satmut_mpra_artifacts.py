"""Validate committed satMutMPRA source artifacts without network access."""

from pathlib import Path

from vepbench.satmut_mpra import validate_prepared_artifacts

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/sources/satmut-mpra-cadd-v1.7.jsonl"
MANIFEST = ROOT / "data/sources/satmut-mpra-cadd-v1.7.manifest.json"


def main() -> None:
    manifest = validate_prepared_artifacts(SOURCE, MANIFEST)
    print(
        f"validated {manifest['output']['records']} satMutMPRA source records "
        f"(sha256 {manifest['output']['sha256']})"
    )


if __name__ == "__main__":
    main()
