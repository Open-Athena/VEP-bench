import json
from pathlib import Path

import pytest
from huggingface_hub import BucketFile

from vepbench.bucket import _shared_root_plan, apply_bucket_plan, create_bucket_plan
from vepbench.builder import BuildError, canonical_json
from vepbench.publication import build_version

ROOT = Path(__file__).resolve().parents[1]


def publication(tmp_path: Path, version: str = "candidate") -> Path:
    output = tmp_path / "publication"
    build_version(
        questions_path=ROOT / "tests/fixtures/synthetic-questions.jsonl",
        results_dir=ROOT / "tests/fixtures/results",
        result_schema_path=ROOT / "schemas/result.schema.json",
        schemas_dir=ROOT / "schemas",
        output=output,
        version_name=version,
    )
    return output


def test_main_plan_requires_explicit_promotion_flag(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="requires --promote-main"):
        create_bucket_plan(
            root=tmp_path,
            version_name="main",
            bucket_id="open-athena/VEP-bench",
            plan_path=tmp_path / "plan.jsonl",
            token="not-used",
        )


def test_plan_records_shared_root_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = publication(tmp_path)
    plan_path = tmp_path / "candidate.plan.jsonl"

    class FakePlan:
        @staticmethod
        def summary():
            return {"uploads": 2, "deletes": 0, "skips": 1, "total_size": 123}

    class FakeApi:
        def list_bucket_tree(self, _bucket, prefix, recursive, token):
            return []

        def sync_bucket(self, source, destination, delete, exclude, plan, token):
            Path(plan).write_text(
                canonical_json(
                    {
                        "type": "header",
                        "source": source,
                        "dest": destination,
                        "timestamp": "2026-08-31T00:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            return FakePlan()

    monkeypatch.setattr("vepbench.bucket.HfApi", lambda token: FakeApi())

    summary = create_bucket_plan(
        root=root,
        version_name="candidate",
        bucket_id="open-athena/VEP-bench",
        plan_path=plan_path,
        token="synthetic-token",
    )

    header = json.loads(plan_path.read_text(encoding="utf-8").splitlines()[0])
    assert header["shared_root"] == _shared_root_plan(root, "upload")
    assert summary.uploads == 2 + len(header["shared_root"])
    assert summary.total_size == 123 + sum(item["size"] for item in header["shared_root"])


def test_apply_requires_exact_destination_confirmation(tmp_path: Path) -> None:
    root = publication(tmp_path)
    source = (root / "versions/candidate").resolve()
    plan = tmp_path / "plan.jsonl"
    plan.write_text(
        canonical_json(
            {
                "type": "header",
                "source": str(source),
                "dest": "hf://buckets/open-athena/VEP-bench/versions/candidate",
                "timestamp": "2026-08-31T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BuildError, match="exactly match"):
        apply_bucket_plan(
            plan_path=plan,
            confirm_destination="hf://buckets/open-athena/VEP-bench/versions/main",
            token="not-used",
        )


def test_apply_removes_marker_and_uploads_new_manifest_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = publication(tmp_path, version="main")
    source = (root / "versions/main").resolve()
    destination = "hf://buckets/open-athena/VEP-bench/versions/main"
    content_files = [
        path for path in source.rglob("*") if path.is_file() and path.name != "manifest.json"
    ]
    plan = tmp_path / "plan.jsonl"
    lines = [
        canonical_json(
            {
                "type": "header",
                "source": str(source),
                "dest": destination,
                "timestamp": "2026-08-31T00:00:00Z",
                "shared_root": _shared_root_plan(root, "upload"),
            }
        )
    ]
    lines.extend(
        canonical_json(
            {
                "type": "operation",
                "action": "upload",
                "path": path.relative_to(source).as_posix(),
                "size": path.stat().st_size,
                "reason": "test",
            }
        )
        for path in content_files
    )
    plan.write_text("\n".join(lines) + "\n", encoding="utf-8")

    class FakeApi:
        def __init__(self) -> None:
            self.files = {
                "versions/main/manifest.json": 3,
                "versions/main/stale.json": 5,
            }
            self.events: list[str] = []

        def list_bucket_tree(self, _bucket, prefix, recursive, token):
            return [
                BucketFile(type="file", path=path, size=size, xetHash="0" * 64)
                for path, size in self.files.items()
                if path == prefix or path.startswith(f"{prefix}/")
            ]

        def batch_bucket_files(self, _bucket, add=None, delete=None, token=None):
            if delete:
                for path in delete:
                    self.events.append(f"delete:{path}")
                    self.files.pop(path, None)
            if add:
                for local, remote in add:
                    self.events.append(f"add:{remote}")
                    self.files[remote] = Path(local).stat().st_size

        def sync_bucket(self, *, apply, token):
            self.events.append("sync")
            for path in list(self.files):
                if path.startswith("versions/main/"):
                    self.files.pop(path)
            for local in content_files:
                relative = local.relative_to(source).as_posix()
                self.files[f"versions/main/{relative}"] = local.stat().st_size

        def download_bucket_files(self, _bucket, files, raise_on_missing_files, token):
            for remote, local in files:
                local = Path(local)
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes((root / remote).read_bytes())

    fake = FakeApi()
    monkeypatch.setattr("vepbench.bucket.HfApi", lambda token: fake)

    apply_bucket_plan(
        plan_path=plan,
        confirm_destination=destination,
        token="synthetic-token",
        promote_main=True,
    )

    marker = "versions/main/manifest.json"
    assert fake.events.index(f"delete:{marker}") < fake.events.index("add:README.md")
    assert fake.events.index("add:README.md") < fake.events.index("sync")
    assert fake.events[-1] == f"add:{marker}"


def test_named_apply_rejects_shared_schema_divergence_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = publication(tmp_path)
    source = (root / "versions/candidate").resolve()
    destination = "hf://buckets/open-athena/VEP-bench/versions/candidate"
    content_files = [
        path for path in source.rglob("*") if path.is_file() and path.name != "manifest.json"
    ]
    lines = [
        canonical_json(
            {
                "type": "header",
                "source": str(source),
                "dest": destination,
                "timestamp": "2026-08-31T00:00:00Z",
                "shared_root": _shared_root_plan(root, "skip"),
            }
        )
    ]
    lines.extend(
        canonical_json(
            {
                "type": "operation",
                "action": "upload",
                "path": path.relative_to(source).as_posix(),
                "size": path.stat().st_size,
                "reason": "test",
            }
        )
        for path in content_files
    )
    plan = tmp_path / "candidate.plan.jsonl"
    plan.write_text("\n".join(lines) + "\n", encoding="utf-8")

    class FakeApi:
        def __init__(self) -> None:
            self.events: list[str] = []

        def list_bucket_tree(self, _bucket, prefix, recursive, token):
            if prefix == "versions/main":
                return [
                    BucketFile(
                        type="file",
                        path="versions/main/manifest.json",
                        size=3,
                        xetHash="0" * 64,
                    )
                ]
            return []

        def download_bucket_files(self, _bucket, files, raise_on_missing_files, token):
            for _remote, local in files:
                local = Path(local)
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(b"different\n")

        def batch_bucket_files(self, *_args, **_kwargs):
            self.events.append("mutate")

        def sync_bucket(self, **_kwargs):
            self.events.append("sync")

    fake = FakeApi()
    monkeypatch.setattr("vepbench.bucket.HfApi", lambda token: fake)

    with pytest.raises(BuildError, match="named versions may not change"):
        apply_bucket_plan(
            plan_path=plan,
            confirm_destination=destination,
            token="synthetic-token",
        )
    assert fake.events == []


def test_apply_rejects_unsafe_operation_path(tmp_path: Path) -> None:
    root = publication(tmp_path)
    destination = "hf://buckets/open-athena/VEP-bench/versions/candidate"
    plan = tmp_path / "plan.jsonl"
    plan.write_text(
        "\n".join(
            [
                canonical_json(
                    {
                        "type": "header",
                        "source": str((root / "versions/candidate").resolve()),
                        "dest": destination,
                        "timestamp": "2026-08-31T00:00:00Z",
                    }
                ),
                canonical_json(
                    {
                        "type": "operation",
                        "action": "delete",
                        "path": "../main/manifest.json",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BuildError, match="unsafe path"):
        apply_bucket_plan(
            plan_path=plan,
            confirm_destination=destination,
            token="not-used",
        )
