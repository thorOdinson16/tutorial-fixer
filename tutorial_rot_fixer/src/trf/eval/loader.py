"""Load eval cases: `<name>.md` plus optional `<name>.assertions.json` (block id -> Assertion)."""

from __future__ import annotations

import json
from pathlib import Path

from trf.state import Assertion, GraphState

DATASET_DIR = Path(__file__).parent / "dataset"


def list_cases(directory: Path = DATASET_DIR) -> list[str]:
    return sorted(p.stem for p in directory.glob("*.md"))


def load_case(name: str, directory: Path = DATASET_DIR) -> GraphState:
    md = (directory / f"{name}.md").read_text(encoding="utf-8")
    ap = directory / f"{name}.assertions.json"
    assertions = (
        {k: Assertion.model_validate(v) for k, v in json.loads(ap.read_text("utf-8")).items()}
        if ap.exists()
        else {}
    )
    return GraphState(tutorial_md=md, assertions=assertions)
