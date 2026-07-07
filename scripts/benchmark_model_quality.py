#!/usr/bin/env python3
"""Write the v0.49 measured local-model quality plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from capabledeputy.model_quality import model_quality_plan, write_model_quality_plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("benchmark-results/model-quality/plan.jsonl"),
        help="JSONL file to write.",
    )
    args = parser.parse_args()

    plan = model_quality_plan()
    write_model_quality_plan(plan, args.results)
    print(json.dumps({"event": "wrote_plan", "path": str(args.results)}, sort_keys=True))
    print(json.dumps(plan, sort_keys=True))


if __name__ == "__main__":
    main()
