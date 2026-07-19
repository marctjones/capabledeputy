"""One-shot image-generation worker (runs in the isolated `.venv-images`).

The daemon runs in `.venv`, which deliberately does NOT carry the heavy image
backends (mflux / mlx / diffusers) — those live in `.venv-images`. So the daemon
image-job handler must not import `generate_image` in-process; it spawns THIS
worker with `.venv-images/bin/python` instead. Reads a JSON request on stdin,
writes the `generate_image()` result dict as JSON on stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def main() -> None:
    req: dict[str, Any] = json.load(sys.stdin)
    # Imported here so `python -m ... --help`-style probes don't pull the heavy
    # backends, and so an import failure is reported as a faithful result.
    from capabledeputy.mcp_servers._image_pipeline import generate_image, load_image_gen_config

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
    json.dump(result, sys.stdout)


if __name__ == "__main__":  # pragma: no cover
    main()
