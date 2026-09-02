#!/usr/bin/env python3
"""Command-line wrapper for the deterministic browser QA fixture builder."""

import argparse
from pathlib import Path

from vepbench.browser_qa import (
    DEFAULT_PREDICTION,
    DEFAULT_QUESTION_ID,
    prepare_fixture,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--site-root", type=Path)
    parser.add_argument("--data-base-url")
    parser.add_argument("--question-id", default=DEFAULT_QUESTION_ID)
    parser.add_argument("--prediction", default=DEFAULT_PREDICTION)
    parser.add_argument("--alternate-model", action="store_true")
    args = parser.parse_args()
    manifest = prepare_fixture(
        questions_path=args.questions,
        output=args.output,
        selected_question_id=args.question_id,
        prediction=args.prediction,
        include_alternate_model=args.alternate_model,
        site_root=args.site_root,
        data_base_url=args.data_base_url,
    )
    print(
        f"prepared deterministic browser QA for {manifest['question_set_size']} "
        f"question(s) at {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
