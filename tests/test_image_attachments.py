"""Tests for structured image_attachment turn events."""

from __future__ import annotations

from capabledeputy.daemon.image_attachments import image_attachment_payloads_from_outcome


def test_image_attachment_payloads_from_markdown_output() -> None:
    outcome = {
        "output": "Saved chart.\n\n![plot](/tmp/project/chart.png)\n",
        "tool_name": "plot_tool",
    }
    payloads = image_attachment_payloads_from_outcome(outcome)
    paths = {item["path"] for item in payloads}
    assert "/tmp/project/chart.png" in paths


def test_image_attachment_payloads_from_image_path_field() -> None:
    outcome = {
        "output": {"image_path": "/tmp/screenshot.jpg", "alt": "screen"},
    }
    payloads = image_attachment_payloads_from_outcome(outcome)
    assert payloads == [
        {
            "path": "/tmp/screenshot.jpg",
            "alt": "screen",
            "source": "tool_return",
        },
    ]


def test_image_attachment_payloads_deduplicate_paths() -> None:
    outcome = {
        "output": "![a](/tmp/a.png) and ![b](/tmp/a.png)",
    }
    payloads = image_attachment_payloads_from_outcome(outcome)
    assert len(payloads) == 1
