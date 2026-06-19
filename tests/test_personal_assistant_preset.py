"""Regression tests for the macOS/Google personal-assistant preset."""

from __future__ import annotations

from pathlib import Path

from capabledeputy.policy.bindings import load as load_bindings
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.purposes import load as load_purposes
from capabledeputy.upstream.config import load_config_file

_PRESET = Path(__file__).parent.parent / "configs" / "personal-assistant"


def _cap_pairs(purpose_id: str) -> set[tuple[CapabilityKind, str]]:
    purposes = load_purposes(_PRESET / "purposes.yaml")
    purpose = purposes.get(purpose_id)
    assert purpose is not None
    return {(cap.kind, cap.pattern) for cap in purpose.default_capabilities}


def test_personal_assistant_daemon_uses_official_google_and_macos_servers() -> None:
    configs = load_config_file(_PRESET / "daemon.yaml")
    names = {config.name for config in configs}

    assert "gws" not in names
    assert {
        "bundled-apple-mail",
        "bundled-keynote",
        "bundled-pages",
        "bundled-numbers",
        "bundled-macos",
        "google-gmail",
        "google-drive",
        "google-calendar",
        "google-chat",
        "google-people",
    } <= names
    assert all(config.strict is True for config in configs)

    gmail = next(config for config in configs if config.name == "google-gmail")
    assert gmail.transport == "streamable_http"
    assert gmail.auth is not None
    assert gmail.auth.type == "oauth2"
    assert gmail.auth.client_id_env == "GOOGLE_MCP_CLIENT_ID"
    assert "SEND_EMAIL" in gmail.disabled_kinds
    assert gmail.tool_overrides["create_draft"].capability_kind == CapabilityKind.GMAIL_DRAFT

    pages = next(config for config in configs if config.name == "bundled-pages")
    assert pages.tool_overrides["pages.append_text"].capability_kind == CapabilityKind.PAGES_EDIT
    numbers = next(config for config in configs if config.name == "bundled-numbers")
    assert (
        numbers.tool_overrides["numbers.set_cell_value"].capability_kind
        == CapabilityKind.NUMBERS_EDIT
    )


def test_personal_assistant_purposes_are_macos_google_and_apple_ready() -> None:
    combined = (
        (_PRESET / "purposes.yaml").read_text(encoding="utf-8")
        + "\n"
        + (_PRESET / "source_bindings.yaml").read_text(encoding="utf-8")
    )
    assert "/home/" not in combined
    assert "/Users/*/" in combined

    general = _cap_pairs("general")
    assert (CapabilityKind.READ_FS, "/Users/*/Documents/**") in general
    assert (CapabilityKind.READ_FS, "/Users/*/Documents/GitHub/**") in general
    assert (CapabilityKind.GMAIL_READ, "*") in general
    assert (CapabilityKind.DRIVE_READ, "*") in general
    assert (CapabilityKind.APPLE_MAIL_READ, "*") in general
    assert (CapabilityKind.PAGES_READ, "*") in general
    assert (CapabilityKind.NUMBERS_READ, "*") in general
    assert (CapabilityKind.MACOS_CLIPBOARD_READ, "*") in general

    inbox = _cap_pairs("inbox")
    assert (CapabilityKind.GMAIL_DRAFT, "*") in inbox
    assert (CapabilityKind.APPLE_MAIL_DRAFT, "*") in inbox
    assert (CapabilityKind.PEOPLE_READ, "*") in inbox

    writing = _cap_pairs("writing")
    assert (CapabilityKind.PAGES_EDIT, "*") in writing
    assert (CapabilityKind.PAGES_EXPORT, "*") in writing
    assert (CapabilityKind.KEYNOTE_READ, "*") in writing


def test_personal_assistant_source_bindings_cover_service_uri_schemes() -> None:
    bindings = load_bindings(_PRESET / "source_bindings.yaml")

    assert (
        bindings.resolve("file:///Users/marc/Documents/GitHub/capdep/README.md").category == "code"
    )
    assert bindings.resolve("file:///Users/marc/Desktop/todo.txt").category == "personal"
    assert bindings.resolve("gmail://thread/123").category == "email"
    assert bindings.resolve("gdrive://file/abc").category == "personal"
    assert bindings.resolve("gcal://primary/event/abc").category == "personal"
    assert bindings.resolve("gchat://spaces/abc/messages/def").category == "work"
    assert bindings.resolve("people://contacts/abc").category == "personal"
    assert bindings.resolve("applemail://inbox/message/123").category == "email"
    assert bindings.resolve("pages://frontmost").category == "personal"
    assert bindings.resolve("numbers://frontmost").category == "personal"
    assert bindings.resolve("keynote://frontmost").category == "work"
