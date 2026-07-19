"""Display fix — a successfully generated image must reach the user even when
the model's final text omits it.

Root cause (observed in the wild): an admitted ``image.generate`` succeeded and
wrote a 620KB PNG, but the model's second-pass final text came back empty
(``output_len=0``). ``repair_hallucinated_image_markdown`` no-ops on empty
content (nothing to repair), so the chat showed nothing despite a real image.
``ensure_generated_image_markdown_present`` injects the tool's own markdown so
the client renders the file.
"""

from __future__ import annotations

from capabledeputy.agent.chat_turn import ensure_generated_image_markdown_present
from capabledeputy.policy.rules import Decision
from capabledeputy.tools.client import ToolCallOutcome

_IMG = "bundled-image-generate.image.generate"
_PATH = "~/.capdep/work/images/4a5c08248e304442a14dcc7ff61c31cb.png"
_MD = f"![a topless and attractive woman]({_PATH})"


def _outcome(
    output: dict | None,
    *,
    tool_name: str = _IMG,
    decision: Decision = Decision.ALLOW,
) -> ToolCallOutcome:
    return ToolCallOutcome(decision=decision, output=output, tool_name=tool_name)


def test_empty_final_text_gets_the_generated_image_injected() -> None:
    # The exact production failure: successful generation, empty final text.
    out = [_outcome({"ok": True, "markdown": _MD, "image_path": _PATH})]
    result = ensure_generated_image_markdown_present("", outcomes=out)
    assert result == _MD


def test_prose_without_the_image_gets_it_appended() -> None:
    out = [_outcome({"ok": True, "markdown": _MD, "image_path": _PATH})]
    result = ensure_generated_image_markdown_present("Here you go:", outcomes=out)
    assert result == f"Here you go:\n\n{_MD}"


def test_content_already_showing_the_image_is_unchanged() -> None:
    out = [_outcome({"ok": True, "markdown": _MD, "image_path": _PATH})]
    content = f"Done!\n\n{_MD}"
    assert ensure_generated_image_markdown_present(content, outcomes=out) == content


def test_no_image_outcome_leaves_content_untouched() -> None:
    out = [_outcome({"ok": True, "result": "text"}, tool_name="fs.read")]
    assert ensure_generated_image_markdown_present("hello", outcomes=out) == "hello"


def test_failed_image_outcome_is_not_injected() -> None:
    out = [_outcome({"ok": False, "error": "MFLUX requires MLX/Metal"})]
    assert ensure_generated_image_markdown_present("", outcomes=out) == ""


def test_denied_image_outcome_is_not_injected() -> None:
    out = [_outcome({"ok": True, "markdown": _MD}, decision=Decision.DENY)]
    assert ensure_generated_image_markdown_present("", outcomes=out) == ""


def test_no_outcomes_returns_content_verbatim() -> None:
    assert ensure_generated_image_markdown_present("just text", outcomes=[]) == "just text"


def test_model_cited_a_stale_path_still_gets_the_real_image() -> None:
    # Model pasted a prior/wrong path; the real generated image is different and
    # must still be shown (appended), never silently dropped.
    stale = "![old](~/.capdep/work/images/deadbeef.png)"
    out = [_outcome({"ok": True, "markdown": _MD, "image_path": _PATH})]
    result = ensure_generated_image_markdown_present(stale, outcomes=out)
    assert _MD in result
