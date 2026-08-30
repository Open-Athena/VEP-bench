"""Validate committed VEP consequence source artifacts without network access."""

from pathlib import Path

from vepbench.vep_consequence import validate_prepared_artifacts

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/sources/chr17-vep-consequences.jsonl"
MANIFEST = ROOT / "data/sources/chr17-vep-consequences.manifest.json"


def main() -> None:
    manifest = validate_prepared_artifacts(SOURCE, MANIFEST)
    print(
        f"validated {manifest['output']['records']} source records "
        f"(sha256 {manifest['output']['sha256']})"
    )


if __name__ == "__main__":
    main()
