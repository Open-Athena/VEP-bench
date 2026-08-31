"""Explicit, prefix-scoped Hugging Face Bucket publication workflows."""

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from huggingface_hub import BucketFile, HfApi

from .builder import BuildError, canonical_json, sha256_file
from .publication import SCHEMA_FILES, validate_version

BUCKET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class BucketPlanSummary:
    destination: str
    uploads: int
    deletes: int
    skips: int
    total_size: int


def create_bucket_plan(
    *,
    root: str | Path,
    version_name: str,
    bucket_id: str,
    plan_path: str | Path,
    token: str,
    promote_main: bool = False,
) -> BucketPlanSummary:
    """Validate a version and save a non-mutating HF sync plan for review."""

    _validate_bucket_id(bucket_id)
    if version_name == "main" and not promote_main:
        raise BuildError("planning a main replacement requires --promote-main")
    validate_version(root, version_name=version_name)
    plan_file = Path(plan_path)
    if plan_file.exists():
        raise BuildError(f"refusing to overwrite existing bucket plan {plan_file}")
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    source = Path(root).resolve() / "versions" / version_name
    destination = _destination(bucket_id, version_name)
    api = HfApi(token=token)
    main_is_ready = _main_marker_present(api, bucket_id, token)
    if version_name != "main" and main_is_ready:
        _verify_remote_shared_root(api, bucket_id, Path(root), token)
    shared_action = "skip" if version_name != "main" and main_is_ready else "upload"
    plan = api.sync_bucket(
        str(source),
        destination,
        delete=True,
        exclude=["manifest.json"],
        plan=str(plan_file),
        token=token,
    )
    summary = plan.summary()
    header, operations = _read_plan(plan_file)
    shared_root = _shared_root_plan(Path(root), shared_action)
    header["shared_root"] = shared_root
    plan_file.write_text(
        "\n".join(canonical_json(record) for record in (header, *operations)) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return BucketPlanSummary(
        destination=destination,
        uploads=int(summary["uploads"]) + sum(item["action"] == "upload" for item in shared_root),
        deletes=int(summary["deletes"]),
        skips=int(summary["skips"]) + sum(item["action"] == "skip" for item in shared_root),
        total_size=int(summary["total_size"])
        + sum(item["size"] for item in shared_root if item["action"] == "upload"),
    )


def apply_bucket_plan(
    *,
    plan_path: str | Path,
    confirm_destination: str,
    token: str,
    promote_main: bool = False,
) -> BucketPlanSummary:
    """Apply a reviewed plan and install its manifest as the final operation."""

    plan_file = Path(plan_path)
    header, operations = _read_plan(plan_file)
    source = Path(header["source"]).resolve()
    destination = header["dest"]
    if destination != confirm_destination:
        raise BuildError(
            f"--confirm-destination must exactly match the plan destination {destination!r}"
        )
    bucket_id, version_name = _parse_destination(destination)
    if version_name == "main" and not promote_main:
        raise BuildError("applying a main replacement requires --promote-main")
    expected_source_suffix = Path("versions") / version_name
    if source.parts[-2:] != expected_source_suffix.parts:
        raise BuildError("bucket plan source must be one versions/<name> directory")
    root = source.parent.parent
    validate_version(root, version_name=version_name)
    operation_paths: set[str] = set()
    for operation in operations:
        path = operation.get("path")
        if not isinstance(path, str) or not _safe_relative_path(path):
            raise BuildError(f"bucket plan contains unsafe path {path!r}")
        if path == "manifest.json":
            raise BuildError("bucket plan must exclude manifest.json")
        action = operation.get("action")
        if action not in {"upload", "delete", "skip"}:
            raise BuildError(f"bucket plan contains unsupported action {action!r}")
        if path in operation_paths:
            raise BuildError(f"bucket plan contains duplicate path {path!r}")
        operation_paths.add(path)
        if action == "upload":
            local_file = source / path
            if not local_file.is_file() or local_file.stat().st_size != operation.get("size"):
                raise BuildError(f"bucket plan upload no longer matches local file {path!r}")
    planned_local = {
        operation["path"] for operation in operations if operation["action"] in {"upload", "skip"}
    }
    actual_local = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if planned_local != actual_local:
        raise BuildError("bucket plan does not cover the complete local version")

    api = HfApi(token=token)
    main_is_ready = _main_marker_present(api, bucket_id, token)
    shared_action = "skip" if version_name != "main" and main_is_ready else "upload"
    expected_shared_root = _shared_root_plan(root, shared_action)
    if header.get("shared_root") != expected_shared_root:
        raise BuildError(
            "bucket plan shared-root actions no longer match local files or remote state"
        )
    if version_name != "main" and main_is_ready:
        _verify_remote_shared_root(api, bucket_id, root, token)

    marker_path = f"versions/{version_name}/manifest.json"
    remote_paths = _remote_file_sizes(api, bucket_id, f"versions/{version_name}", token)
    if marker_path in remote_paths:
        api.batch_bucket_files(bucket_id, delete=[marker_path], token=token)

    root_additions = [(root / "README.md", "README.md")]
    root_additions.extend((root / "schemas" / name, f"schemas/{name}") for name in SCHEMA_FILES)
    if shared_action == "upload":
        api.batch_bucket_files(bucket_id, add=root_additions, token=token)

    api.sync_bucket(apply=str(plan_file), token=token)
    _verify_remote_tree(api, bucket_id, root, version_name, token, include_manifest=False)
    _verify_remote_digests(api, bucket_id, root, version_name, token)
    api.batch_bucket_files(
        bucket_id,
        add=[(source / "manifest.json", marker_path)],
        token=token,
    )
    _verify_remote_tree(api, bucket_id, root, version_name, token, include_manifest=True)
    _verify_remote_marker(api, bucket_id, root, version_name, token)

    shared_root = header["shared_root"]
    uploads = (
        sum(operation.get("action") == "upload" for operation in operations)
        + sum(item["action"] == "upload" for item in shared_root)
        + 1
    )
    deletes = sum(operation.get("action") == "delete" for operation in operations)
    skips = sum(operation.get("action") == "skip" for operation in operations) + sum(
        item["action"] == "skip" for item in shared_root
    )
    total_size = (
        sum(
            int(operation.get("size") or 0)
            for operation in operations
            if operation.get("action") == "upload"
        )
        + sum(item["size"] for item in shared_root if item["action"] == "upload")
        + (source / "manifest.json").stat().st_size
    )
    return BucketPlanSummary(destination, uploads, deletes, skips, total_size)


def _verify_remote_tree(
    api: HfApi,
    bucket_id: str,
    root: Path,
    version_name: str,
    token: str,
    *,
    include_manifest: bool,
) -> None:
    local_files = {
        (
            f"versions/{version_name}/"
            f"{path.relative_to(root / 'versions' / version_name).as_posix()}"
        ): path.stat().st_size
        for path in (root / "versions" / version_name).rglob("*")
        if path.is_file() and (include_manifest or path.name != "manifest.json")
    }
    remote_files = _remote_file_sizes(api, bucket_id, f"versions/{version_name}", token)
    if remote_files != local_files:
        missing = sorted(local_files.keys() - remote_files.keys())
        stale = sorted(remote_files.keys() - local_files.keys())
        wrong_size = sorted(
            path
            for path in local_files.keys() & remote_files.keys()
            if local_files[path] != remote_files[path]
        )
        raise BuildError(
            "remote version verification failed: "
            f"missing={missing}, stale={stale}, wrong_size={wrong_size}"
        )
    schema_sizes = {
        f"schemas/{name}": (root / "schemas" / name).stat().st_size for name in SCHEMA_FILES
    }
    remote_schemas = _remote_file_sizes(api, bucket_id, "schemas", token)
    for path, size in schema_sizes.items():
        if remote_schemas.get(path) != size:
            raise BuildError(f"remote schema verification failed for {path}")
    remote_readme = _remote_file_sizes(api, bucket_id, "README.md", token)
    if remote_readme.get("README.md") != (root / "README.md").stat().st_size:
        raise BuildError("remote bucket README verification failed")


def _shared_root_plan(root: Path, action: str) -> list[dict[str, Any]]:
    if action not in {"upload", "skip"}:
        raise ValueError(f"unsupported shared-root action {action!r}")
    paths = ["README.md", *(f"schemas/{name}" for name in SCHEMA_FILES)]
    return [
        {
            "action": action,
            "path": path,
            "sha256": sha256_file(root / path),
            "size": (root / path).stat().st_size,
        }
        for path in paths
    ]


def _main_marker_present(api: HfApi, bucket_id: str, token: str) -> bool:
    return "versions/main/manifest.json" in _remote_file_sizes(
        api, bucket_id, "versions/main", token
    )


def _verify_remote_shared_root(api: HfApi, bucket_id: str, root: Path, token: str) -> None:
    descriptors = _shared_root_plan(root, "skip")
    with tempfile.TemporaryDirectory(prefix="vepbench-shared-root-verify-") as temporary:
        mirror = Path(temporary)
        downloads = []
        for descriptor in descriptors:
            local = mirror / descriptor["path"]
            local.parent.mkdir(parents=True, exist_ok=True)
            downloads.append((descriptor["path"], local))
        try:
            api.download_bucket_files(
                bucket_id,
                downloads,
                raise_on_missing_files=True,
                token=token,
            )
        except Exception as exc:
            raise BuildError("published main shared schemas/README could not be verified") from exc
        for descriptor in descriptors:
            path = mirror / descriptor["path"]
            if (
                not path.is_file()
                or path.stat().st_size != descriptor["size"]
                or sha256_file(path) != descriptor["sha256"]
            ):
                raise BuildError(
                    "named versions may not change shared bucket files while main is ready: "
                    f"{descriptor['path']}"
                )


def _verify_remote_digests(
    api: HfApi,
    bucket_id: str,
    root: Path,
    version_name: str,
    token: str,
) -> None:
    remote_paths = ["README.md", *(f"schemas/{name}" for name in SCHEMA_FILES)]
    remote_paths.extend(
        f"versions/{version_name}/{path.relative_to(root / 'versions' / version_name).as_posix()}"
        for path in (root / "versions" / version_name).rglob("*")
        if path.is_file() and path.name != "manifest.json"
    )
    with tempfile.TemporaryDirectory(prefix="vepbench-remote-verify-") as temporary:
        mirror = Path(temporary)
        downloads: list[tuple[str, Path]] = []
        for remote_path in remote_paths:
            local_path = mirror / remote_path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            downloads.append((remote_path, local_path))
        api.download_bucket_files(
            bucket_id,
            downloads,
            raise_on_missing_files=True,
            token=token,
        )
        marker = mirror / "versions" / version_name / "manifest.json"
        shutil.copyfile(root / "versions" / version_name / "manifest.json", marker)
        validate_version(mirror, version_name=version_name)


def _verify_remote_marker(
    api: HfApi,
    bucket_id: str,
    root: Path,
    version_name: str,
    token: str,
) -> None:
    remote_path = f"versions/{version_name}/manifest.json"
    with tempfile.TemporaryDirectory(prefix="vepbench-marker-verify-") as temporary:
        downloaded = Path(temporary) / "manifest.json"
        api.download_bucket_files(
            bucket_id,
            [(remote_path, downloaded)],
            raise_on_missing_files=True,
            token=token,
        )
        local = root / "versions" / version_name / "manifest.json"
        if downloaded.read_bytes() != local.read_bytes():
            raise BuildError("remote manifest does not match the validated local marker")


def _remote_file_sizes(api: HfApi, bucket_id: str, prefix: str, token: str) -> dict[str, int]:
    return {
        item.path: item.size
        for item in api.list_bucket_tree(bucket_id, prefix=prefix, recursive=True, token=token)
        if isinstance(item, BucketFile)
    }


def _read_plan(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        operations = [json.loads(line) for line in lines[1:] if line.strip()]
    except (OSError, IndexError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read bucket plan {path}: {exc}") from exc
    if header.get("type") != "header" or not all(
        operation.get("type") == "operation" for operation in operations
    ):
        raise BuildError(f"{path}: invalid Hugging Face sync plan")
    return header, operations


def _parse_destination(destination: str) -> tuple[str, str]:
    prefix = "hf://buckets/"
    if not destination.startswith(prefix):
        raise BuildError("bucket plan destination is not an HF bucket")
    parts = destination.removeprefix(prefix).split("/")
    if len(parts) != 4 or parts[2] != "versions":
        raise BuildError("bucket plan destination must be one versions/<name> prefix")
    bucket_id = "/".join(parts[:2])
    version_name = parts[3]
    _validate_bucket_id(bucket_id)
    return bucket_id, version_name


def _destination(bucket_id: str, version_name: str) -> str:
    return f"hf://buckets/{bucket_id}/versions/{version_name}"


def _validate_bucket_id(bucket_id: str) -> None:
    if not BUCKET_ID.fullmatch(bucket_id):
        raise BuildError("bucket ID must have the form namespace/name")


def _safe_relative_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return (
        path != ""
        and not candidate.is_absolute()
        and "." not in candidate.parts
        and ".." not in candidate.parts
        and "\\" not in path
        and "\x00" not in path
    )


def require_hf_token() -> str:
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise BuildError("HF_TOKEN is not set")
    return token
