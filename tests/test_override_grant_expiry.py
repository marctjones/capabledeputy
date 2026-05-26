"""Tests for Override Grant expiry default + cap (T122-T123, FR-032, Q2).

The spec clarification on 2026-05-25 (Q2) locked in:
- default expiry: 15 minutes (900s) when an entry doesn't declare one
- absolute hard cap: 60 minutes (3600s); the validator refuses
  OverridePolicyEntry objects with `expiry_seconds > 3600` at
  authoring / load time so misconfiguration can't yield an
  all-day bypass
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.overrides import (
    OVERRIDE_EXPIRY_DEFAULT_SECONDS,
    OVERRIDE_EXPIRY_MAX_SECONDS,
    HardFloor,
    OverridePolicy,
    OverridePolicyEntry,
    OverridePolicyValidationError,
)


def test_default_expiry_is_15_minutes() -> None:
    """An entry constructed without an explicit expiry uses the
    spec's 15-minute default (Q2)."""
    entry = OverridePolicyEntry(
        floor=HardFloor.PROHIBITED,
        policy=OverridePolicy.SINGLE_AUTHORIZED,
        authorized_principal_ids=frozenset({"alice"}),
    )
    assert entry.expiry_seconds == 900
    assert entry.expiry_seconds == OVERRIDE_EXPIRY_DEFAULT_SECONDS


def test_explicit_expiry_within_cap_honored() -> None:
    """A configured value at-or-below the cap is honored verbatim."""
    entry = OverridePolicyEntry(
        floor=HardFloor.PROHIBITED,
        policy=OverridePolicy.SINGLE_AUTHORIZED,
        authorized_principal_ids=frozenset({"alice"}),
        expiry_seconds=600,  # 10 min
    )
    assert entry.expiry_seconds == 600


def test_explicit_expiry_at_cap_honored() -> None:
    """Exactly 3600s (the cap) is allowed — only > 3600s is refused."""
    entry = OverridePolicyEntry(
        floor=HardFloor.PROHIBITED,
        policy=OverridePolicy.SINGLE_AUTHORIZED,
        authorized_principal_ids=frozenset({"alice"}),
        expiry_seconds=OVERRIDE_EXPIRY_MAX_SECONDS,
    )
    assert entry.expiry_seconds == 3600


def test_expiry_above_cap_refused() -> None:
    """Configuring expiry > 3600s raises at construction time, with a
    clear FR-032 reference in the error message so the operator knows
    the spec-imposed limit isn't a typo."""
    with pytest.raises(OverridePolicyValidationError, match="FR-032"):
        OverridePolicyEntry(
            floor=HardFloor.PROHIBITED,
            policy=OverridePolicy.SINGLE_AUTHORIZED,
            authorized_principal_ids=frozenset({"alice"}),
            expiry_seconds=3601,
        )


def test_expiry_one_hour_thirty_min_refused() -> None:
    """5400s (90 min) is well above the cap; must be refused."""
    with pytest.raises(OverridePolicyValidationError) as excinfo:
        OverridePolicyEntry(
            floor=HardFloor.PROHIBITED,
            policy=OverridePolicy.SINGLE_AUTHORIZED,
            authorized_principal_ids=frozenset({"alice"}),
            expiry_seconds=5400,
        )
    assert "3600" in str(excinfo.value)
    assert "60 min" in str(excinfo.value)


def test_expiry_zero_refused() -> None:
    """An entry with expiry_seconds=0 would yield a grant that's
    already expired; refused as a misconfiguration."""
    with pytest.raises(OverridePolicyValidationError, match="positive"):
        OverridePolicyEntry(
            floor=HardFloor.INTEGRITY_FLOOR,
            policy=OverridePolicy.SINGLE_AUTHORIZED,
            authorized_principal_ids=frozenset({"alice"}),
            expiry_seconds=0,
        )


def test_expiry_negative_refused() -> None:
    with pytest.raises(OverridePolicyValidationError, match="positive"):
        OverridePolicyEntry(
            floor=HardFloor.INTEGRITY_FLOOR,
            policy=OverridePolicy.SINGLE_AUTHORIZED,
            authorized_principal_ids=frozenset({"alice"}),
            expiry_seconds=-1,
        )


def test_yaml_loader_uses_default_when_unset() -> None:
    """When YAML config omits `expiry_seconds`, the loader (`overrides.load`)
    supplies the OVERRIDE_EXPIRY_DEFAULT_SECONDS constant."""
    import tempfile
    from pathlib import Path

    from capabledeputy.policy.overrides import load as load_override_policies

    yaml_text = """
policies:
  - tier_or_floor: prohibited
    policy: single-authorized
    authorized_principal_ids:
      - alice
"""
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        f.write(yaml_text)
        path = Path(f.name)

    policies = load_override_policies(path)
    entry = policies.get(HardFloor.PROHIBITED)
    assert entry is not None
    assert entry.expiry_seconds == OVERRIDE_EXPIRY_DEFAULT_SECONDS
    assert entry.expiry_seconds == 900
