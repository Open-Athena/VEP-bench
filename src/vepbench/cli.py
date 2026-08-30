"""VEPBench command-line interface."""

import argparse
import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from .batch import collect_batch_file, refresh_batch_state, submit_batch_file
from .builder import BuildError, build_file
from .evaluator import ProviderError, evaluate_file
from .model_profile import load_model_profile
from .site import build_site

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
        default=PROJECT_ROOT / "benchmark/questions.jsonl",
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
        default=PROJECT_ROOT / "benchmark/questions.jsonl",
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
    evaluate.add_argument("--max-tokens", type=int)
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
        default=PROJECT_ROOT / "benchmark/questions.jsonl",
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
            count, digest = build_file(
                args.source, args.template, args.schema, args.output
            )
            print(f"wrote {count} question(s) to {args.output} (sha256 {digest})")
            return 0
        if args.command == "evaluate":
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise BuildError("OPENROUTER_API_KEY is not set")
            if args.model_profile is not None:
                profile = load_model_profile(args.model_profile)
                model_id = profile.model_id
                profile_label = profile.label
                generation_parameters = dict(profile.generation_parameters)
            else:
                model_id = args.model
                profile_label = args.model
                generation_parameters = {"temperature": 0.0, "max_tokens": 4096}
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
            output = args.output or PROJECT_ROOT / "results" / f"{run_id}.jsonl"
            if not args.direct:
                if args.resume:
                    raise BuildError("--resume requires --direct")
                state_path = args.batch_state or (
                    PROJECT_ROOT / ".vepbench" / "batches" / f"{run_id}.json"
                )
                batch = submit_batch_file(
                    questions_path=args.questions,
                    question_schema_path=args.schema,
                    state_path=state_path,
                    result_output=output,
                    run_id=run_id,
                    model_id=model_id,
                    api_key=api_key,
                    generation_parameters=generation_parameters,
                )
                print(
                    f"submitted {batch.requests} request(s) as OpenRouter batch "
                    f"{batch.batch_id} ({batch.status}); state: {batch.state_path}"
                )
                return 0
            summary = evaluate_file(
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
                f"wrote {summary.completed + summary.api_errors} result(s) to "
                f"{summary.output} ({summary.api_errors} API error(s))"
            )
            return 0 if summary.is_complete else 1
        if args.command == "batch-status":
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise BuildError("OPENROUTER_API_KEY is not set")
            batch = refresh_batch_state(state_path=args.state, api_key=api_key)
            print(
                f"batch {batch.batch_id}: {batch.status} "
                f"(completed={batch.completed}, failed={batch.failed}, total={batch.total})"
            )
            return 0
        if args.command == "batch-collect":
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise BuildError("OPENROUTER_API_KEY is not set")
            summary = collect_batch_file(
                state_path=args.state,
                questions_path=args.questions,
                question_schema_path=args.schema,
                result_schema_path=args.result_schema,
                api_key=api_key,
            )
            print(
                f"wrote {summary.completed + summary.api_errors} result(s) to "
                f"{summary.output} ({summary.api_errors} API error(s))"
            )
            return 0 if summary.is_complete else 1
        if args.command == "site":
            if args.output.exists() and any(args.output.iterdir()):
                raise BuildError(f"refusing to overwrite non-empty site directory {args.output}")
            observable = PROJECT_ROOT / "node_modules/.bin/observable"
            if not observable.is_file():
                raise BuildError("Observable Framework is not installed; run `npm ci`")
            with tempfile.TemporaryDirectory(prefix="vepbench-observable-") as temporary:
                source = Path(temporary) / "source"
                manifest = build_site(
                    questions_path=PROJECT_ROOT / "benchmark/questions.jsonl",
                    question_schema_path=PROJECT_ROOT / "schemas/question.schema.json",
                    results_dir=PROJECT_ROOT / "results",
                    result_schema_path=PROJECT_ROOT / "schemas/result.schema.json",
                    assets_dir=PROJECT_ROOT / "web",
                    output=source,
                )
                completed = subprocess.run(
                    [
                        str(observable),
                        "build",
                        "--root",
                        str(source),
                        "--output",
                        str(args.output.resolve()),
                    ],
                    cwd=PROJECT_ROOT,
                    check=False,
                )
                if completed.returncode != 0:
                    raise BuildError(
                        f"Observable Framework build failed with exit code "
                        f"{completed.returncode}"
                    )
            print(
                f"built {args.output} with {manifest['questions']['records']} question(s) "
                f"and {len(manifest['results'])} run(s)"
            )
            return 0
    except (BuildError, ProviderError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
