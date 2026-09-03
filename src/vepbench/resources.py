"""Paths to immutable resources bundled with the evaluator package."""

from importlib.resources import files
from pathlib import Path

# The distributions are private workspace projects today, but resolving through
# importlib.resources keeps the evaluator independent of a source checkout.
SCHEMAS_DIR = Path(str(files("vepbench.schemas")))
QUESTION_SCHEMA = SCHEMAS_DIR / "question.schema.json"
RESULT_SCHEMA = SCHEMAS_DIR / "result.schema.json"
