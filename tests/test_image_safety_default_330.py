"""#330 (spike #317) — image-generation ships safe-by-default, posture-tiered:
strict / high-security-useful force the dials ON (non-negotiable); low-friction
defaults on with an explicit opt-out; unknown posture fails closed; absent env
means ON (no unsafe-by-omission surface)."""

from __future__ import annotations

import pytest

from capabledeputy.daemon.lifecycle import _apply_image_safety_floor
from capabledeputy.policy.image_safety import (
    forced_image_safety_env,
    is_image_generation_command,
)
from capabledeputy.policy.posture import (
    BUILTIN_POSTURES,
    DEFAULT_POSTURE,
    IMAGE_SAFETY_DEFAULT_ON_OPTOUT_OK,
    IMAGE_SAFETY_FORCED_ON,
    Posture,
    PostureError,
)
from capabledeputy.upstream.config import UpstreamServerConfig

# --- posture semantics -------------------------------------------------------


def test_preset_image_filters_are_posture_tiered() -> None:
    assert BUILTIN_POSTURES["strict"].image_filters == IMAGE_SAFETY_FORCED_ON
    assert BUILTIN_POSTURES["high-security-useful"].image_filters == IMAGE_SAFETY_FORCED_ON
    assert (
        BUILTIN_POSTURES["low-friction-practical"].image_filters
        == IMAGE_SAFETY_DEFAULT_ON_OPTOUT_OK
    )
    assert BUILTIN_POSTURES["strict"].image_safety_forced() is True
    assert BUILTIN_POSTURES["low-friction-practical"].image_safety_forced() is False


def test_default_posture_is_safe_by_default() -> None:
    # An unconfigured runtime defaults on (opt-out permitted), never off.
    assert DEFAULT_POSTURE.image_filters == IMAGE_SAFETY_DEFAULT_ON_OPTOUT_OK


def test_validate_rejects_off_or_unknown_image_filters() -> None:
    with pytest.raises(PostureError, match="image_filters"):
        Posture(id="bad", image_filters="off").validate()
    with pytest.raises(PostureError, match="image_filters"):
        Posture(id="bad2", image_filters="whatever").validate()


def test_custom_posture_may_force_or_default_but_not_disable() -> None:
    # Both on-by-default modes are allowed for a custom posture.
    assert Posture(id="c1", image_filters=IMAGE_SAFETY_FORCED_ON).validate().image_safety_forced()
    assert not (
        Posture(id="c2", image_filters=IMAGE_SAFETY_DEFAULT_ON_OPTOUT_OK)
        .validate()
        .image_safety_forced()
    )


# --- image-server detection --------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ("capdep-image-generate", "mcp-server-image-generate"),
        ("capdep-images",),
        ("python", "-m", "capabledeputy.mcp_servers.image_generate"),
        ("/usr/bin/capdep", "mcp-server-images"),
    ],
)
def test_detects_image_generation_commands(command: tuple[str, ...]) -> None:
    assert is_image_generation_command(command)


@pytest.mark.parametrize(
    "command",
    [
        (),
        ("capdep-image-fetch",),  # downloader, NOT a generator
        ("python", "-m", "capabledeputy.mcp_servers.other"),
        ("uvx", "mcp-server-fetch"),
    ],
)
def test_non_image_generation_commands_are_ignored(command: tuple[str, ...]) -> None:
    assert not is_image_generation_command(command)


# --- the forced-on bridge ----------------------------------------------------


class _Ctx:
    def __init__(self, posture: Posture | None) -> None:
        self.active_posture = posture


def _img_cfg(env: dict[str, str] | None = None) -> UpstreamServerConfig:
    return UpstreamServerConfig(
        name="bundled-image-generate",
        command=("capdep-image-generate", "mcp-server-image-generate"),
        env=env or {},
    )


