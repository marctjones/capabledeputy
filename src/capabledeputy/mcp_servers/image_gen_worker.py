"""One-shot image-generation worker (runs in the isolated `.venv-images`).

The daemon runs in `.venv`, which deliberately does NOT carry the heavy image
backends (mflux / mlx / diffusers) — those live in `.venv-images`. So the daemon
image-job handler must not import `generate_image` in-process; it spawns THIS
worker with `.venv-images/bin/python` instead. Reads a JSON request on stdin,
writes the `generate_image()` result dict as JSON on stdout.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def main() -> None:
    req: dict[str, Any] = json.load(sys.stdin)
    # Imported here so `python -m ... --help`-style probes don't pull the heavy
    # backends, and so an import failure is reported as a faithful result.
    from capabledeputy.mcp_servers._image_pipeline import generate_image, load_image_gen_config

    # The backends (mflux/mlx) print progress + warnings straight to stdout —
    # e.g. a native "⚠️  Model is pre-quantized at 4-bit…" line. This worker's
    # contract is that stdout carries ONLY the JSON result (the daemon runs
    # json.loads on it), so redirect all generation-time output to stderr at the
    # fd level — which also catches native (non-Python) writes — and emit the
    # result dict on the real stdout afterwards.
    saved_stdout_fd = os.dup(1)
    try:
        os.dup2(2, 1)
        result = generate_image(
            prompt=str(req.get("prompt") or ""),
            style=req.get("style"),
            negative_prompt=req.get("negative_prompt"),
            width=req.get("width"),
            height=req.get("height"),
            steps=req.get("steps"),
            seed=req.get("seed"),
            alt=req.get("alt"),
            filename=req.get("filename"),
            config=load_image_gen_config(profile_name=req.get("profile")),
        )
    finally:
        sys.stdout.flush()
        os.dup2(saved_stdout_fd, 1)
        os.close(saved_stdout_fd)
    json.dump(result, sys.stdout)
    sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover
    main()
