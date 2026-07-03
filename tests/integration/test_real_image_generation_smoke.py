"""Opt-in real image generation smoke.

Collected by the standard suite, skipped unless the operator explicitly enables
real image generation. This exercises the actual configured image backend and
is intentionally not a default CI gate.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from capabledeputy.mcp_servers._image_pipeline import generate_image, image_readiness

pytestmark = pytest.mark.real_image


_ENABLED = pytest.mark.skipif(
    os.environ.get("CAPDEP_REAL_IMAGE_SMOKE") != "1",
    reason="set CAPDEP_REAL_IMAGE_SMOKE=1 to run real image backend smoke",
)


@_ENABLED
def test_real_image_backend_readiness_and_generation() -> None:
    profile = os.environ.get("CAPDEP_REAL_IMAGE_PROFILE") or None
    readiness = image_readiness(profile_name=profile)
    assert readiness["profile"]
    assert readiness["checks"]
    if not readiness["ok"]:
        pytest.skip(f"image backend not ready: {readiness}")

    result = generate_image(
        prompt=os.environ.get("CAPDEP_REAL_IMAGE_PROMPT", "a simple red square icon"),
        alt="real image smoke",
        filename="capdep-real-image-smoke.png",
    )

    assert result["ok"] is True
    path = Path(result["image_path"]).expanduser()
    assert path.is_file()
    assert path.stat().st_size > 1000
    assert str(path) in result["markdown"]
