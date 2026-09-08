"""Regression tests for the macOS personal-assistant preset."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from capabledeputy.policy.bindings import load as load_bindings
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.decision_inspector_loader import load_decision_inspectors
from capabledeputy.policy.purposes import load as load_purposes
from capabledeputy.policy.relationships import load as load_relationship_groups
from capabledeputy.upstream.config import load_config_file

_PRESET = Path(__file__).parent.parent / "configs" / "personal-assistant"


def _cap_pairs(purpose_id: str) -> set[tuple[CapabilityKind, str]]:
    purposes = load_purposes(_PRESET / "purposes.yaml")
    purpose = purposes.get(purpose_id)
    assert purpose is not None
    pairs: set[tuple[CapabilityKind, str]] = set()
    for cap in purpose.default_capabilities:
        assert isinstance(cap.kind, CapabilityKind)
        pairs.add((cap.kind, cap.pattern))
    return pairs


def test_personal_assistant_daemon_uses_bundled_and_macos_servers() -> None:
    configs = load_config_file(_PRESET / "daemon.yaml")
    names = {config.name for config in configs}

    assert "gws" not in names
    assert not any(name.startswith("google-") for name in names)
    assert {
        "bundled-apple-mail",
        "bundled-keynote",
        "bundled-pages",
        "bundled-numbers",
        "bundled-macos",
        "bundled-outlook",
        "bundled-word",
        "bundled-powerpoint",
    } <= names
    assert all(config.strict is True for config in configs)

    apple_mail = next(config for config in configs if config.name == "bundled-apple-mail")
    assert apple_mail.tool_overrides["apple_mail.create_draft"].target_arg == "to"
    assert (
        apple_mail.tool_overrides["apple_mail.get_message"].target_template
        == "applemail://mailbox/{mailbox_name}/message/{message_id}"
    )
    pages = next(config for config in configs if config.name == "bundled-pages")
    assert pages.tool_overrides["pages.append_text"].capability_kind == CapabilityKind.PAGES_EDIT
    assert pages.tool_overrides["pages.append_text"].target_template == "pages://frontmost"
    assert pages.tool_overrides["pages.export_pdf"].target_arg == "path"
    numbers = next(config for config in configs if config.name == "bundled-numbers")
    assert (
        numbers.tool_overrides["numbers.set_cell_value"].capability_kind
        == CapabilityKind.NUMBERS_EDIT
    )
    assert numbers.tool_overrides["numbers.export_pdf"].target_arg == "path"

    macos = next(config for config in configs if config.name == "bundled-macos")
    assert macos.tool_overrides["macos.open_application"].target_template == (
        "macos://app/{bundle_id}"
    )
    assert macos.tool_overrides["macos.get_clipboard_text"].target_template == "macos://clipboard"

    outlook = next(config for config in configs if config.name == "bundled-outlook")
    assert outlook.tool_overrides["outlook.create_draft"].target_arg == "to"
    word = next(config for config in configs if config.name == "bundled-word")
    assert word.tool_overrides["word.append_text"].capability_kind == CapabilityKind.WORD_EDIT
    assert word.tool_overrides["word.export_pdf"].target_arg == "path"
    powerpoint = next(config for config in configs if config.name == "bundled-powerpoint")
    assert (
        powerpoint.tool_overrides["powerpoint.append_speaker_notes"].capability_kind
        == CapabilityKind.POWERPOINT_EDIT
    )
    assert (
        powerpoint.tool_overrides["powerpoint.start_slideshow"].capability_kind
        == CapabilityKind.POWERPOINT_PRESENT
    )

    # A tool_override with no target_arg/target_template falls back to
    # `args["target"]` (adapter.py), which fs/git/memory/fetch tools never
    # set — the capability check then scopes against an empty target, so a
    # path-scoped grant (e.g. the foreground chat defaults' READ_FS on
    # `~/Desktop/*`) can never match and every call is silently denied.
    fs = next(config for config in configs if config.name == "bundled-fs")
    for tool in ("fs.read", "fs.list", "fs.create", "fs.write", "fs.delete"):
        assert fs.tool_overrides[tool].target_arg == "path", tool
    fetch = next(config for config in configs if config.name == "bundled-fetch")
    assert fetch.tool_overrides["fetch.get"].target_arg == "url"
    git = next(config for config in configs if config.name == "bundled-git")
    for tool in ("git.status", "git.log", "git.diff", "git.show", "git.branch_list"):
        assert git.tool_overrides[tool].target_arg == "repo_path", tool
    memory = next(config for config in configs if config.name == "bundled-memory")
    for tool in ("memory.create", "memory.read", "memory.update", "memory.delete"):
        assert memory.tool_overrides[tool].target_arg == "key", tool
    assert memory.tool_overrides["memory.list"].target_arg == "prefix"
    # Memory keys are arbitrary strings, not filesystem paths — dedicated
    # kinds so a memory grant can't also widen filesystem authority.
    assert memory.tool_overrides["memory.create"].capability_kind == CapabilityKind.MEMORY_CREATE
    assert memory.tool_overrides["memory.read"].capability_kind == CapabilityKind.MEMORY_READ
    assert memory.tool_overrides["memory.update"].capability_kind == CapabilityKind.MEMORY_MODIFY
    assert memory.tool_overrides["memory.delete"].capability_kind == CapabilityKind.MEMORY_DELETE
    assert memory.tool_overrides["memory.list"].capability_kind == CapabilityKind.MEMORY_READ


def test_personal_assistant_enables_conservative_starlark_inspectors() -> None:
    raw = yaml.safe_load((_PRESET / "daemon.yaml").read_text(encoding="utf-8"))
    entries = raw["decision_inspectors"]
    scripts = [entry["script"] for entry in entries]

    assert scripts == [
        "../policies/sensitive_egress_confirm.star",
        "../policies/local_app_confirm.star",
        "../policies/frequency_cap.star",
        "../policies/onguard_declared_workflows.star",
        "../policies/onguard_sensitive_publish_confirm.star",
        "../policies/onguard_low_integrity_suggestions.star",
    ]
    assert "../policies/purpose_scoped_relax.star" not in scripts
    assert "../policies/relationship_relax.star" not in scripts
    assert all(entry["runtime"] == "starlark" for entry in entries)
    assert all(entry["failure_mode"] == "require_approval" for entry in entries)

    pytest.importorskip("starlark", reason="requires the capabledeputy[starlark] extra")
    inspectors = load_decision_inspectors(raw, base_dir=_PRESET)
    assert [inspector.name for inspector in inspectors] == [
        "sensitive_egress_confirm",
        "local_app_confirm",
        "frequency_cap",
        "onguard_declared_workflows",
        "onguard_sensitive_publish_confirm",
        "onguard_low_integrity_suggestions",
    ]


def test_personal_assistant_purposes_are_macos_and_apple_ready() -> None:
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
    assert (CapabilityKind.CLOUD_FILE_READ, "*") in general
    assert (CapabilityKind.APPLE_MAIL_READ, "*") in general
    assert (CapabilityKind.OUTLOOK_READ, "*") in general
    assert (CapabilityKind.PAGES_READ, "*") in general
    assert (CapabilityKind.WORD_READ, "*") in general
    assert (CapabilityKind.NUMBERS_READ, "*") in general
    assert (CapabilityKind.POWERPOINT_READ, "*") in general
    assert (CapabilityKind.MACOS_CLIPBOARD_READ, "*") in general

    inbox = _cap_pairs("inbox")
    assert (CapabilityKind.EXTERNAL_MAIL_DRAFT, "*") in inbox
    assert (CapabilityKind.IMAP_READ, "*") in inbox
    assert (CapabilityKind.APPLE_MAIL_DRAFT, "*") in inbox
    assert (CapabilityKind.OUTLOOK_DRAFT, "*") in inbox
    assert (CapabilityKind.CLOUD_FILE_READ, "*") in inbox

    calendar = _cap_pairs("calendar")
    assert (CapabilityKind.CALENDAR_READ, "*") in calendar
    assert (CapabilityKind.CREATE_CAL, "*") in calendar
    assert (CapabilityKind.MODIFY_CAL, "*") in calendar
    assert (CapabilityKind.CLOUD_FILE_READ, "*") in calendar

    writing = _cap_pairs("writing")
    assert (CapabilityKind.PAGES_EDIT, "*") in writing
    assert (CapabilityKind.PAGES_EXPORT, "*") in writing
    assert (CapabilityKind.WORD_EDIT, "*") in writing
    assert (CapabilityKind.WORD_EXPORT, "*") in writing
    assert (CapabilityKind.KEYNOTE_READ, "*") in writing
    assert (CapabilityKind.POWERPOINT_EDIT, "*") in writing
    assert (CapabilityKind.POWERPOINT_EXPORT, "*") in writing

    research = _cap_pairs("research")
    assert (CapabilityKind.WEB_FETCH, "*") in research
    assert (CapabilityKind.BROWSER_READ, "https://*") in research


def test_personal_assistant_source_bindings_cover_service_uri_schemes() -> None:
    bindings = load_bindings(_PRESET / "source_bindings.yaml")

    assert (
        bindings.resolve("file:///Users/marc/Documents/GitHub/capdep/README.md").category == "code"
    )
    assert bindings.resolve("file:///Users/marc/Desktop/todo.txt").category == "personal"
    assert bindings.resolve("imap://inbox/message/123").category == "email"
    assert bindings.resolve("applemail://inbox/message/123").category == "email"
    assert bindings.resolve("outlook://accounts").category == "email"
    assert bindings.resolve("pages://frontmost").category == "personal"
    assert bindings.resolve("word://frontmost").category == "personal"
    assert bindings.resolve("numbers://frontmost").category == "personal"
    assert bindings.resolve("keynote://frontmost").category == "work"
    assert bindings.resolve("powerpoint://frontmost").category == "work"
    assert bindings.resolve("macos://clipboard").category == "personal"
    assert bindings.resolve("macos://app/com.apple.mail").category == "personal"
    assert bindings.resolve("macos://notification").category == "scratch"
    assert bindings.resolve("browser://active/page").category == "public"
    assert bindings.resolve("screen://current-window").category == "personal"


def test_personal_assistant_relationship_groups_support_low_friction_workflows() -> None:
    groups = load_relationship_groups(_PRESET / "relationship_groups.yaml")

    assert groups.is_member("me@example.com", "self")
    assert groups.is_member("me@example.com", "trusted-draft")
    assert groups.is_member("spouse@example.com", "family")
    assert groups.is_member("coworker@example.com", "work-team")
