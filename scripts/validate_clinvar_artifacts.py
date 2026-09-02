"""Validate committed ClinVar source artifacts without network access."""

from pathlib import Path

from vepbench.clinvar import validate_prepared_artifacts

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/sources/clinvar-july-2026.jsonl"
MANIFEST = ROOT / "data/sources/clinvar-july-2026.manifest.json"


def main() -> None:
    manifest = validate_prepared_artifacts(SOURCE, MANIFEST)
    print(
        f"validated {manifest['output']['records']} ClinVar source records "
        f"(sha256 {manifest['output']['sha256']})"
    )


if __name__ == "__main__":
    main()
