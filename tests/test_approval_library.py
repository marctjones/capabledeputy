"""Approval pattern library: load YAML, validate, apply.

Each library entry is validated through the same `ApprovalPatternRule.create`
checks as a CLI-created pattern; a single bad entry rejects the whole
file rather than partially loading.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from capabledeputy.approval.library import (
    LibraryEntry,
    PatternLibraryError,
    apply_library,
    load_library_file,
    parse_library,
)
from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.pattern import ApprovalPatternRegistry


def test_parse_library_basic() -> None:
    raw = {
        "patterns": [
            {
                "name": "spouse-emails",
                "action": "SEND_EMAIL",
                "target_pattern": "spouse@example.com",
                "ttl_hours": 24,
            },
        ],
    }
    entries = parse_library(raw)
    assert len(entries) == 1
    e = entries[0]
    assert e.action == ApprovalAction.SEND_EMAIL
    assert e.target_pattern == "spouse@example.com"
    assert e.ttl_hours == 24


def test_parse_library_missing_name_errors() -> None:
    raw = {"patterns": [{"action": "SEND_EMAIL", "target_pattern": "x", "ttl_hours": 1}]}
    with pytest.raises(PatternLibraryError, match="name"):
        parse_library(raw)


def test_parse_library_unknown_action_errors() -> None:
    raw = {
        "patterns": [
            {"name": "x", "action": "NOT_REAL", "target_pattern": "y", "ttl_hours": 1},
        ],
    }
    with pytest.raises(PatternLibraryError, match="action"):
        parse_library(raw)


def test_parse_library_duplicate_name_errors() -> None:
    raw = {
        "patterns": [
            {"name": "dup", "action": "SEND_EMAIL", "target_pattern": "a@b.com", "ttl_hours": 1},
            {"name": "dup", "action": "SEND_EMAIL", "target_pattern": "c@d.com", "ttl_hours": 1},
        ],
    }
    with pytest.raises(PatternLibraryError, match="duplicate"):
        parse_library(raw)


def test_apply_library_registers_each_entry() -> None:
    entries = [
        LibraryEntry(
            name="spouse",
            action=ApprovalAction.SEND_EMAIL,
            target_pattern="spouse@example.com",
            payload_pattern="*prescription*",
            ttl_hours=24,
        ),
    ]
    registry = ApprovalPatternRegistry()
    rules = apply_library(entries, registry)
    assert len(rules) == 1
    assert len(registry.list()) == 1


def test_apply_library_propagates_validation_errors() -> None:
    """A library entry that fails the existing pattern footgun guard
    (bare `*`) rejects the whole library — partial loads are unsafe."""
    entries = [
        LibraryEntry(
            name="bad",
            action=ApprovalAction.SEND_EMAIL,
            target_pattern="*",
            payload_pattern=None,
            ttl_hours=24,
        ),
    ]
    registry = ApprovalPatternRegistry()
    with pytest.raises(PatternLibraryError, match="bad"):
        apply_library(entries, registry)
    assert len(registry.list()) == 0


def test_load_library_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "lib.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            patterns:
              - name: spouse-emails
                action: SEND_EMAIL
                target_pattern: "spouse@example.com"
                payload_pattern: "*prescription*"
                ttl_hours: 24
              - name: groceries
                action: QUEUE_PURCHASE
                target_pattern: "amazon-fresh"
                ttl_hours: 168
            """,
        ),
    )
    entries = load_library_file(path)
    assert len(entries) == 2
    assert entries[0].name == "spouse-emails"
    assert entries[1].action == ApprovalAction.QUEUE_PURCHASE


def test_starter_library_loads_and_applies() -> None:
    """The shipped configs/approval-patterns.yaml must parse and apply
    cleanly — it's the entry point every new user will reference."""
    path = Path(__file__).parent.parent / "configs" / "approval-patterns.yaml"
    if not path.is_file():
        pytest.skip("starter library not in repo")
    entries = load_library_file(path)
    assert len(entries) >= 1
    registry = ApprovalPatternRegistry()
    rules = apply_library(entries, registry)
    assert len(rules) == len(entries)
