import runpy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_cache_fingerprints_include_shared_sampling_and_allele_code():
    utility = runpy.run_path(str(ROOT / "scripts/update_preparation_digests.py"))
    for task in utility["TASKS"]:
        config = yaml.safe_load((ROOT / "tasks" / task / "config/preparation.yaml").read_text())
        assert config["cache"]["implementation_sha256"] == utility["implementation_digest"](task)
        assert config["cache"]["root"].endswith("/v2")
