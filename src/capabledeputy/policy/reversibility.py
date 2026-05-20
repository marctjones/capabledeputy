"""Reversibility and Mutability labels (003 US6 / FR-037 / FR-039 / FR-044).

A `ReversibilityLabel(degree, agent)` describes how reversible an
effect is and *who* would have to act to reverse it. Composition
across (effect-default x target x channel) is most-restrictive
(FR-037). The label sits on:
  - the ToolDefinition (`default_reversibility`),
  - the target/binding (via SourceLocationLabelBinding),
  - the channel (e.g., `EXECUTE.sandbox` containment lifts to
    `reversible/system`, FR-040).

`MutabilityLabel` is analogous: how much a target may be mutated.
Composing a write into an immutable / append-only target lifts the
*reversibility* of the write to `irreversible` regardless of what
the effect default claimed (FR-039 / SC-016 — create/append cannot
be undone in-place).

Write-discipline (FR-044): only a verified version-preserving write
(prior-version handle present + post-state hash matches the
attestation) earns `reversible/system`. Unverified or in-place ⇒
`irreversible`. The verifier here is pure — the actual port read
lives in T083's caller code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class ReversibilityError(RuntimeError):
    """Reversibility/mutability config or composition violation;
    fail-closed per Principle VI."""


class ReversibilityDegree(StrEnum):
    """How easily the effect can be undone.
    reversible > reversible-with-friction > irreversible
    (decreasing ease of undo)."""

    REVERSIBLE = "reversible"
    REVERSIBLE_WITH_FRICTION = "reversible-with-friction"
    IRREVERSIBLE = "irreversible"


class ReversalAgent(StrEnum):
    """Who would have to act to reverse the effect.
    system > human > external (decreasing autonomy of the platform).
    `system` means the platform itself can undo it; `human` means a
    person (the principal or operator) must take action; `external`
    means a third party must cooperate (and may refuse)."""

    SYSTEM = "system"
    HUMAN = "human"
    EXTERNAL = "external"


class MutabilityDegree(StrEnum):
    """How much the target accepts in-place change.
    immutable > append-only > in-place
    (decreasing strictness)."""

    IMMUTABLE = "immutable"
    APPEND_ONLY = "append-only"
    IN_PLACE = "in-place"


# Rank tables for most-restrictive composition. Lower rank = MORE
# restrictive (worse for the actor). Most-restrictive wins, per
# FR-037 / FR-039 — never relax during composition.
_REVERSIBILITY_RANK: dict[ReversibilityDegree, int] = {
    ReversibilityDegree.IRREVERSIBLE: 0,
    ReversibilityDegree.REVERSIBLE_WITH_FRICTION: 1,
    ReversibilityDegree.REVERSIBLE: 2,
}
_AGENT_RANK: dict[ReversalAgent, int] = {
    ReversalAgent.EXTERNAL: 0,
    ReversalAgent.HUMAN: 1,
    ReversalAgent.SYSTEM: 2,
}
_MUTABILITY_RANK: dict[MutabilityDegree, int] = {
    MutabilityDegree.IMMUTABLE: 0,
    MutabilityDegree.APPEND_ONLY: 1,
    MutabilityDegree.IN_PLACE: 2,
}


@dataclass(frozen=True)
class ReversibilityLabel:
    """(degree, agent) pair attached to an effect / target / channel."""

    degree: ReversibilityDegree
    agent: ReversalAgent

    def to_dict(self) -> dict[str, str]:
        return {"degree": self.degree.value, "agent": self.agent.value}

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> ReversibilityLabel:
        return cls(
            degree=ReversibilityDegree(d["degree"]),
            agent=ReversalAgent(d["agent"]),
        )


@dataclass(frozen=True)
class MutabilityLabel:
    """(degree, agent) pair describing how the target tolerates
    in-place change. `agent` here is the actor that would change
    the target's mutability (e.g., a human admin can change an
    append-only log to in-place; the system cannot)."""

    degree: MutabilityDegree
    agent: ReversalAgent

    def to_dict(self) -> dict[str, str]:
        return {"degree": self.degree.value, "agent": self.agent.value}

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> MutabilityLabel:
        return cls(
            degree=MutabilityDegree(d["degree"]),
            agent=ReversalAgent(d["agent"]),
        )


def compose_reversibility(*labels: ReversibilityLabel) -> ReversibilityLabel:
    """Most-restrictive composition across labels. Fail-closed on
    empty input — callers MUST supply at least one (FR-037)."""
    if not labels:
        raise ReversibilityError(
            "compose_reversibility() requires at least one ReversibilityLabel",
        )
    worst_degree = min(labels, key=lambda lbl: _REVERSIBILITY_RANK[lbl.degree]).degree
    worst_agent = min(labels, key=lambda lbl: _AGENT_RANK[lbl.agent]).agent
    return ReversibilityLabel(degree=worst_degree, agent=worst_agent)


def compose_mutability(*labels: MutabilityLabel) -> MutabilityLabel:
    """Most-restrictive composition (FR-039). The agent collapses to
    the worst-case agent across inputs."""
    if not labels:
        raise ReversibilityError(
            "compose_mutability() requires at least one MutabilityLabel",
        )
    worst_degree = min(labels, key=lambda lbl: _MUTABILITY_RANK[lbl.degree]).degree
    worst_agent = min(labels, key=lambda lbl: _AGENT_RANK[lbl.agent]).agent
    return MutabilityLabel(degree=worst_degree, agent=worst_agent)


def reversibility_for_write_into(
    effect_default: ReversibilityLabel,
    target_mutability: MutabilityLabel,
) -> ReversibilityLabel:
    """Compose the effective reversibility of a write effect against
    the target's mutability label (FR-039 / SC-016).

    Key invariant: a write into an immutable or append-only target
    composes to `irreversible` regardless of the effect's claimed
    default — once written, you cannot un-create or un-append in
    place. The verifier in `verify_write_discipline` is the only
    path that can earn `reversible/system` for a write (FR-044).
    """
    if target_mutability.degree in (MutabilityDegree.IMMUTABLE, MutabilityDegree.APPEND_ONLY):
        return ReversibilityLabel(
            degree=ReversibilityDegree.IRREVERSIBLE,
            agent=compose_mutability(target_mutability, target_mutability).agent,
        )
    return effect_default


@dataclass(frozen=True)
class WriteResult:
    """Minimal shape returned by VersionedWritePort.write — the inputs
    the verifier needs. Real port impl lands in spec 004; this is the
    in-TCB record. `prior_version_handle` is the locator the port
    surfaces for the pre-write state; `post_state_hash` is the hash
    of the destination immediately after the write; `attestation` is
    the port's signed confirmation that the prior version is
    retrievable for the retention window declared by the operator
    (FR-044)."""

    prior_version_handle: str | None
    post_state_hash: str
    attestation: str


def verify_write_discipline(
    result: WriteResult,
    *,
    observed_prior_hash: str | None,
    expected_pre_state_hash: str | None,
) -> ReversibilityLabel:
    """T083 — write-discipline verification (FR-044 / SC-015).

    Returns `reversible/system` ONLY when:
      - the port surfaced a prior_version_handle,
      - the caller successfully read that handle (observed_prior_hash
        is not None),
      - the observed prior hash matches the expected pre-write hash,
      - an attestation was returned.

    Any other combination ⇒ `irreversible/external`. This is the
    fail-closed default — an unverifiable write earns no autonomy
    boost. Pure function; the actual port read happens at the
    caller (the substrate/version_write_port adapter, T075).
    """
    if (
        result.prior_version_handle is None
        or observed_prior_hash is None
        or expected_pre_state_hash is None
        or observed_prior_hash != expected_pre_state_hash
        or not result.attestation
    ):
        return ReversibilityLabel(
            degree=ReversibilityDegree.IRREVERSIBLE,
            agent=ReversalAgent.EXTERNAL,
        )
    return ReversibilityLabel(
        degree=ReversibilityDegree.REVERSIBLE,
        agent=ReversalAgent.SYSTEM,
    )


# --- YAML loader ----------------------------------------------------


@dataclass(frozen=True)
class ReversibilityRegistry:
    """Operator-declared label catalogue (id → ReversibilityLabel)."""

    by_id: dict[str, ReversibilityLabel] = field(default_factory=dict)

    def get(self, label_id: str) -> ReversibilityLabel | None:
        return self.by_id.get(label_id)


@dataclass(frozen=True)
class MutabilityRegistry:
    by_id: dict[str, MutabilityLabel] = field(default_factory=dict)

    def get(self, label_id: str) -> MutabilityLabel | None:
        return self.by_id.get(label_id)


def load_registries(
    path: Path,
) -> tuple[ReversibilityRegistry, MutabilityRegistry]:
    """Load reversibility_labels + mutability_labels from labels.yaml.
    Fail-closed on missing file or unparseable YAML."""
    if not path.is_file():
        raise ReversibilityError(f"labels config missing: {path}")
    try:
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ReversibilityError(f"unparseable: {path} — {e}") from e

    rev_raw = data.get("reversibility_labels") or []
    mut_raw = data.get("mutability_labels") or []
    if not isinstance(rev_raw, list) or not isinstance(mut_raw, list):
        raise ReversibilityError(
            f"reversibility_labels and mutability_labels must be lists: {path}",
        )
    rev: dict[str, ReversibilityLabel] = {}
    for i, item in enumerate(rev_raw):
        if not isinstance(item, dict):
            raise ReversibilityError(f"reversibility_labels[{i}] is not an object")
        try:
            lid = str(item["id"])
            rev[lid] = ReversibilityLabel(
                degree=ReversibilityDegree(str(item["degree"])),
                agent=ReversalAgent(str(item["agent"])),
            )
        except (KeyError, ValueError) as e:
            raise ReversibilityError(f"reversibility_labels[{i}]: {e}") from e
    mut: dict[str, MutabilityLabel] = {}
    for i, item in enumerate(mut_raw):
        if not isinstance(item, dict):
            raise ReversibilityError(f"mutability_labels[{i}] is not an object")
        try:
            lid = str(item["id"])
            mut[lid] = MutabilityLabel(
                degree=MutabilityDegree(str(item["degree"])),
                agent=ReversalAgent(str(item["agent"])),
            )
        except (KeyError, ValueError) as e:
            raise ReversibilityError(f"mutability_labels[{i}]: {e}") from e
    return ReversibilityRegistry(by_id=rev), MutabilityRegistry(by_id=mut)
