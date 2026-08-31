"""Prepare the pinned chr17 VEP consequence benchmark source artifacts."""

import os
import tempfile
from pathlib import Path

from huggingface_hub import hf_hub_download

from vepbench.genome import Genome
from vepbench.vep_consequence import (
    PreparationConfig,
    prepare_dataset,
    scan_candidate_pools,
    write_prepared_dataset,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REVISION = "eb3022cc6797b9369cca16af72ff3c4197df343a"
REFERENCE_REVISION = "11b9433582981bb929af333bc6422f10a8fd71b4"
PARQUET_URL = (
    "https://huggingface.co/datasets/songlab/hg38-variant-consequences/resolve/"
    f"{SOURCE_REVISION}/17.parquet"
)
PARQUET_URI = f"hf://datasets/songlab/hg38-variant-consequences@{SOURCE_REVISION}/17.parquet"
FASTA_URL = (
    "https://huggingface.co/datasets/marin-dna/human-genome/resolve/"
    f"{REFERENCE_REVISION}/Homo_sapiens.GRCh38.dna_sm.primary_assembly.fa"
)
OUTPUT = ROOT / "data/sources/chr17-vep-consequences.jsonl"
MANIFEST_OUTPUT = ROOT / "data/sources/chr17-vep-consequences.manifest.json"


def main() -> None:
    config = PreparationConfig()
    hf_token = os.environ.get("HF_TOKEN")
    headers = {"Authorization": f"Bearer {hf_token}"} if hf_token else {}
    with tempfile.TemporaryDirectory(prefix="vepbench-chr17-") as temporary_dir:
        print("downloading pinned chr17 Parquet with Hugging Face Hub")
        parquet_path = hf_hub_download(
            repo_id="songlab/hg38-variant-consequences",
            filename="17.parquet",
            repo_type="dataset",
            revision=SOURCE_REVISION,
            token=hf_token,
            cache_dir=temporary_dir,
        )
        parquet_size = Path(parquet_path).stat().st_size
        print(f"loading {parquet_size:,} Parquet bytes with Polars")
        scan = scan_candidate_pools(
            parquet_path,
            seed=config.seed,
            pool_size=config.candidate_pool_size,
            progress=print,
        )
    print("fetching and validating reference sequence windows")
    genome_storage_options = {"headers": headers} if headers else None
    with Genome(
        FASTA_URL,
        subset_chroms={config.chromosome},
        storage_options=genome_storage_options,
    ) as genome:
        prepared = prepare_dataset(
            scan,
            genome,
            config=config,
            source={
                "dataset": "songlab/hg38-variant-consequences",
                "dataset_revision": (f"songlab/hg38-variant-consequences@{SOURCE_REVISION}"),
                "parquet_uri": PARQUET_URI,
                "parquet_url": PARQUET_URL,
                "revision": SOURCE_REVISION,
            },
            reference={
                "dataset": "marin-dna/human-genome",
                "fasta_url": FASTA_URL,
                "revision": REFERENCE_REVISION,
                "assembly": "GRCh38",
                "ensembl_release": "115",
                "sequence": "soft-masked primary assembly (uppercased for prompts)",
            },
        )
    count, digest = write_prepared_dataset(
        prepared,
        output=OUTPUT,
        manifest_output=MANIFEST_OUTPUT,
        output_relpath="data/sources/chr17-vep-consequences.jsonl",
    )
    print(f"wrote {count} source records to {OUTPUT} (sha256 {digest})")
    print(f"wrote manifest to {MANIFEST_OUTPUT}")


if __name__ == "__main__":
    main()
