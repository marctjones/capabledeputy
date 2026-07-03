"""Bundled MCP server: local image generation (MLX/MFLUX or SDXL fallback).

Run via:
  capdep mcp-server-image-generate
  python -m capabledeputy.mcp_servers.image_generate
"""

from __future__ import annotations

import asyncio
from typing import Any

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools
from capabledeputy.mcp_servers._image_pipeline import generate_image

SERVER_NAME = "capdep-image-generate"
_GENERATION_LOCK = asyncio.Lock()


async def _generate(args: dict[str, Any]) -> dict[str, Any]:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required")

    seed_raw = args.get("seed")
    seed = int(seed_raw) if seed_raw is not None else None
    width_raw = args.get("width")
    height_raw = args.get("height")
    steps_raw = args.get("steps")

    async with _GENERATION_LOCK:
        return await asyncio.to_thread(
            generate_image,
            prompt=prompt,
            style=str(args.get("style") or "").strip() or None,
            negative_prompt=str(args.get("negative_prompt") or "").strip() or None,
            width=int(width_raw) if width_raw is not None else None,
            height=int(height_raw) if height_raw is not None else None,
            steps=int(steps_raw) if steps_raw is not None else None,
            seed=seed,
            alt=str(args.get("alt") or "").strip() or None,
            filename=str(args.get("filename") or "").strip() or None,
        )


def tools() -> list[ToolDescriptor]:
    return [
        ToolDescriptor(
            name="image.generate",
            description=(
                "Generate a PNG image locally with an operator-selected image model "
                "and return markdown for inline CapDepMac display. Writes to "
                "~/.capdep/work/images/ by default.\n\n"
                "USE THIS WHEN: the user asks to CREATE or GENERATE a new image "
                "(not fetch an existing photo from a website). Call this tool and "
                "include the returned `markdown` in your reply.\n\n"
                "Do NOT use for Wikipedia or website photos — use wikipedia.lookup "
                "and bundled-image-fetch.image.fetch instead.\n\n"
                "Default Mac backend is MLX via MFLUX when available. Diffusers SDXL "
                "remains available as a compatibility fallback. The backend, model, "
                "quantization, LoRAs, and prompt filtering are operator configuration."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": (
                            "Image description. Natural language for photoreal; "
                            "tags optional for graphic_novel."
                        ),
                    },
                    "style": {
                        "type": "string",
                        "enum": ["photoreal", "graphic_novel"],
                        "description": "Checkpoint style (default photoreal).",
                    },
                    "negative_prompt": {
                        "type": "string",
                        "description": "Optional negative prompt.",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Output width in pixels (default 768).",
                    },
                    "height": {
                        "type": "integer",
                        "description": "Output height in pixels (default 768).",
                    },
                    "steps": {
                        "type": "integer",
                        "description": "Diffusion steps (default 20).",
                    },
                    "seed": {
                        "type": "integer",
                        "description": "Optional RNG seed for reproducibility.",
                    },
                    "alt": {
                        "type": "string",
                        "description": "Alt text for inline markdown image.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional output filename (png).",
                    },
                },
                "required": ["prompt"],
            },
            handler=_generate,
            annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
        ),
    ]


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
