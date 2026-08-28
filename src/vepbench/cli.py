"""VEPBench command-line interface."""

import argparse
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from .builder import BuildError, build_file
from .demo import build_demo_result
from .evaluator import evaluate_file
from .site import build_site

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vepbench")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build deterministic benchmark questions")
    build.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT / "data/sources/synthetic.jsonl",
    )
    build.add_argument(
        "--template",
        type=Path,
        default=PROJECT_ROOT / "templates/multiple_choice.json",
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
    evaluate.add_argument("--model", required=True, help="OpenRouter model ID")
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
    evaluate.add_argument("--temperature", type=float, default=0.0)
    evaluate.add_argument("--max-tokens", type=int, default=4096)
    evaluate.add_argument(
        "--reasoning-effort",
        choices=("minimal", "low", "medium", "high", "xhigh", "max"),
    )
    demo = subparsers.add_parser(
        "build-demo-result", help="build the offline synthetic explorer result"
    )
    demo.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "results/synthetic-demo.jsonl",
    )

    site = subparsers.add_parser("site", help="validate data and build the static site")
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
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            model_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", args.model).strip("-")
            run_id = args.run_id or f"{model_slug}-{timestamp}"
            output = args.output or PROJECT_ROOT / "results" / f"{run_id}.jsonl"
            summary = evaluate_file(
                questions_path=args.questions,
                question_schema_path=args.schema,
                result_schema_path=args.result_schema,
                output=output,
                run_id=run_id,
                model_id=args.model,
                api_key=api_key,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                reasoning_effort=args.reasoning_effort,
            )
            print(
                f"wrote {summary.completed + summary.api_errors} result(s) to "
                f"{summary.output} ({summary.api_errors} API error(s))"
            )
            return 0 if summary.is_complete else 1
        if args.command == "build-demo-result":
            summary = build_demo_result(
                questions_path=PROJECT_ROOT / "benchmark/questions.jsonl",
                question_schema_path=PROJECT_ROOT / "schemas/question.schema.json",
                result_schema_path=PROJECT_ROOT / "schemas/result.schema.json",
                response_path=PROJECT_ROOT
                / "data/fixtures/synthetic-openrouter-response.json",
                output=args.output,
            )
            print(f"wrote {summary.completed} synthetic result(s) to {summary.output}")
            return 0
        if args.command == "site":
            manifest = build_site(
                questions_path=PROJECT_ROOT / "benchmark/questions.jsonl",
                question_schema_path=PROJECT_ROOT / "schemas/question.schema.json",
                results_dir=PROJECT_ROOT / "results",
                result_schema_path=PROJECT_ROOT / "schemas/result.schema.json",
                assets_dir=PROJECT_ROOT / "web",
                output=args.output,
            )
            print(
                f"built {args.output} with {manifest['questions']['records']} question(s) "
                f"and {len(manifest['results'])} run(s)"
            )
            return 0
    except (BuildError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
