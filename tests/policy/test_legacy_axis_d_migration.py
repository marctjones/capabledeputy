"""T047 — Axis-D derivation from legacy trust prefix at v5→v6 migration.

The v0.7 `label_set` carries a trust prefix (`trusted.*` /
`untrusted.*`) but no first-class initiator or authentication
record. At v5→v6, the converter derives a conservative Axis-D so
post-migration sessions have a populated `axis_d` without claiming
information we never recorded.

Rules (FR-024 forward-only, most-restrictive):
  * `untrusted.*` present (with or without trusted) ⇒ external
    initiator, authentication=none. Worst-case wins.
  * Only `trusted.user_direct` ⇒ principal initiator,
    authentication=device-bound.
  * No trust label ⇒ axis_d stays default (`{}`).
"""

from __future__ import annotations

import json

from capabledeputy.session.store import _convert_legacy_label_set


def test_untrusted_label_yields_external_axis_d() -> None:
    _axis_a_json, axis_b_json, axis_d_json = _convert_legacy_label_set(
        json.dumps(["untrusted.external"]),
    )
    axis_d = json.loads(axis_d_json)
    assert axis_d["initiator"] == "external:legacy-untrusted"
    assert axis_d["authentication"] == "none"
    assert axis_d["expectedness"] == "anomalous"
    assert axis_d["reversibility"] == {"degree": "irreversible", "agent": "external"}
    # axis_b sanity:
    assert any(e["level"] == "external-untrusted" for e in json.loads(axis_b_json))


def test_untrusted_user_input_also_external() -> None:
    _, _, axis_d_json = _convert_legacy_label_set(
        json.dumps(["untrusted.user_input"]),
    )
    axis_d = json.loads(axis_d_json)
    assert axis_d["initiator"] == "external:legacy-untrusted"
    assert axis_d["authentication"] == "none"


def test_trusted_only_yields_principal_axis_d() -> None:
    _, _, axis_d_json = _convert_legacy_label_set(
        json.dumps(["trusted.user_direct"]),
    )
    axis_d = json.loads(axis_d_json)
    assert axis_d["initiator"] == "principal:legacy-migration"
    assert axis_d["authentication"] == "device-bound"
    assert axis_d["expectedness"] == "anomalous"


def test_untrusted_dominates_trusted_most_restrictive() -> None:
    """Both labels present ⇒ untrusted wins (FR-024 most-restrictive)."""
    _, _, axis_d_json = _convert_legacy_label_set(
        json.dumps(["trusted.user_direct", "untrusted.external"]),
    )
    axis_d = json.loads(axis_d_json)
    assert axis_d["initiator"] == "external:legacy-untrusted"
    assert axis_d["authentication"] == "none"


def test_no_trust_label_yields_empty_axis_d() -> None:
    """A label_set with only confidential.* or egress.* legacy values
    does not produce an Axis-D — we have no signal to derive from."""
    _, _, axis_d_json = _convert_legacy_label_set(
        json.dumps(["confidential.health"]),
    )
    assert axis_d_json == "{}"


def test_empty_label_set_yields_empty_axis_d() -> None:
    _, _, axis_d_json = _convert_legacy_label_set("[]")
    assert axis_d_json == "{}"


def test_malformed_json_fails_closed() -> None:
    """Unparseable input ⇒ default ('[]','[]','{}') — never crash."""
    axis_a_json, axis_b_json, axis_d_json = _convert_legacy_label_set("not-json")
    assert axis_a_json == "[]"
    assert axis_b_json == "[]"
    assert axis_d_json == "{}"


def test_converter_is_idempotent_on_axis_d() -> None:
    """Running the converter twice on the same input gives the same
    JSON output (string-level idempotence) — needed because the
    migration query may re-encounter rows."""
    label_set_json = json.dumps(["untrusted.external", "confidential.health"])
    first = _convert_legacy_label_set(label_set_json)
    second = _convert_legacy_label_set(label_set_json)
    assert first == second
