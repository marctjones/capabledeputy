#!/usr/bin/env python3
"""Plan CapDep local-model experiments for tool, guard, and reranker candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from capabledeputy.model_experiments import (
    experiment_plan,
    write_jsonl_plan,
)


def _hf_metadata(repo: str) -> dict[str, Any]:
    from huggingface_hub import HfApi

    info = HfApi().model_info(repo, files_metadata=False)
    tags = getattr(info, "tags", []) or []
    license_tag = next((tag.split(":", 1)[1] for tag in tags if tag.startswith("license:")), None)
    return {
        "repo": repo,
        "ok": True,
        "gated": getattr(info, "gated", None),
        "private": getattr(info, "private", None),
        "downloads": getattr(info, "downloads", None),
        "likes": getattr(info, "likes", None),
        "license": license_tag,
        "tags": tags[:12],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--purpose",
        choices=("tool_selection", "risk_guard", "reranker"),
        help="Limit the plan to one experiment purpose.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("benchmark-results/model-experiments/converted"),
        help="Root directory for planned converted MLX artifacts.",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("benchmark-results/model-experiments/plan.jsonl"),
        help="JSONL file to write.",
    )
    parser.add_argument(
        "--check-hf",
        action="store_true",
        help="Verify source repositories through the Hugging Face API.",
    )
    args = parser.parse_args()

    purpose = args.purpose if args.purpose is not None else None
    plan = experiment_plan(purpose=purpose, output_root=args.output_root)
    if args.check_hf:
        metadata = {}
        for candidate in plan["candidates"]:
            repo = candidate["source_repo"]
            try:
                metadata[repo] = _hf_metadata(repo)
            except Exception as exc:  # pragma: no cover - network/auth dependent
                metadata[repo] = {
                    "repo": repo,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        plan["hf_metadata"] = metadata

    write_jsonl_plan(plan, args.results)
    print(json.dumps({"event": "wrote_plan", "path": str(args.results)}, sort_keys=True))
    print(json.dumps(plan, sort_keys=True))


if __name__ == "__main__":
    main()
