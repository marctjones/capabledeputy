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

# CapDep does NOT content-filter image generation — no dial is posture-forced.
#
# #416 removed forced PROMPT filtering; #428 removes forced OUTPUT filtering.
# docs/governance-scope.md: CapDep governs effects/flows structurally and is
# "silent by design on content safety". #330 forced both the prompt filter and
# the output image-safety checker on — content moderation, out of the stated
# scope. Both dials now default off (see `_image_pipeline.load_image_gen_config`)
# and remain off-by-default, opt-in operator dials; no posture forces them.
#
# This helper (and the posture `image_filters` forcing that calls it) is now
# vestigial — retained returning {} pending removal of the #330 scaffolding.
_FORCED_IMAGE_ENV: dict[str, str] = {}

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