def test_no_posture_forces_image_content_filtering() -> None:
    # #416 + #428 — CapDep does not content-filter image generation; NO posture
    # forces any dial. A forced_on posture leaves the config values exactly as-is.
    cfg = _img_cfg({"CAPDEP_IMAGE_SAFETY": "off", "CAPDEP_IMAGE_PROMPT_FILTER": "off"})
    out = _apply_image_safety_floor([cfg], _Ctx(BUILTIN_POSTURES["strict"]))
    assert out[0].env["CAPDEP_IMAGE_SAFETY"] == "off"  # untouched
    assert out[0].env["CAPDEP_IMAGE_PROMPT_FILTER"] == "off"  # untouched
    assert forced_image_safety_env() == {}


def test_non_forcing_posture_leaves_optout_in_place() -> None:
    # low-friction honors the operator's explicit opt-out (no override).
    cfg = _img_cfg({"CAPDEP_IMAGE_SAFETY": "off"})
    out = _apply_image_safety_floor([cfg], _Ctx(BUILTIN_POSTURES["low-friction-practical"]))
    assert out[0].env["CAPDEP_IMAGE_SAFETY"] == "off"  # untouched


def test_no_posture_is_a_noop() -> None:
    cfg = _img_cfg({"CAPDEP_IMAGE_SAFETY": "off"})
    out = _apply_image_safety_floor([cfg], _Ctx(None))
    assert out[0].env["CAPDEP_IMAGE_SAFETY"] == "off"


def test_forced_posture_does_not_touch_non_image_servers() -> None:
    other = UpstreamServerConfig(name="fetch", command=("uvx", "mcp-server-fetch"), env={})
    out = _apply_image_safety_floor([other], _Ctx(BUILTIN_POSTURES["strict"]))
    assert "CAPDEP_IMAGE_SAFETY" not in out[0].env


# --- safe-by-omission code default -------------------------------------------


def test_absent_env_no_image_content_filtering(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CAPDEP_IMAGE_SAFETY", "CAPDEP_IMAGE_PROMPT_FILTER"):
        monkeypatch.delenv(var, raising=False)
    from capabledeputy.mcp_servers._image_pipeline import load_image_gen_config

    cfg = load_image_gen_config()
    # #428 — OUTPUT safety off by default; the model's real output passes through.
    assert cfg.safety_enabled is False
    # #416 — PROMPT filter off by default; prompts pass through unmodified.
    assert cfg.prompt_filter_enabled is False


def test_on_off_tokens_parse_for_the_opt_in_dials(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dials are now off-by-default, opt-in operator controls. Pin the
    producer→consumer parse contract in BOTH directions so an operator who
    explicitly opts in ('on') really enables, and 'off' really disables."""
    from capabledeputy.mcp_servers._image_pipeline import load_image_gen_config

    monkeypatch.setenv("CAPDEP_IMAGE_SAFETY", "on")
    monkeypatch.setenv("CAPDEP_IMAGE_PROMPT_FILTER", "on")
    on = load_image_gen_config()
    assert on.safety_enabled is True  # explicit opt-in really enables
    assert on.prompt_filter_enabled is True

    monkeypatch.setenv("CAPDEP_IMAGE_SAFETY", "off")
    monkeypatch.setenv("CAPDEP_IMAGE_PROMPT_FILTER", "off")
    off = load_image_gen_config()
    assert off.safety_enabled is False  # the low-friction opt-out really disables
    assert off.prompt_filter_enabled is False


# --- shipped managed config --------------------------------------------------


def test_shipped_managed_config_ships_image_filters_off() -> None:
    from capabledeputy.cli import _managed_config as mc

    for body in (mc.BUNDLED_IMAGE_GENERATE_BLOCK_BODY, mc.BUNDLED_IMAGES_BLOCK_BODY):
        # #428 + #416 — CapDep does not content-filter image generation: the
        # shipped config disables BOTH the output checker and the prompt filter.
        assert 'CAPDEP_IMAGE_SAFETY: "off"' in body
        assert 'CAPDEP_IMAGE_SAFETY: "on"' not in body
        assert 'CAPDEP_IMAGE_PROMPT_FILTER: "off"' in body
        assert 'CAPDEP_IMAGE_PROMPT_FILTER: "on"' not in body
