"""VEPBench command-line interface."""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from .batch import (
    collect_batch_file,
    merge_batch_result_files,
    refresh_batch_state,
    submit_batch_file,
)
from .bucket import apply_bucket_plan, create_bucket_plan, require_hf_token
from .builder import BuildError, build_file, read_jsonl
from .evaluator import ProviderError, evaluate_file
from .model_profile import load_model_profile
from .publication import build_version, promote_version, validate_version
from .site import build_question_metadata, build_site
from .task_profile import load_task_profile

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vepbench")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build deterministic benchmark questions")
    build.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT / "data/sources/chr17-vep-consequences.jsonl",
    )
    build.add_argument(
        "--template",
        type=Path,
        default=PROJECT_ROOT / "templates/vep_most_severe_consequence.json",
    )
    build.add_argument(
        "--schema",
        type=Path,
        default=PROJECT_ROOT / "schemas/question.schema.json",
    )
    build.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / ".vepbench/questions.jsonl",
    )

    evaluate = subparsers.add_parser(
        "evaluate", help="evaluate benchmark questions through OpenRouter"
    )
    model_source = evaluate.add_mutually_exclusive_group(required=True)
    model_source.add_argument("--model", help="OpenRouter model ID")
    model_source.add_argument(
        "--model-profile",
        type=Path,
        help="versioned YAML model profile",
    )
    evaluate.add_argument(
        "--questions",
        type=Path,
        default=PROJECT_ROOT / ".vepbench/questions.jsonl",
    )
    evaluate.add_argument(
        "--task-profile",
        type=Path,
        help="versioned task-level evaluation settings",
    )
    evaluate.add_argument(
        "--schema",
        type=Path,
        default=PROJECT_ROOT / "schemas/question.schema.json",
    )
    evaluate.add_argument(
        "--result-schema",
        type=Path,
        default=PROJECT_ROOT / "schemas/result.schema.json",
    )
    evaluate.add_argument("--output", type=Path)
    evaluate.add_argument("--run-id")
    evaluate.add_argument(
        "--direct",
        action="store_true",
        help="send bounded parallel requests instead of submitting a batch",
    )
    evaluate.add_argument(
        "--batch-state",
        type=Path,
        help="local state path for the asynchronous batch submission",
    )
    evaluate.add_argument(
        "--batch-offset",
        type=int,
        default=0,
        help="zero-based first question to submit through the Batch API",
    )
    evaluate.add_argument(
        "--batch-size",
        type=int,
        help="maximum questions to submit while retaining full-set result identity",
    )
    evaluate.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="maximum in-flight requests with --direct (default: 8)",
    )
    evaluate.add_argument(
        "--resume",
        action="store_true",
        help="validate and continue an interrupted ordered result file (requires --direct)",
    )
    evaluate.add_argument("--temperature", type=float)
    evaluate.add_argument(
        "--max-tokens",
        type=int,
        help="completion ceiling for ad hoc --model runs (benchmark profiles own this)",
    )
    evaluate.add_argument(
        "--reasoning-effort",
        choices=("minimal", "low", "medium", "high", "xhigh", "max"),
    )
    batch_status = subparsers.add_parser(
        "batch-status", help="refresh the status of a submitted OpenRouter batch"
    )
    batch_status.add_argument("--state", type=Path, required=True)
    batch_collect = subparsers.add_parser(
        "batch-collect", help="collect and score a completed OpenRouter batch"
    )
    batch_collect.add_argument("--state", type=Path, required=True)
    batch_collect.add_argument(
        "--questions",
        type=Path,
        default=PROJECT_ROOT / ".vepbench/questions.jsonl",
    )
    batch_collect.add_argument(
        "--schema",
        type=Path,
        default=PROJECT_ROOT / "schemas/question.schema.json",
    )
    batch_collect.add_argument(
        "--result-schema",
        type=Path,
        default=PROJECT_ROOT / "schemas/result.schema.json",
    )
    batch_merge = subparsers.add_parser(
        "batch-merge", help="merge collected batch chunks into one full result file"
    )
    batch_merge.add_argument("inputs", type=Path, nargs="+")
    batch_merge.add_argument(
        "--questions",
        type=Path,
        default=PROJECT_ROOT / ".vepbench/questions.jsonl",
    )
    batch_merge.add_argument(
        "--schema",
        type=Path,
        default=PROJECT_ROOT / "schemas/question.schema.json",
    )
    batch_merge.add_argument(
        "--result-schema",
        type=Path,
        default=PROJECT_ROOT / "schemas/result.schema.json",
    )
    batch_merge.add_argument("--output", type=Path, required=True)
    version_build = subparsers.add_parser(
        "version-build", help="build a validated local bucket version"
    )
    version_build.add_argument("--version", required=True)
    version_build.add_argument(
        "--questions",
        type=Path,
        action="append",
        help="question JSONL for one task family; repeat for multi-task publication",
    )
    version_build.add_argument(
        "--results-dir",
        type=Path,
        action="append",
        help="directory of result JSONL files; repeat for multiple staging directories",
    )
    version_build.add_argument(
        "--result-schema",
        type=Path,
        default=PROJECT_ROOT / "schemas/result.schema.json",
    )
    version_build.add_argument("--schemas-dir", type=Path, default=PROJECT_ROOT / "schemas")
    version_build.add_argument(
        "--model-catalog",
        type=Path,
        default=PROJECT_ROOT / "configs/models/catalog.json",
        help="model family and release metadata for published leaderboard rows",
    )
    version_build.add_argument("--output", type=Path, required=True)
    version_validate = subparsers.add_parser(
        "version-validate", help="validate a complete local bucket version"
    )
    version_validate.add_argument("--version", required=True)
    version_validate.add_argument("--root", type=Path, required=True)
    version_promote = subparsers.add_parser(
        "version-promote", help="build a future main tree from a named local version"
    )
    version_promote.add_argument("--source-root", type=Path, required=True)
    version_promote.add_argument("--source-version", required=True)
    version_promote.add_argument("--output", type=Path, required=True)
    bucket_plan = subparsers.add_parser(
        "bucket-plan", help="save a reviewed, prefix-scoped HF Bucket sync plan"
    )
    bucket_plan.add_argument("--root", type=Path, required=True)
    bucket_plan.add_argument("--version", required=True)
    bucket_plan.add_argument("--bucket", default="open-athena/vepbench")
    bucket_plan.add_argument("--plan", type=Path, required=True)
    bucket_plan.add_argument(
        "--promote-main",
        action="store_true",
        help="acknowledge that the plan replaces the protected main version",
    )
    bucket_apply = subparsers.add_parser(
        "bucket-apply", help="apply a reviewed HF Bucket plan and publish its manifest last"
    )
    bucket_apply.add_argument("--plan", type=Path, required=True)
    bucket_apply.add_argument("--confirm-destination", required=True)
    bucket_apply.add_argument(
        "--promote-main",
        action="store_true",
        help="acknowledge that the plan replaces the protected main version",
    )
    site = subparsers.add_parser(
        "site", help="validate data and build the Observable static explorer"
    )
    site.add_argument("--output", type=Path, default=PROJECT_ROOT / "_site")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            count, digest = build_file(args.source, args.template, args.schema, args.output)
            print(f"wrote {count} question(s) to {args.output} (sha256 {digest})")
            return 0
        if args.command == "evaluate":
            if args.model_profile is not None and args.task_profile is None:
                raise BuildError("--model-profile requires --task-profile")
            if args.task_profile is not None and args.max_tokens is not None:
                raise BuildError(
                    "--max-tokens cannot override a task profile; update the versioned "
                    "task profile instead"
                )
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise BuildError("OPENROUTER_API_KEY is not set")
            if args.task_profile is not None:
                task_profile = load_task_profile(args.task_profile)
                question_families = {
                    question.get("metadata", {}).get("task_family")
                    for question in read_jsonl(args.questions)
                }
                if None in question_families:
                    raise BuildError(f"{args.questions}: question is missing metadata.task_family")
                if question_families != {task_profile.task_family}:
                    raise BuildError(
                        f"{args.questions}: task families "
                        f"{sorted(question_families, key=str)} "
                        f"do not match task profile {task_profile.task_family!r}"
                    )
                generation_parameters = dict(task_profile.generation_parameters)
            else:
                generation_parameters = {}
            if args.model_profile is not None:
                profile = load_model_profile(args.model_profile)
                model_id = profile.model_id
                profile_label = profile.label
                overlapping = generation_parameters.keys() & profile.generation_parameters.keys()
                if overlapping:
                    raise BuildError(
                        "task and model profiles define the same generation parameter(s): "
                        f"{sorted(overlapping)}"
                    )
                generation_parameters.update(profile.generation_parameters)
            else:
                model_id = args.model
                profile_label = args.model
                generation_parameters.setdefault("temperature", 0.0)
                generation_parameters.setdefault("max_tokens", 4096)
            if args.temperature is not None:
                generation_parameters["temperature"] = args.temperature
            if args.max_tokens is not None:
                generation_parameters["max_tokens"] = args.max_tokens
            if args.reasoning_effort is not None:
                generation_parameters["reasoning"] = {
                    "effort": args.reasoning_effort,
                    "exclude": False,
                }
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            model_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", profile_label).strip("-")
            run_id = args.run_id or f"{model_slug}-{timestamp}"
            output = args.output or PROJECT_ROOT / ".vepbench/results" / f"{run_id}.jsonl"
            if not args.direct:
                if args.resume:
                    raise BuildError("--resume requires --direct")
                state_path = args.batch_state or (
                    PROJECT_ROOT / ".vepbench" / "batches" / f"{run_id}.json"
                )
                submission = submit_batch_file(
                    questions_path=args.questions,
                    question_schema_path=args.schema,
                    state_path=state_path,
                    result_output=output,
                    run_id=run_id,
                    model_id=model_id,
                    api_key=api_key,
                    generation_parameters=generation_parameters,
                    batch_offset=args.batch_offset,
                    batch_size=args.batch_size,
                )
                print(
                    f"submitted {submission.requests} request(s) as OpenRouter batch "
                    f"{submission.batch_id} ({submission.status}); "
                    f"state: {submission.state_path}"
                )
                return 0
            if args.batch_offset != 0 or args.batch_size is not None:
                raise BuildError("--batch-offset and --batch-size cannot be used with --direct")
            evaluation = evaluate_file(
                questions_path=args.questions,
                question_schema_path=args.schema,
                result_schema_path=args.result_schema,
                output=output,
                run_id=run_id,
                model_id=model_id,
                api_key=api_key,
                generation_parameters=generation_parameters,
                progress=lambda done, total, errors: print(
                    f"evaluated {done}/{total} ({errors} API error(s))", flush=True
                ),
                concurrency=args.concurrency,
                resume=args.resume,
            )
            print(
                f"wrote {evaluation.completed + evaluation.api_errors} result(s) to "
                f"{evaluation.output} ({evaluation.api_errors} API error(s))"
            )
            return 0 if evaluation.is_complete else 1
        if args.command == "batch-status":
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise BuildError("OPENROUTER_API_KEY is not set")
            batch_status = refresh_batch_state(state_path=args.state, api_key=api_key)
            print(
                f"batch {batch_status.batch_id}: {batch_status.status} "
                f"(completed={batch_status.completed}, failed={batch_status.failed}, "
                f"total={batch_status.total})"
            )
            return 0
        if args.command == "batch-collect":
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise BuildError("OPENROUTER_API_KEY is not set")
            collected = collect_batch_file(
                state_path=args.state,
                questions_path=args.questions,
                question_schema_path=args.schema,
                result_schema_path=args.result_schema,
                api_key=api_key,
            )
            print(
                f"wrote {collected.completed + collected.api_errors} result(s) to "
                f"{collected.output} ({collected.api_errors} API error(s))"
            )
            return 0 if collected.is_complete else 1
        if args.command == "batch-merge":
            merged = merge_batch_result_files(
                result_paths=args.inputs,
                questions_path=args.questions,
                question_schema_path=args.schema,
                result_schema_path=args.result_schema,
                output=args.output,
            )
            print(
                f"wrote {merged.completed + merged.api_errors} merged result(s) to "
                f"{merged.output} ({merged.api_errors} API error(s))"
            )
            return 0 if merged.is_complete else 1
        if args.command == "version-build":
            manifest = build_version(
                questions_path=args.questions or [PROJECT_ROOT / ".vepbench/questions.jsonl"],
                results_dir=args.results_dir or [PROJECT_ROOT / ".vepbench/results"],
                result_schema_path=args.result_schema,
                schemas_dir=args.schemas_dir,
                model_catalog_path=args.model_catalog,
                output=args.output,
                version_name=args.version,
            )
            print(
                f"built versions/{args.version} with "
                f"{manifest['question_set_size']} question(s) and "
                f"{manifest['artifacts']['runs']['records']} run(s) at {args.output}"
            )
            return 0
        if args.command == "version-validate":
            manifest = validate_version(args.root, version_name=args.version)
            print(
                f"validated versions/{args.version} with "
                f"{manifest['question_set_size']} question(s) and "
                f"{manifest['artifacts']['runs']['records']} run(s)"
            )
            return 0
        if args.command == "version-promote":
            manifest = promote_version(
                source_root=args.source_root,
                source_version=args.source_version,
                output=args.output,
            )
            print(
                f"promoted versions/{args.source_version} into a validated local "
                f"versions/main tree with {manifest['question_set_size']} question(s) "
                f"at {args.output}"
            )
            return 0
        if args.command == "bucket-plan":
            plan = create_bucket_plan(
                root=args.root,
                version_name=args.version,
                bucket_id=args.bucket,
                plan_path=args.plan,
                token=require_hf_token(),
                promote_main=args.promote_main,
            )
            print(
                f"saved plan for {plan.destination}: uploads={plan.uploads}, "
                f"deletes={plan.deletes}, skips={plan.skips}, "
                f"upload_bytes={plan.total_size}"
            )
            return 0
        if args.command == "bucket-apply":
            applied = apply_bucket_plan(
                plan_path=args.plan,
                confirm_destination=args.confirm_destination,
                token=require_hf_token(),
                promote_main=args.promote_main,
            )
            print(
                f"published and verified {applied.destination}: "
                f"uploads={applied.uploads}, deletes={applied.deletes}"
            )
            return 0
        if args.command == "site":
            if args.output.exists() and any(args.output.iterdir()):
                raise BuildError(f"refusing to overwrite non-empty site directory {args.output}")
            observable = PROJECT_ROOT / "node_modules/.bin/observable"
            if not observable.is_file():
                raise BuildError("Observable Framework is not installed; run `npm ci`")
            with tempfile.TemporaryDirectory(prefix="vepbench-observable-") as temporary:
                project = Path(temporary)
                source = project / "web"
                consequence_manifest = json.loads(
                    (PROJECT_ROOT / "data/sources/chr17-vep-consequences.manifest.json").read_text(
                        encoding="utf-8"
                    )
                )
                question_metadata = build_question_metadata(
                    source_paths=[
                        PROJECT_ROOT / "data/sources/chr17-vep-consequences.jsonl",
                        PROJECT_ROOT / "data/sources/clinvar-july-2026.jsonl",
                        PROJECT_ROOT / "data/sources/satmut-mpra-cadd-v1.7.jsonl",
                    ],
                    consequence_overrides={
                        "vep_most_severe_consequence": consequence_manifest[
                            "record_source_consequences"
                        ]
                    },
                )
                manifest = build_site(
                    assets_dir=PROJECT_ROOT / "web",
                    output=source,
                    question_metadata=question_metadata,
                )
                shutil.copy2(
                    PROJECT_ROOT / "observablehq.config.js",
                    project / "observablehq.config.js",
                )
                completed = subprocess.run(
                    [str(observable), "build"],
                    cwd=project,
                    check=False,
                    env={
                        **os.environ,
                        "OBSERVABLE_TELEMETRY_DISABLE": "true",
                        "VEPBENCH_OBSERVABLE_OUTPUT": str(args.output.resolve()),
                    },
                )
                if completed.returncode != 0:
                    raise BuildError(
                        f"Observable Framework build failed with exit code {completed.returncode}"
                    )
            print(f"built {args.output} against {manifest['data_base_url']}")
            return 0
    except (BuildError, ProviderError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
