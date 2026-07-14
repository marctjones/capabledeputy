"""Tests for bundled images MCP server and routing."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capabledeputy.agent.chat_turn import (
    allowed_image_generate_paths,
    collect_prior_work_image_paths,
    has_chart_generation_intent,
    has_image_fetch_intent,
    has_image_generation_intent,
    has_probable_image_generation_intent,
    has_wikipedia_lookup_intent,
    is_conversational_turn,
    looks_like_hallucinated_image_markdown,
    looks_like_image_generation_refusal,
    normalize_image_path,
    repair_hallucinated_image_markdown,
    should_force_image_generate_tool,
)
from capabledeputy.agent.loop import _generated_image_artifacts_from_outcomes
from capabledeputy.daemon.image_attachments import image_attachment_payloads_from_outcome
from capabledeputy.mcp_servers import fetch as fetch_server
from capabledeputy.mcp_servers import image_fetch as image_fetch_server
from capabledeputy.mcp_servers import image_generate as image_generate_server
from capabledeputy.mcp_servers import images as images_server
from capabledeputy.mcp_servers._image_fetch import extract_og_image_url
from capabledeputy.mcp_servers._image_pipeline import (
    ImageGenConfig,
    available_image_profiles,
    clear_pipeline_cache,
    generate_image,
    image_readiness,
    load_image_gen_config,
    validate_prompt,
    wrap_prompt_for_style,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.tools.client import ToolCallOutcome


def _handler(server, name: str) -> Callable[..., Any]:
    for tool in server.tools():
        if tool.name == name:
            return tool.handler
    raise KeyError(name)


def test_split_servers_expose_single_tools() -> None:
    gen_names = {t.name for t in image_generate_server.tools()}
    fetch_names = {t.name for t in image_fetch_server.tools()}
    legacy_names = {t.name for t in images_server.tools()}
    assert gen_names == {"image.generate"}
    assert fetch_names == {"image.fetch"}
    assert legacy_names == {"image.generate", "image.fetch"}


def test_available_image_profiles_surface_asset_readiness(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CAPDEP_MODEL_ASSET_HOME", str(tmp_path / "assets"))
    profiles = {profile["id"]: profile for profile in available_image_profiles()}

    assert profiles["default"]["asset_profile"] == "image.default"
    assert profiles["default"]["asset_readiness"]["status"] == "native"
    assert profiles["sdxl-nsfw"]["asset_readiness"]["status"] == "source_fallback"


def test_image_readiness_includes_model_asset_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CAPDEP_MODEL_ASSET_HOME", str(tmp_path / "assets"))
    readiness = image_readiness(profile_name="default")

    assert readiness["asset_profile"] == "image.default"
    assert readiness["asset_readiness"]["status"] == "native"
    assert any(check["id"] == "model-asset" for check in readiness["checks"])


def test_image_readiness_checks_mlx_metal_on_apple_silicon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "capabledeputy.mcp_servers._image_pipeline._on_apple_silicon",
        lambda: True,
    )
    monkeypatch.setattr(
        "capabledeputy.mcp_servers._image_pipeline._mflux_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "capabledeputy.mcp_servers._image_pipeline._mlx_metal_status",
        lambda: (True, "mlx.core Metal backend available"),
    )

    readiness = image_readiness(profile_name="default")

    assert readiness["backend"] == "mflux"
    mlx_metal = next(check for check in readiness["checks"] if check["id"] == "mlx-metal")
    assert mlx_metal["status"] == "ok"
    assert mlx_metal["detail"] == "mlx.core Metal backend available"


def test_image_readiness_refuses_auto_diffusers_fallback_on_apple_silicon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPDEP_IMAGE_BACKEND", "auto")
    monkeypatch.setattr(
        "capabledeputy.mcp_servers._image_pipeline._on_apple_silicon",
        lambda: True,
    )
    monkeypatch.setattr(
        "capabledeputy.mcp_servers._image_pipeline._mflux_available",
        lambda: False,
    )
    monkeypatch.setattr(
        "capabledeputy.mcp_servers._image_pipeline._mlx_metal_status",
        lambda: (True, "mlx.core Metal backend available"),
    )

    readiness = image_readiness(profile_name="default")

    assert readiness["ok"] is False
    assert readiness["backend"] == "mflux"
    assert any(
        check["id"] == "mlx-metal" and check["status"] == "error" for check in readiness["checks"]
    )
    assert "requires MFLUX with MLX/Metal" in readiness["checks"][1]["detail"]


def test_has_image_generation_intent_detects_scene_requests() -> None:
    assert has_image_generation_intent("generate a photoreal portrait of a blonde woman")
    assert has_image_generation_intent("create an explicit nsfw illustration")
    assert not has_image_generation_intent("generate a line graph of population by decade")


def test_chart_intent_does_not_steal_pure_image_requests() -> None:
    assert has_chart_generation_intent("generate a line graph of sales")
    assert not has_chart_generation_intent("generate an image of a blonde woman")
    assert has_image_generation_intent("generate an image of a blonde woman")


def test_image_intent_disables_conversational_short_circuit() -> None:
    assert not is_conversational_turn("draw a naked woman in a studio")


def test_image_fetch_intent_detects_wikipedia_photo_requests() -> None:
    assert has_image_fetch_intent("show jia lissa image from wikipedia")
    assert not has_image_generation_intent("show jia lissa image from wikipedia")


def test_wikipedia_lookup_intent_detects_info_requests() -> None:
    assert has_wikipedia_lookup_intent("show me information on jia lissa")
    assert has_wikipedia_lookup_intent("who is Ada Lovelace")


def test_style_followups_are_generation_not_conversational() -> None:
    assert has_image_generation_intent("cartoon")
    assert has_image_generation_intent("photorealistic")
    assert not is_conversational_turn("cartoon")
    assert has_image_generation_intent("do a dog instead of a cat")


def test_attractive_woman_inline_phrasing_is_generation_intent() -> None:
    message = "Show me the image of an attractive women inline"
    assert has_image_generation_intent(message)
    assert should_force_image_generate_tool(message)
    assert not is_conversational_turn(message)


def test_show_me_visual_subject_is_generation_intent() -> None:
    message = "show me a black cock"
    assert has_image_generation_intent(message)
    assert should_force_image_generate_tool(message)
    assert has_probable_image_generation_intent(message)
    assert not is_conversational_turn(message)


def test_show_me_info_query_is_not_generation_intent() -> None:
    message = "show me information about Ada Lovelace"
    assert has_wikipedia_lookup_intent(message)
    assert not has_image_generation_intent(message)
    assert not has_probable_image_generation_intent(message)


def test_probable_intent_retries_after_model_refusal() -> None:
    refusal = "I cannot generate or display explicit adult content."
    assert looks_like_image_generation_refusal(refusal)
    assert has_probable_image_generation_intent("show me a black cock")


def test_hallucinated_image_reuses_prior_work_path() -> None:
    dog = "~/.capdep/work/images/dog.png"
    prior = collect_prior_work_image_paths(f"![dog]({dog})")
    woman_md = f"![attractive woman]({dog})"
    assert looks_like_hallucinated_image_markdown(
        woman_md,
        prior_paths=prior,
        allowed_paths=frozenset(),
    )


def test_allowed_work_image_path_is_not_hallucination() -> None:
    dog = "~/.capdep/work/images/dog.png"
    woman = "~/.capdep/work/images/woman.png"
    prior = collect_prior_work_image_paths(f"![dog]({dog})")
    woman_md = f"![woman]({woman})"
    allowed = frozenset({normalize_image_path(woman)})
    assert not looks_like_hallucinated_image_markdown(
        woman_md,
        prior_paths=prior,
        allowed_paths=allowed,
    )


def test_repair_hallucinated_markdown_uses_tool_output() -> None:
    dog = "~/.capdep/work/images/dog.png"
    woman = "~/.capdep/work/images/woman.png"
    tool_md = f"![attractive woman]({woman})"
    prior = collect_prior_work_image_paths(f"![dog]({dog})")
    hallucinated = f"![attractive woman]({dog})"
    outcome = ToolCallOutcome(
        decision=Decision.ALLOW,
        tool_name="bundled-image-generate.image.generate",
        output={
            "image_path": woman,
            "markdown": tool_md,
        },
    )
    allowed = allowed_image_generate_paths([outcome])
    repaired = repair_hallucinated_image_markdown(
        hallucinated,
        prior_paths=prior,
        allowed_paths=allowed,
        outcomes=[outcome],
    )
    assert repaired == tool_md
    assert dog not in repaired


def test_generated_image_outcome_becomes_session_artifact(tmp_path: Path) -> None:
    image_path = tmp_path / ".capdep" / "work" / "images" / "woman.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image-bytes")
    outcome = ToolCallOutcome(
        decision=Decision.ALLOW,
        tool_name="bundled-image-generate.image.generate",
        tool_args={"prompt": "make a portrait"},
        output={
            "image_path": str(image_path),
            "markdown": f"![portrait]({image_path})",
        },
    )

    artifacts = _generated_image_artifacts_from_outcomes([outcome], origin_turn_id=3)

    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "generated_image"
    assert artifacts[0]["path"] == str(image_path)
    assert artifacts[0]["alt"] == "portrait"
    assert artifacts[0]["prompt"] == "make a portrait"
    assert artifacts[0]["origin_turn_id"] == 3
    assert artifacts[0]["sha256"]


def test_looks_like_image_generation_refusal_detects_prose_decline() -> None:
    text = (
        "I cannot generate or display images of real people, including "
        "attractive women, as it may involve non-consensual content."
    )
    assert looks_like_image_generation_refusal(text)
    assert not looks_like_image_generation_refusal(
        '{"tool_calls": [{"name": "bundled-images.image.generate"}]}',
    )


def test_extract_og_image_url_parses_meta_tag() -> None:
    html = '<meta property="og:image" content="https://example.com/p.jpg">'
    assert extract_og_image_url(html) == "https://example.com/p.jpg"


@pytest.mark.asyncio
async def test_image_fetch_handler_returns_markdown(tmp_path: Path) -> None:
    fake = {
        "ok": True,
        "markdown": "![alt](https://example.com/p.jpg)",
        "final_url": "https://example.com/p.jpg",
        "content": "Fetched image.",
    }
    with patch(
        "capabledeputy.mcp_servers.image_fetch.fetch_image",
        new_callable=AsyncMock,
        return_value=fake,
    ):
        result = await _handler(image_fetch_server, "image.fetch")(
            {"url": "https://example.com/p.jpg"},
        )
    assert result["ok"] is True
    assert "markdown" in result


@pytest.mark.asyncio
async def test_wikipedia_lookup_handler_returns_summary() -> None:
    fake = {
        "ok": True,
        "title": "Cat",
        "summary": "Small carnivorous mammal.",
        "page_url": "https://en.wikipedia.org/wiki/Cat",
        "image_url": "https://upload.wikimedia.org/cat.jpg",
        "markdown_image": "![Cat](https://upload.wikimedia.org/cat.jpg)",
        "content": "Cat summary",
    }
    fetch_handler = next(t.handler for t in fetch_server.tools() if t.name == "wikipedia.lookup")
    with patch(
        "capabledeputy.mcp_servers.fetch.wikipedia_lookup",
        return_value=fake,
    ):
        result: Any = await fetch_handler({"title": "Cat"})
    assert result["ok"] is True
    assert result["summary"]


def test_validate_prompt_filter_is_operator_controlled() -> None:
    assert validate_prompt("a child in a park") is None
    assert validate_prompt("a child in a park", enabled=True) is not None
    assert validate_prompt("consenting adult woman portrait") is None


def test_available_image_profiles_describe_benchmark_defaults() -> None:
    profiles = {profile["id"]: profile for profile in available_image_profiles()}

    assert profiles["default"]["recommended"] is True
    assert profiles["default"]["backend"] == "mflux"
    assert profiles["default"]["model"] == "z-image-turbo"
    assert profiles["balanced"]["steps"] > profiles["fast"]["steps"]
    assert profiles["quality"]["slow"] is True
    assert profiles["quality"]["asset_profile"] == "image.flux2-klein-quality"
    assert profiles["quality-flux2"]["model"] == "flux2-klein-4b"
    assert profiles["quality-qwen"]["model"] == "qwen-image"
    assert profiles["quality-qwen"]["asset_profile"] == "image.qwen-image-quality"
    assert profiles["sdxl-nsfw"]["backend"] == "diffusers"
    assert profiles["sdxl-nsfw"]["model"] == "photoreal"


def test_load_image_gen_config_uses_profile_and_ignores_empty_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPDEP_IMAGE_MODEL_PATH", "")

    config = load_image_gen_config(profile_name="balanced")

    assert config.backend == "mflux"
    assert config.model == "z-image-turbo"
    assert config.model_path == "filipstrand/Z-Image-Turbo-mflux-4bit"
    assert config.default_steps == 12


def test_image_readiness_reports_profile_errors() -> None:
    readiness = image_readiness(profile_name="not-real")

    assert readiness["ok"] is False
    assert readiness["checks"][0]["id"] == "profile"


def test_wrap_prompt_for_graphic_novel_adds_pony_tags() -> None:
    wrapped = wrap_prompt_for_style("1girl, blonde hair", "graphic_novel")
    assert wrapped.startswith("score_9")
    assert "1girl" in wrapped


def test_image_profile_flux_nsfw_selects_mflux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_IMAGE_PROFILE", "flux-nsfw")
    monkeypatch.delenv("CAPDEP_IMAGE_BACKEND", raising=False)
    monkeypatch.delenv("CAPDEP_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("CAPDEP_IMAGE_GUIDANCE", raising=False)

    config = load_image_gen_config()

    assert config.backend == "mflux"
    assert config.model == "schnell"
    assert config.guidance == 1.0
    assert config.quantize == 8


def test_image_profile_pony_nsfw_selects_diffusers_graphic_novel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPDEP_IMAGE_PROFILE", "pony-nsfw")
    monkeypatch.delenv("CAPDEP_IMAGE_BACKEND", raising=False)
    monkeypatch.delenv("CAPDEP_IMAGE_STYLE", raising=False)
    monkeypatch.delenv("CAPDEP_IMAGE_STEPS", raising=False)

    config = load_image_gen_config()

    assert config.backend == "diffusers"
    assert config.default_style == "graphic_novel"
    assert config.default_steps == 24


def test_image_profile_fallback_accepts_local_checkpoint_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "adult-fallback.safetensors"
    monkeypatch.setenv("CAPDEP_IMAGE_PROFILE", "sdxl-nsfw")
    monkeypatch.setenv("CAPDEP_IMAGE_CHECKPOINT_PATH", str(checkpoint))
    monkeypatch.delenv("CAPDEP_IMAGE_BACKEND", raising=False)

    config = load_image_gen_config()

    assert config.backend == "diffusers"
    assert config.checkpoints["photoreal"]["path"] == str(checkpoint)


@pytest.mark.asyncio
async def test_image_generate_handler_returns_markdown(tmp_path: Path) -> None:
    fake_out = {
        "ok": True,
        "style": "photoreal",
        "image_path": str(tmp_path / "out.png"),
        "markdown": f"![alt]({tmp_path / 'out.png'})",
        "content": "Generated photoreal image.",
    }
    with patch(
        "capabledeputy.mcp_servers.image_generate.generate_image",
        return_value=fake_out,
    ):
        result = await _handler(image_generate_server, "image.generate")(
            {"prompt": "studio portrait"},
        )
    assert result["ok"] is True
    assert "markdown" in result


@pytest.mark.asyncio
async def test_image_generate_handler_serializes_concurrent_requests(tmp_path: Path) -> None:
    active = 0
    max_active = 0
    guard = threading.Lock()

    def fake_generate_image(*, prompt: str, **_: object) -> dict[str, object]:
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return {
            "ok": True,
            "style": "photoreal",
            "image_path": str(tmp_path / f"{prompt}.png"),
            "markdown": f"![{prompt}]({tmp_path / f'{prompt}.png'})",
            "content": "Generated photoreal image.",
        }

    handler = _handler(image_generate_server, "image.generate")
    with patch(
        "capabledeputy.mcp_servers.image_generate.generate_image",
        side_effect=fake_generate_image,
    ):
        first, second = await asyncio.gather(
            handler({"prompt": "first"}),
            handler({"prompt": "second"}),
        )

    assert first["ok"] is True
    assert second["ok"] is True
    assert max_active == 1


@pytest.mark.asyncio
async def test_image_generate_handler_requires_prompt() -> None:
    with pytest.raises(ValueError, match="prompt is required"):
        await _handler(image_generate_server, "image.generate")({})


def test_generate_image_mocked_pipeline(tmp_path: Path) -> None:
    clear_pipeline_cache()
    config = ImageGenConfig(
        output_dir=tmp_path,
        backend="diffusers",
        default_style="photoreal",
        model="z-image-turbo",
        model_path=None,
        quantize=None,
        lora_paths=(),
        lora_scales=(),
        guidance=None,
        device="cpu",
        default_width=512,
        default_height=512,
        default_steps=4,
        safety_enabled=False,
        prompt_filter_enabled=False,
        checkpoints={
            "photoreal": {
                "repo": "RunDiffusion/Juggernaut-XL-v9",
                "filename": "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors",
            },
            "graphic_novel": {
                "repo": "LyliaEngine/Pony_Diffusion_V6_XL",
                "filename": "ponyDiffusionV6XL_v6StartWithThisOne.safetensors",
            },
        },
    )

    fake_image = MagicMock()
    fake_pipe = MagicMock()
    fake_pipe.return_value.images = [fake_image]

    mock_torch = MagicMock()
    mock_torch.Generator.return_value.manual_seed.return_value = "gen"
    with (
        patch(
            "capabledeputy.mcp_servers._image_pipeline._load_pipeline",
            return_value=fake_pipe,
        ),
        patch.dict(sys.modules, {"torch": mock_torch}),
    ):
        out = generate_image(
            prompt="consenting adult portrait",
            config=config,
            filename="test.png",
        )

    assert out["ok"] is True
    assert out["image_path"].endswith("test.png")
    assert "![consenting adult portrait]" in out["markdown"]
    fake_image.save.assert_called_once()

    payloads = image_attachment_payloads_from_outcome({"output": out})
    assert payloads and payloads[0]["path"] == out["image_path"]
    clear_pipeline_cache()


def test_generate_image_auto_refuses_without_mflux_mlx_on_apple_silicon(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "capabledeputy.mcp_servers._image_pipeline._on_apple_silicon",
        lambda: True,
    )
    monkeypatch.setattr(
        "capabledeputy.mcp_servers._image_pipeline._mflux_available",
        lambda: False,
    )
    monkeypatch.setattr(
        "capabledeputy.mcp_servers._image_pipeline._mlx_metal_status",
        lambda: (True, "mlx.core Metal backend available"),
    )
    config = ImageGenConfig(
        output_dir=tmp_path,
        backend="auto",
        default_style="photoreal",
        model="z-image-turbo",
        model_path=None,
        quantize=8,
        lora_paths=(),
        lora_scales=(),
        guidance=None,
        device="mps",
        default_width=512,
        default_height=512,
        default_steps=4,
        safety_enabled=False,
        prompt_filter_enabled=False,
        checkpoints={},
    )

    out = generate_image(prompt="operator selected local image prompt", config=config)

    assert out["ok"] is False
    assert out["backend"] == "mflux"
    assert "requires MFLUX with MLX/Metal" in out["error"]


def test_generate_image_mocked_mflux_backend(tmp_path: Path) -> None:
    clear_pipeline_cache()
    config = ImageGenConfig(
        output_dir=tmp_path,
        backend="mflux",
        default_style="photoreal",
        model="z-image-turbo",
        model_path="filipstrand/Z-Image-Turbo-mflux-4bit",
        quantize=8,
        lora_paths=(),
        lora_scales=(),
        guidance=None,
        device="mps",
        default_width=512,
        default_height=512,
        default_steps=9,
        safety_enabled=False,
        prompt_filter_enabled=False,
        checkpoints={},
    )

    fake_image = MagicMock()
    fake_model = MagicMock()
    fake_model.generate_image.return_value = fake_image

    with patch(
        "capabledeputy.mcp_servers._image_pipeline._load_mflux_model",
        return_value=fake_model,
    ):
        out = generate_image(
            prompt="operator selected local image prompt",
            config=config,
            filename="mlx.png",
        )

    assert out["ok"] is True
    assert out["backend"] == "mflux"
    assert out["model"] == "z-image-turbo"
    assert out["quantize"] == 8
    fake_model.generate_image.assert_called_once()
    fake_image.save.assert_called_once()
    clear_pipeline_cache()
