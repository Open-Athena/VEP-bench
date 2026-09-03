"""Compatibility wrapper for the SGE workspace command."""

from vepbench_sge.cli import main as task_main


def main() -> int:
    return task_main(["validate"])


if __name__ == "__main__":
    raise SystemExit(main())
