#!/usr/bin/env python3
"""Benchmark CapDep image-generation backends on the local machine."""

from __future__ import annotations

import argparse
import json
import platform
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any

from capabledeputy.mcp_servers._image_pipeline import (
    ImageGenConfig,
    clear_pipeline_cache,
    generate_image,
    load_image_gen_config,
)

DEFAULT_CANDIDATES = (
    "z-image-turbo",
    "flux2-klein-4b",
    "flux2-klein-9b",
    "z-image",
)

SUPPORTED_CANDIDATES = (
    *DEFAULT_CANDIDATES,
    "fibo-lite",
    "fibo",
    "schnell",
    "qwen-image",
    "dev",
    "krea-dev",
)

PREFETCH_REPOS = {
    "z-image-turbo": "filipstrand/Z-Image-Turbo-mflux-4bit",
    "z-image": "Tongyi-MAI/Z-Image-Turbo",
    "flux2-klein-4b": "black-forest-labs/FLUX.2-klein-4B",
    "flux2-klein-9b": "black-forest-labs/FLUX.2-klein-9B",
    "fibo-lite": "briaai/Fibo-lite",
    "fibo": "briaai/Fibo",
    "schnell": "black-forest-labs/FLUX.1-schnell",
    "dev": "black-forest-labs/FLUX.1-dev",
    "krea-dev": "black-forest-labs/FLUX.1-Krea-dev",
    "qwen-image": "OsaurusAI/Qwen-Image-mflux-4bit",
}


def _prefetch_candidates(candidates: tuple[str, ...], *, workers: int) -> list[dict[str, Any]]:
    from huggingface_hub import snapshot_download

    repos = {PREFETCH_REPOS[candidate] for candidate in candidates if candidate in PREFETCH_REPOS}
    rows: list[dict[str, Any]] = []
    if not repos:
        return rows
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(repos)))) as pool:
        futures = {pool.submit(snapshot_download, repo_id=repo): repo for repo in sorted(repos)}
        for future in as_completed(futures):
            repo = futures[future]
            try:
                path = future.result()
                row = {"event": "prefetch", "ok": True, "repo": repo, "path": path, "error": None}
            except Exception as exc:
                row = {
                    "event": "prefetch",
                    "ok": False,
                    "repo": repo,
                    "path": None,
                    "error": str(exc),
                }
            rows.append(row)
            print(json.dumps(row, sort_keys=True))
    return rows


def _candidate_config(
    base: ImageGenConfig,
    *,
    candidate: str,
    output_dir: Path,
    width: int,
    height: int,
    steps: int,
    quantize: int | None,
    lora_paths: tuple[str, ...],
    lora_scales: tuple[float, ...],
) -> ImageGenConfig:
    model_path = "filipstrand/Z-Image-Turbo-mflux-4bit" if candidate == "z-image-turbo" else None
    if candidate == "flux2-klein-4b":
        steps = min(steps, 4)
    elif candidate == "z-image-turbo":
        steps = min(steps, 9)
    return replace(
        base,
        backend="mflux",
        model=candidate,
        model_path=model_path,
        quantize=quantize,
        output_dir=output_dir,
        default_width=width,
        default_height=height,
        default_steps=steps,
        lora_paths=lora_paths,
        lora_scales=lora_scales,
        prompt_filter_enabled=False,
    )


def _run_candidate(
    *,
    base: ImageGenConfig,
    candidate: str,
    prompt: str,
    output_dir: Path,
    width: int,
    height: int,
    steps: int,
    quantize: int | None,
    lora_paths: tuple[str, ...],
    lora_scales: tuple[float, ...],
    seed: int,
) -> dict[str, Any]:
    clear_pipeline_cache()
    config = _candidate_config(
        base,
        candidate=candidate,
        output_dir=output_dir,
        width=width,
        height=height,
        steps=steps,
        quantize=quantize,
        lora_paths=lora_paths,
        lora_scales=lora_scales,
    )
    start = time.perf_counter()
    result = generate_image(
        prompt=prompt,
        config=config,
        seed=seed,
        filename=f"{candidate}.png",
    )
    elapsed = time.perf_counter() - start
    return {
        "candidate": candidate,
        "ok": bool(result.get("ok")),
        "elapsed_seconds": round(elapsed, 3),
        "backend": result.get("backend"),
        "model": result.get("model"),
        "model_path": result.get("model_path"),
        "quantize": result.get("quantize"),
        "lora_paths": list(config.lora_paths),
        "lora_scales": list(config.lora_scales),
        "width": result.get("width"),
        "height": result.get("height"),
        "steps": result.get("steps"),
        "image_path": result.get("image_path"),
        "error": result.get("error"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompt",
        default=(
            "A detailed cinematic portrait of a professional creative working at a "
            "Mac laptop in a softly lit studio, natural skin texture, realistic "
            "hands, shallow depth of field, balanced color grading."
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark-results/images"))
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("benchmark-results/image-models.jsonl"),
    )
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--steps", type=int, default=9)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--quantize", type=int, default=8)
    parser.add_argument("--candidate", action="append", choices=SUPPORTED_CANDIDATES)
    parser.add_argument("--lora", action="append", default=[])
    parser.add_argument("--lora-scale", action="append", type=float, default=[])
    parser.add_argument(
        "--prefetch-workers",
        type=int,
        default=0,
        help="Download selected model snapshots before generation; 2 is a good Mac default.",
    )
    args = parser.parse_args()

    candidates = tuple(args.candidate or DEFAULT_CANDIDATES)
    lora_paths = tuple(args.lora)
    lora_scales = tuple(args.lora_scale)
    if lora_paths and lora_scales and len(lora_paths) != len(lora_scales):
        parser.error("--lora-scale count must match --lora count")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.results.parent.mkdir(parents=True, exist_ok=True)

    base = load_image_gen_config()
    header = {
        "event": "benchmark_start",
        "machine": platform.machine(),
        "system": platform.system(),
        "candidates": candidates,
        "width": args.width,
        "height": args.height,
        "steps": args.steps,
        "quantize": args.quantize,
        "lora_paths": lora_paths,
        "lora_scales": lora_scales,
    }
    with args.results.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(header, sort_keys=True) + "\n")
        print(json.dumps(header, sort_keys=True))
        if args.prefetch_workers > 0:
            for row in _prefetch_candidates(candidates, workers=args.prefetch_workers):
                fh.write(json.dumps(row, sort_keys=True) + "\n")
                fh.flush()
        for candidate in candidates:
            row = _run_candidate(
                base=base,
                candidate=candidate,
                prompt=args.prompt,
                output_dir=args.output_dir,
                width=args.width,
                height=args.height,
                steps=args.steps,
                quantize=args.quantize,
                lora_paths=lora_paths,
                lora_scales=lora_scales,
                seed=args.seed,
            )
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.flush()
            print(json.dumps(row, sort_keys=True))


if __name__ == "__main__":
    main()
