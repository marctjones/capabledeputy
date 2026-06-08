"""Usability slice U1a — the SHIPPED default labeling oracle.

A fresh deployment must auto-label genuinely-sensitive reads (so the engine
gates the right things) WITHOUT operator config, while keeping benign reads
unlabeled (so they aren't over-gated — the anti-fatigue property). And the
rules must be raise-only: a rule can only over-classify, never under.

These pin the shipped configs/fs_label_rules.yaml + email_label_rules.yaml as
ACTIVE defaults (not .example stubs).
"""

from __future__ import annotations

from pathlib import Path

from capabledeputy.policy.email_labeling import load_email_label_rules
from capabledeputy.policy.fs_labeling import load_fs_label_rules
from capabledeputy.policy.tiers import Tier

_CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def _fs():
    return load_fs_label_rules(_CONFIGS / "fs_label_rules.yaml")


def _email():
    return load_email_label_rules(_CONFIGS / "email_label_rules.yaml")


# --- the default is ACTIVE, not a stub ------------------------------


def test_shipped_fs_and_email_rules_are_active() -> None:
    assert _fs().rules, "configs/fs_label_rules.yaml must ship ACTIVE (non-empty)"
    assert _email().rules, "configs/email_label_rules.yaml must ship ACTIVE (non-empty)"


# --- sensitive reads auto-label with NO operator config -------------


def test_financial_path_auto_labels_restricted() -> None:
    st = _fs().labels_for("~/Documents/Financial/statement.pdf")
    fin = next((c for c in st.a if c.category == "financial"), None)
    assert fin is not None
    assert fin.tier == Tier.RESTRICTED  # financial resolves restricted (labels.yaml)


def test_health_path_auto_labels() -> None:
    st = _fs().labels_for("~/Documents/Medical/labs.pdf")
    assert "health" in {c.category for c in st.a}


def test_credential_glob_labels_high() -> None:
    st = _fs().labels_for("/home/me/deploy/id_rsa")
    cred = next((c for c in st.a if c.category == "credentials"), None)
    assert cred is not None


def test_financial_email_sender_auto_labels() -> None:
    st = _email().labels_for({"from": "alerts@chase.com", "subject": "hello", "body": ""})
    assert "financial" in {c.category for c in st.a}


# --- the anti-fatigue property: benign reads stay UNLABELED ---------


def test_benign_file_stays_unlabeled() -> None:
    st = _fs().labels_for("~/notes/grocery-list.txt")
    assert not st.a, "benign read must not be over-labeled (would cause fatigue)"
    assert not st.b


def test_benign_email_not_raised() -> None:
    st = _email().labels_for(
        {"from": "friend@gmail.example", "subject": "lunch?", "body": "tacos at noon"},
    )
    assert not st.a, "benign mail must not be raised (would cause fatigue)"


# --- raise-only: a rule can only OVER-classify, never under ----------


def test_unknown_category_over_classifies_never_under() -> None:
    """`credentials` is not declared in labels.yaml; the resolver must map an
    unknown category to a HIGH tier (raise-only safety: a typo can only
    over-classify, never silently under-classify)."""
    st = _fs().labels_for("/x/vault.kdbx")
    cred = next((c for c in st.a if c.category == "credentials"), None)
    assert cred is not None
    assert cred.tier in (Tier.RESTRICTED, Tier.PROHIBITED), cred.tier
