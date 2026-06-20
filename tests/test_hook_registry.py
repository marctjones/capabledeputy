"""Tests for HookRegistry — spec 004 P0 named hooks."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from capabledeputy.substrate.hook_registry import (
    STANDARD_HOOKS,
    HookError,
    HookRegistry,
    build_registry_from_policy_context,
)


@dataclass
class _Fake:
    name: str = "fake"


def test_empty_registry_returns_empty_tuple() -> None:
    reg = HookRegistry()
    assert reg.get("at_chokepoint.decision") == ()
    assert reg.is_empty()
    assert reg.total_primitives() == 0


def test_register_at_standard_hook() -> None:
    reg = HookRegistry()
    p = _Fake("inspector-1")
    reg.register("at_chokepoint.decision", p)
    assert reg.get("at_chokepoint.decision") == (p,)


def test_register_preserves_order() -> None:
    reg = HookRegistry()
    a = _Fake("a")
    b = _Fake("b")
    c = _Fake("c")
    reg.register("at_chokepoint.decision", a)
    reg.register("at_chokepoint.decision", b)
    reg.register("at_chokepoint.decision", c)
    assert reg.get("at_chokepoint.decision") == (a, b, c)


def test_register_unknown_hook_refused() -> None:
    reg = HookRegistry()
    with pytest.raises(HookError, match="unknown hook"):
        reg.register("not_a_real_hook", _Fake())


def test_register_unknown_hook_error_lists_valid_options() -> None:
    reg = HookRegistry()
    with pytest.raises(HookError) as exc_info:
        reg.register("nope", _Fake())
    # The error message includes the valid hook names so operators
    # see what they could have used.
    assert "at_chokepoint.decision" in str(exc_info.value)


def test_get_unknown_hook_returns_empty() -> None:
    """Reads are permissive — chokepoint code shouldn't have to
    know which hooks the operator populated."""
    reg = HookRegistry()
    assert reg.get("at_chokepoint.decision") == ()
    # Even truly unknown names read empty
    assert reg.get("does-not-exist") == ()


def test_all_registered_hooks_sorted() -> None:
    reg = HookRegistry()
    reg.register("at_session.terminate", _Fake())
    reg.register("at_ingest.value_in", _Fake())
    reg.register("at_chokepoint.decision", _Fake())
    assert reg.all_registered_hooks() == (
        "at_chokepoint.decision",
        "at_ingest.value_in",
        "at_session.terminate",
    )


def test_total_primitives_across_hooks() -> None:
    reg = HookRegistry()
    reg.register("at_chokepoint.decision", _Fake())
    reg.register("at_chokepoint.decision", _Fake())
    reg.register("at_ingest.value_in", _Fake())
    assert reg.total_primitives() == 3
    assert not reg.is_empty()


def test_entries_are_sorted_and_preserve_primitive_order() -> None:
    reg = HookRegistry()
    a = _Fake("a")
    b = _Fake("b")
    c = _Fake("c")
    reg.register("at_session.terminate", c)
    reg.register("at_chokepoint.decision", a)
    reg.register("at_chokepoint.decision", b)
    assert reg.entries() == (
        ("at_chokepoint.decision", (a, b)),
        ("at_session.terminate", (c,)),
    )


def test_extend_appends_other_registry() -> None:
    base = HookRegistry()
    extra = HookRegistry()
    a = _Fake("a")
    b = _Fake("b")
    base.register("at_chokepoint.decision", a)
    extra.register("at_chokepoint.decision", b)
    base.extend(extra)
    assert base.get("at_chokepoint.decision") == (a, b)


def test_standard_hooks_includes_lifecycle_points() -> None:
    """Sanity-check the standard hook taxonomy covers the lifecycle."""
    assert "at_chokepoint.decision" in STANDARD_HOOKS
    assert "at_ingest.value_in" in STANDARD_HOOKS
    assert "at_ingest.declassifier_chain" in STANDARD_HOOKS
    assert "at_dispatch.pre_dispatch" in STANDARD_HOOKS
    assert "at_dispatch.post_dispatch" in STANDARD_HOOKS
    assert "at_session.spawn" in STANDARD_HOOKS


# ---------- bridge from PolicyContext ----------


def test_bridge_from_empty_policy_context() -> None:
    @dataclass
    class _EmptyPC:
        inspectors: tuple = ()
        declassifiers: tuple = ()
        decision_inspectors: tuple = ()

    reg = build_registry_from_policy_context(_EmptyPC())
    assert reg.is_empty()


def test_bridge_populates_three_hooks() -> None:
    @dataclass
    class _PC:
        inspectors: tuple
        declassifiers: tuple
        decision_inspectors: tuple

    insp = _Fake("inspector")
    dec = _Fake("decision_inspector")
    declass = _Fake("declassifier")
    pc = _PC(
        inspectors=(insp,),
        declassifiers=(declass,),
        decision_inspectors=(dec,),
    )
    reg = build_registry_from_policy_context(pc)
    assert reg.get("at_ingest.value_in") == (insp,)
    assert reg.get("at_ingest.declassifier_chain") == (declass,)
    assert reg.get("at_chokepoint.decision") == (dec,)
    assert reg.total_primitives() == 3


def test_bridge_merges_explicit_hook_registry_after_tuple_hooks() -> None:
    @dataclass
    class _PC:
        inspectors: tuple = ()
        declassifiers: tuple = ()
        decision_inspectors: tuple = ()
        hook_registry: HookRegistry | None = None

    tuple_hook = _Fake("tuple")
    explicit_hook = _Fake("explicit")
    hooks = HookRegistry()
    hooks.register("at_chokepoint.decision", explicit_hook)
    reg = build_registry_from_policy_context(
        _PC(decision_inspectors=(tuple_hook,), hook_registry=hooks)
    )
    assert reg.get("at_chokepoint.decision") == (tuple_hook, explicit_hook)


def test_bridge_handles_none_attributes() -> None:
    """A PolicyContext without one of the fields (None) doesn't crash."""

    @dataclass
    class _PartialPC:
        inspectors: tuple = ()
        # no declassifiers or decision_inspectors

    reg = build_registry_from_policy_context(_PartialPC())
    assert reg.get("at_ingest.value_in") == ()
    assert reg.get("at_ingest.declassifier_chain") == ()
