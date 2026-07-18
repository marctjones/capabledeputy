"""#330 (spike #317) — bridge the active security posture to the image-generation
subprocess safety dials.

The bundled image generator is a separate MCP subprocess that reads its own
`CAPDEP_IMAGE_*` env at startup. A posture that FORCES image safety on
(strict / high-security-useful) must win over whatever the on-disk daemon config
says — otherwise a stale `off` in the config would defeat the floor. This module
holds the pure, dependency-light helpers; `daemon/lifecycle.py` applies them to
the upstream server configs after posture resolution.

Only `forced_on` postures override here. `default_on_optout_ok` needs no
override: the code default (`_image_pipeline.py`) and the shipped managed config
already default the dials ON, and an explicit operator opt-out in that one
permissive posture is deliberately left in place.
"""

from __future__ import annotations

from collections.abc import Sequence

# Forced ON by a `forced_on` posture: the OUTPUT image-safety checker only.
#
# #416 — CapDep does NOT content-filter prompts (docs/governance-scope.md: it
# governs effects/flows structurally and is "silent by design on content
# safety"). #330 originally also forced `CAPDEP_IMAGE_PROMPT_FILTER` on, i.e.
# imposed prompt content moderation — out of the stated scope. That dial is no
# longer forced (and defaults off, see `_image_pipeline.load_image_gen_config`);
# it remains an off-by-default, opt-in operator filter. The output-safety dial
# (`CAPDEP_IMAGE_SAFETY`) is unchanged.
_FORCED_IMAGE_ENV = {"CAPDEP_IMAGE_SAFETY": "on"}

# Command heads / module markers that identify an image-GENERATION server (the
# one governed by the safety dials). image-fetch is a downloader, not a
# generator, so it is intentionally NOT matched.
_IMAGE_GEN_COMMAND_HEADS = frozenset({"capdep-image-generate", "capdep-images"})
_IMAGE_GEN_MODULE_MARKERS = (
    "mcp-server-image-generate",
    "mcp-server-images",
    "capabledeputy.mcp_servers.image_generate",
    "capabledeputy.mcp_servers.images",
)


def is_image_generation_command(command: Sequence[str]) -> bool:
    """True when `command` launches the bundled image-GENERATION server (either
    an unresolved `capdep-image-*` placeholder or a resolved module invocation)."""
    if not command:
        return False
    if command[0] in _IMAGE_GEN_COMMAND_HEADS:
        return True
    return any(any(marker in str(part) for marker in _IMAGE_GEN_MODULE_MARKERS) for part in command)


def forced_image_safety_env() -> dict[str, str]:
    """The env overrides a `forced_on` posture injects into the image server."""
    return dict(_FORCED_IMAGE_ENV)
