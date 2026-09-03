"""Compatibility wrapper for the satMutMPRA workspace command."""

from vepbench_satmut_mpra.cli import main as task_main


def main() -> int:
    return task_main(["validate"])


if __name__ == "__main__":
    raise SystemExit(main())
