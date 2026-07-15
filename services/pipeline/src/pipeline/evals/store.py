"""Load gold annotations from evals/gold/annotations/*.json."""

import json
from pathlib import Path

from pipeline.evals.gold import GoldMatter


def load_gold_matters(annotations_dir: Path) -> list[GoldMatter]:
    matters: list[GoldMatter] = []
    for path in sorted(annotations_dir.glob("*.json")):
        matters.append(GoldMatter.model_validate(json.loads(path.read_text())))
    return matters
